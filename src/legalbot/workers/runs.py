"""Agent-run orchestration tasks.

- run_pipeline(job_id) — primary pipeline entry: dispatched -> processing -> completed
- continue_session(run_id) — user (or schedule) follow-up invocation
- resume_run(run_id, resume_payload) — resumes an interrupted graph with Command(resume=...)
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from legalbot.core.logging import get_logger
from legalbot.db.models import IngestionAttachment, IngestionItem, ProcessingJob, Run
from legalbot.db.models import Session as SessionRow
from legalbot.db.session import async_session_factory
from legalbot.db.types import (
    JobState,
    RunKind,
    RunStatus,
    SessionStatus,
)
from legalbot.jobs.models import InvalidJobTransition
from legalbot.jobs.service import JobService
from legalbot.services.run import RunService
from legalbot.services.session import SessionService
from legalbot.workers._async import run_async
from legalbot.workers.celery_app import celery_app

log = get_logger(__name__)


@celery_app.task(name="legalbot.workers.runs.run_pipeline", bind=True)
def run_pipeline(self, job_id: str) -> dict[str, Any]:
    return run_async(_run_pipeline(job_id))


async def _run_pipeline(job_id: str) -> dict[str, Any]:
    job_uuid = uuid.UUID(job_id)
    sm = async_session_factory()
    async with sm() as db:
        job_svc = JobService(db)
        job = (
            await db.execute(select(ProcessingJob).where(ProcessingJob.id == job_uuid))
        ).scalar_one()

        ok = await job_svc.transition(
            job.id, from_state=JobState.dispatched, to_state=JobState.processing
        )
        if not ok:
            log.warning("pipeline.transition_skipped", job_id=str(job.id), state=job.state)
            return {"ok": False, "reason": "not in dispatched"}

        session_svc = SessionService(db)
        session = await session_svc.create_primary(job)
        await job_svc.set_current_session(job.id, session.id)

        # Determine upfront whether the email has attachments so subagents
        # can branch without a redundant DB round-trip.
        has_attachments = await _ingestion_item_has_attachments(db, job.ingestion_item_id)

        run_svc = RunService(db)
        run = await run_svc.start(
            session_id=session.id,
            job_id=job.id,
            kind=RunKind.pipeline,
            trigger="ingestion",
            trigger_payload={"job_id": str(job.id)},
        )
        await db.commit()

    try:
        graph_ok = await _invoke_graph(
            run_id=run.id,
            session_id=session.id,
            resume_payload=None,
            has_attachments=has_attachments,
        )
    finally:
        await _release_concurrency_budget(job.id)

    if not graph_ok:
        return {
            "ok": False,
            "job_id": job_id,
            "run_id": str(run.id),
            "session_id": str(session.id),
            "reason": "graph_failed",
        }
    return {"ok": True, "job_id": job_id, "run_id": str(run.id), "session_id": str(session.id)}


@celery_app.task(name="legalbot.workers.runs.continue_session", bind=True)
def continue_session(self, run_id: str) -> dict[str, Any]:
    return run_async(_continue_session(run_id))


async def _continue_session(run_id: str) -> dict[str, Any]:
    run_uuid = uuid.UUID(run_id)
    sm = async_session_factory()
    async with sm() as db:
        run = (await db.execute(select(Run).where(Run.id == run_uuid))).scalar_one()
        session = (
            await db.execute(select(SessionRow).where(SessionRow.id == run.session_id))
        ).scalar_one()
    graph_ok = await _invoke_graph(run_id=run.id, session_id=session.id, resume_payload=None)
    return {"ok": graph_ok, "run_id": run_id}


@celery_app.task(name="legalbot.workers.runs.resume_run", bind=True)
def resume_run(self, run_id: str, resume_payload: Any = None) -> dict[str, Any]:
    return run_async(_resume_run(run_id, resume_payload))


async def _resume_run(run_id: str, resume_payload: Any) -> dict[str, Any]:
    run_uuid = uuid.UUID(run_id)
    sm = async_session_factory()
    async with sm() as db:
        run = (await db.execute(select(Run).where(Run.id == run_uuid))).scalar_one()
        session = (
            await db.execute(select(SessionRow).where(SessionRow.id == run.session_id))
        ).scalar_one()
        has_attachments = await _run_has_attachments(db, run)
        if run.kind == RunKind.pipeline:
            job_svc = JobService(db)
            try:
                await job_svc.transition(
                    session.job_id,
                    from_state=JobState.awaiting_human,
                    to_state=JobState.processing,
                )
            except InvalidJobTransition as inv:
                log.warning(
                    "resume.job_transition_skipped",
                    job_id=str(session.job_id),
                    run_id=str(run.id),
                    error=str(inv),
                )
            await db.commit()

    graph_ok = await _invoke_graph(
        run_id=run.id,
        session_id=session.id,
        resume_payload=resume_payload,
        has_attachments=has_attachments,
    )
    return {"ok": graph_ok, "run_id": run_id}


async def _run_has_attachments(db: AsyncSession, run: Run) -> bool:
    job = (
        await db.execute(select(ProcessingJob).where(ProcessingJob.id == run.job_id))
    ).scalar_one()
    return await _ingestion_item_has_attachments(db, job.ingestion_item_id)


async def _ingestion_item_has_attachments(db: AsyncSession, ingestion_item_id: uuid.UUID) -> bool:
    return (
        await db.execute(
            select(IngestionAttachment.id)
            .join(IngestionItem, IngestionAttachment.ingestion_item_id == IngestionItem.id)
            .where(IngestionItem.id == ingestion_item_id)
            .limit(1)
        )
    ).first() is not None


async def _fail_pipeline_job_for_graph_error(
    session_id: uuid.UUID, run_id: uuid.UUID, exc: BaseException
) -> None:
    """Move primary pipeline jobs out of ``processing`` when the graph aborts."""
    err = str(exc)[:12_000]
    sm = async_session_factory()
    async with sm() as db:
        run = (await db.execute(select(Run).where(Run.id == run_id))).scalar_one()
        if run.kind != str(RunKind.pipeline):
            return
        job_svc = JobService(db)
        try:
            await job_svc.transition(
                run.job_id,
                from_state=JobState.processing,
                to_state=JobState.failed,
                extra={"last_error": err},
            )
        except InvalidJobTransition as inv:
            log.warning(
                "pipeline.job_fail_skipped",
                job_id=str(run.job_id),
                run_id=str(run_id),
                error=str(inv),
            )
            return
        await db.commit()


async def _invoke_graph(
    *,
    run_id: uuid.UUID,
    session_id: uuid.UUID,
    resume_payload: Any,
    has_attachments: bool | None = None,
) -> bool:
    """Invoke the compiled LangGraph agent with this session's thread and state.

    Returns ``True`` on success, ``False`` if import or ``ainvoke`` / projection failed.
    """
    try:
        from langgraph.types import Command

        from legalbot.agents.graph import get_compiled_graph
    except Exception as e:
        log.error("graph.import_failed", error=str(e))
        await _mark_run_status(run_id, RunStatus.failed)
        await _fail_pipeline_job_for_graph_error(session_id, run_id, e)
        return False

    sm = async_session_factory()
    async with sm() as db:
        session = (
            await db.execute(select(SessionRow).where(SessionRow.id == session_id))
        ).scalar_one()
        job = (
            await db.execute(
                select(ProcessingJob).where(ProcessingJob.id == session.job_id)
            )
        ).scalar_one_or_none()

    graph = await get_compiled_graph()
    config = {
        "configurable": {
            "thread_id": session.thread_id,
            "user_id": session.owner_user_id,
            "langgraph_user_id": session.owner_user_id,
        }
    }
    try:
        if resume_payload is not None:
            # Older checkpoints may predate has_attachments. Repair only missing/null
            # state so a valid False for emails without attachments is preserved.
            if has_attachments is not None:
                await _ensure_resume_has_attachments(
                    graph=graph,
                    config=config,
                    run_id=run_id,
                    session_id=session_id,
                    has_attachments=has_attachments,
                )
            output_state = await graph.ainvoke(Command(resume=resume_payload), config=config)
        else:
            from langchain_core.messages import HumanMessage

            initial_state: dict[str, Any] = {
                "session_id": str(session_id),
                "run_id": str(run_id),
                "user_id": session.owner_user_id,
                "graph_thread_id": session.thread_id,
                "messages": [
                    HumanMessage(
                        content=(
                            "Process the inbound email for this job end-to-end. "
                            "Your first action must be task('extract', ...)."
                        )
                    )
                ],
            }
            if job is not None:
                initial_state["job_id"] = str(job.id)
                initial_state["email_id"] = str(job.ingestion_item_id)
            if has_attachments is not None:
                initial_state["has_attachments"] = has_attachments
            output_state = await graph.ainvoke(initial_state, config=config)
        # Write projected output to session tables (replaces SessionProjectionMiddleware)
        await _write_session_projection(session_id, run_id, output_state)

        interrupted = await _graph_has_pending_interrupts(graph, config)
        if interrupted:
            log.info("graph.interrupted", run_id=str(run_id), session_id=str(session_id))
            await _mark_run_status(run_id, RunStatus.awaiting_human)
            await _persist_interrupt_from_checkpoint(graph, config, session_id, run_id)
            await _transition_pipeline_job(
                session_id=session_id,
                run_id=run_id,
                from_state=JobState.processing,
                to_state=JobState.awaiting_human,
            )
        else:
            await _mark_run_status(run_id, RunStatus.completed)
            await _finalize_job_if_primary(session_id, run_id)
        return True
    except Exception as e:
        log.exception("graph.invoke_failed", run_id=str(run_id), error=str(e))
        await _mark_run_status(run_id, RunStatus.failed)
        await _fail_pipeline_job_for_graph_error(session_id, run_id, e)
        return False


async def _ensure_resume_has_attachments(
    *,
    graph: Any,
    config: dict[str, Any],
    run_id: uuid.UUID,
    session_id: uuid.UUID,
    has_attachments: bool,
) -> None:
    state = await graph.aget_state(config)
    values = getattr(state, "values", None) or {}
    if not isinstance(values, dict):
        values = {}
    if "has_attachments" in values and values["has_attachments"] is not None:
        return

    await graph.aupdate_state(config=config, values={"has_attachments": has_attachments})
    configurable = config.get("configurable") or {}
    log.info(
        "resume.has_attachments_repaired",
        run_id=str(run_id),
        session_id=str(session_id),
        thread_id=configurable.get("thread_id"),
        has_attachments=has_attachments,
    )


async def _write_session_projection(
    session_id: uuid.UUID, run_id: uuid.UUID, output_state: dict[str, Any]
) -> None:
    """Write projected output from graph to session tables.

    This replaces SessionProjectionMiddleware - LangGraph's native output_schema
    handles projection, and we write the business-relevant fields to the database.
    """
    from legalbot.services.session import SessionService

    sm = async_session_factory()
    async with sm() as db:
        svc = SessionService(db)
        # Write messages from the output; full business payloads live in artifacts
        # and the compact stage handoff lives in stage_result when present.
        messages = output_state.get("messages", [])
        for msg in messages:
            role = getattr(msg, "type", "unknown")
            content = _extract_text_from_message(msg)
            if content:
                await svc.append_message(
                    session_id,
                    role=role,
                    content=content,
                    run_id=run_id,
                )
        stage_result = output_state.get("stage_result")
        if stage_result:
            await svc.append_message(
                session_id,
                role="stage_result",
                content=json.dumps(stage_result, sort_keys=True),
                run_id=run_id,
            )
        await db.commit()


def _extract_text_from_message(msg: Any) -> str | None:
    """Extract text content from a LangChain message."""
    if msg is None:
        return None
    if isinstance(msg, str):
        return msg
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[str] = []
        for block in content:
            if isinstance(block, dict) and "text" in block:
                out.append(str(block["text"]))
        return "\n".join(out) if out else None
    return None


async def _graph_has_pending_interrupts(graph: Any, config: dict[str, Any]) -> bool:
    """Check whether the graph paused at an interrupt rather than completing."""
    try:
        state_snapshot = await graph.aget_state(config)
        return bool(getattr(state_snapshot, "interrupts", ()))
    except Exception:
        return False


async def _persist_interrupt_from_checkpoint(
    graph: Any,
    config: dict[str, Any],
    session_id: uuid.UUID,
    run_id: uuid.UUID,
) -> None:
    """Read pending interrupt envelopes from the checkpoint and persist as InterruptRequest rows.

    This is the authoritative path for persisting interrupts — it covers both
    ``ask_human`` (which calls ``interrupt()`` directly) and
    ``HumanInTheLoopMiddleware`` (which also uses ``interrupt()`` internally).
    """
    from legalbot.interrupts.service import InterruptService

    try:
        state_snapshot = await graph.aget_state(config)
        interrupts = getattr(state_snapshot, "interrupts", ()) or ()
    except Exception:
        log.warning(
            "interrupt.checkpoint_read_failed",
            session_id=str(session_id),
            run_id=str(run_id),
        )
        return

    if not interrupts:
        return

    sm = async_session_factory()
    async with sm() as db:
        svc = InterruptService(db)
        existing = await svc.current_for_session(session_id)
        if existing is not None:
            return

        envelope = getattr(interrupts[0], "value", None)
        if not isinstance(envelope, dict):
            log.warning(
                "interrupt.unexpected_envelope",
                session_id=str(session_id),
                run_id=str(run_id),
                envelope_type=type(envelope).__name__,
            )
            return

        kind, payload, schema = _normalize_interrupt_envelope(envelope)

        await svc.open(
            session_id=session_id,
            run_id=run_id,
            kind=kind,
            payload=payload,
            schema=schema,
        )
        await db.commit()


def _normalize_interrupt_envelope(
    envelope: dict[str, Any],
) -> tuple[Any, dict[str, Any], dict[str, Any] | None]:
    """Translate a raw checkpoint interrupt value to (kind, payload, schema).

    Handles two envelope shapes:

    1. ``ask_human`` / ``request_human_approval`` shape:
       ``{"kind": "...", "payload": {...}}`` — used as-is.
    2. ``HumanInTheLoopMiddleware`` shape:
       ``{"action_requests": [...], "review_configs": [...]}`` — translated to
       ``tool_approval`` with the action requests/review configs preserved in
       the payload so the UI can render the description, tool name, args, and
       allowed decisions. ``schema`` is taken from the first review config's
       ``args_schema`` when present so ``edited_args`` can be validated.
    """
    from legalbot.db.types import InterruptKind

    if "action_requests" in envelope:
        action_requests = envelope.get("action_requests") or []
        review_configs = envelope.get("review_configs") or []
        payload: dict[str, Any] = {
            "action_requests": action_requests,
            "review_configs": review_configs,
        }
        schema: dict[str, Any] | None = None
        first_cfg = review_configs[0] if review_configs else None
        if isinstance(first_cfg, dict):
            args_schema = first_cfg.get("args_schema")
            if isinstance(args_schema, dict):
                schema = args_schema
        return InterruptKind.tool_approval, payload, schema

    kind_str = envelope.get("kind", "information_request")
    kind = (
        InterruptKind(kind_str)
        if kind_str in InterruptKind.__members__.values()
        else InterruptKind.information_request
    )
    raw_payload = envelope.get("payload", {})
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    schema = payload.get("schema") if isinstance(payload, dict) else None
    if not isinstance(schema, dict):
        schema = None
    return kind, payload, schema


async def _mark_run_status(run_id: uuid.UUID, status: RunStatus) -> None:
    from sqlalchemy import update

    session_status = (
        SessionStatus.awaiting_human if status == RunStatus.awaiting_human else SessionStatus.idle
    )

    sm = async_session_factory()
    async with sm() as db:
        await db.execute(
            update(Run)
            .where(Run.id == run_id)
            .values(status=str(status), finished_at=datetime.now(UTC))
        )
        run = (await db.execute(select(Run).where(Run.id == run_id))).scalar_one()
        await db.execute(
            update(SessionRow)
            .where(SessionRow.id == run.session_id)
            .values(status=str(session_status))
        )
        await db.commit()


async def _release_concurrency_budget(job_id: uuid.UUID) -> None:
    """Release the concurrency budget slot so new jobs can be dispatched."""
    try:
        from legalbot.jobs.dispatcher import ConcurrencyBudget

        budget = ConcurrencyBudget()
        await budget.release(job_id)
        log.info("budget.released", job_id=str(job_id))
    except Exception:
        log.exception("budget.release_failed", job_id=str(job_id))


async def _transition_pipeline_job(
    *,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
    from_state: JobState,
    to_state: JobState,
) -> None:
    """Move the primary pipeline job when the graph pauses or resumes HITL."""
    sm = async_session_factory()
    async with sm() as db:
        run = (await db.execute(select(Run).where(Run.id == run_id))).scalar_one()
        if run.kind != RunKind.pipeline:
            return
        session = (
            await db.execute(select(SessionRow).where(SessionRow.id == session_id))
        ).scalar_one()
        job_svc = JobService(db)
        try:
            await job_svc.transition(
                session.job_id,
                from_state=from_state,
                to_state=to_state,
            )
        except InvalidJobTransition as inv:
            log.warning(
                "pipeline.job_transition_skipped",
                job_id=str(session.job_id),
                run_id=str(run_id),
                from_state=str(from_state),
                to_state=str(to_state),
                error=str(inv),
            )
            return
        await db.commit()


async def _finalize_job_if_primary(session_id: uuid.UUID, run_id: uuid.UUID) -> None:
    sm = async_session_factory()
    async with sm() as db:
        run = (await db.execute(select(Run).where(Run.id == run_id))).scalar_one()
        if run.kind != RunKind.pipeline:
            return
        session = (
            await db.execute(select(SessionRow).where(SessionRow.id == session_id))
        ).scalar_one()
        job_svc = JobService(db)
        await job_svc.transition(
            session.job_id, from_state=JobState.processing, to_state=JobState.completed
        )
        await db.commit()
