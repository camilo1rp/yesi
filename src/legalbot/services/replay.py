"""ReplayService — create a child session and fork the LangGraph thread at a checkpoint."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from legalbot.core.logging import get_logger
from legalbot.db.models import Run, RunStep, Session
from legalbot.db.types import RunKind
from legalbot.scheduling.service import SchedulingService
from legalbot.services.run import RunService
from legalbot.services.session import SessionService

log = get_logger(__name__)


def _replay_fork_messages(existing_messages: list[Any], extra_context: str) -> list[Any]:
    """Build the ``messages`` value for replay ``aupdate_state``.

    Checkpoints may end with an ``assistant`` message that still has pending
    ``tool_calls``. Appending a new ``HumanMessage`` via the ``add_messages``
    reducer would leave that tail invalid for OpenAI. We clear the channel and
    set ``sanitize_messages_for_openai_chat(existing)`` plus the operator note.
    """
    from langchain_core.messages import HumanMessage, RemoveMessage
    from langgraph.graph.message import REMOVE_ALL_MESSAGES

    from legalbot.agents.stage_messages import sanitize_messages_for_openai_chat

    tail = sanitize_messages_for_openai_chat(list(existing_messages))
    return [
        RemoveMessage(id=REMOVE_ALL_MESSAGES),
        *tail,
        HumanMessage(content=extra_context),
    ]


class ReplayCheckpointNotFound(LookupError):
    pass


class ReplayService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def replay(
        self,
        *,
        session_id: uuid.UUID,
        from_run_id: uuid.UUID,
        from_step: str,
        extra_context: str,
    ) -> Session:
        session_svc = SessionService(self.db)
        run_svc = RunService(self.db)
        sched_svc = SchedulingService(self.db)

        parent = await session_svc.get(session_id)
        # Confirm the source run exists (scoped to the parent session) before forking.
        (await self.db.execute(select(Run).where(Run.id == from_run_id))).scalar_one()

        step_row = (
            await self.db.execute(
                select(RunStep)
                .where(RunStep.run_id == from_run_id, RunStep.step_name == from_step)
                .order_by(RunStep.started_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if step_row is None or not step_row.checkpoint_id:
            raise ReplayCheckpointNotFound(f"no checkpoint for run={from_run_id} step={from_step}")

        child = await session_svc.create_replay(
            parent=parent,
            branch_from_run_id=from_run_id,
            branch_from_checkpoint_id=step_row.checkpoint_id,
        )

        # Cascade-cancel any follow-ups scheduled by the superseded run.
        await sched_svc.cascade_cancel(run_id=from_run_id)

        new_run = await run_svc.start(
            session_id=child.id,
            job_id=parent.job_id,
            kind=RunKind.pipeline,
            trigger="replay",
            trigger_payload={
                "from_run_id": str(from_run_id),
                "from_step": from_step,
                "extra_context": extra_context,
                "parent_session_id": str(parent.id),
            },
        )

        await self.db.commit()

        # Fork the requested stage namespace on the child LangGraph thread.
        # Prefer the exact ``checkpoint_ns`` stored on the step (per-attempt stream);
        # fall back to legacy ``{stage}:{run_id}`` for rows written before that column existed.
        fork_ns = step_row.checkpoint_ns or f"{from_step}:{from_run_id}"
        await self._fork_thread(
            parent_thread_id=parent.thread_id,
            child_thread_id=child.thread_id,
            checkpoint_ns=fork_ns,
            checkpoint_id=step_row.checkpoint_id,
            extra_context=extra_context,
        )

        # Kick off the resumed pipeline.
        from legalbot.workers.runs import resume_run

        resume_run.delay(str(new_run.id), None)
        log.info(
            "replay.created",
            parent_session_id=str(parent.id),
            child_session_id=str(child.id),
            from_run_id=str(from_run_id),
            from_step=from_step,
        )
        return child

    async def _fork_thread(
        self,
        *,
        parent_thread_id: str,
        child_thread_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
        extra_context: str,
    ) -> None:
        """Use checkpointer.aupdate_state() to branch off `checkpoint_id`.

        LangGraph's supported fork path: supply `configurable.thread_id=<new>`,
        `configurable.checkpoint_ns=<stage:run_id:attempt_id>` (or legacy
        `<stage>:<run_id>`), and `configurable.checkpoint_id=<old>`, then merge the
        operator's extra context without breaking tool-call ordering on the messages channel.
        """
        try:
            from legalbot.agents.graph import get_compiled_graph

            graph = await get_compiled_graph()
            config = {
                "configurable": {
                    "thread_id": child_thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_id,
                    "parent_thread_id": parent_thread_id,
                }
            }
            try:
                snap = await graph.aget_state(config)
                existing = list(snap.values.get("messages") or [])
            except Exception:
                existing = []

            await graph.aupdate_state(
                config=config,
                values={
                    "graph_thread_id": child_thread_id,
                    "messages": _replay_fork_messages(existing, extra_context),
                },
            )
        except Exception as e:  # pragma: no cover — graph not ready in unit tests
            log.warning("replay.fork_failed", error=str(e))
