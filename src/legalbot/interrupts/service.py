"""InterruptService: open, resolve (with artifact_edits), expire, audit helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from jsonschema import Draft202012Validator, ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from legalbot.artifacts.service import ArtifactService
from legalbot.core import metrics
from legalbot.core.config import get_settings
from legalbot.core.logging import get_logger
from legalbot.db.models import (
    InterruptRequest,
    Session,
    UserIntervention,
)
from legalbot.db.types import (
    InterruptKind,
    InterruptStatus,
    SessionStatus,
)
from legalbot.interrupts.schemas import (
    ArtifactEditSchema,
    InterruptResolution,
    InterruptResolutionInfo,
    InterruptResolutionReview,
    InterruptResolutionTool,
)

log = get_logger(__name__)


def _first_action_name(request: InterruptRequest | None) -> str | None:
    """Return the first ``action_requests[].name`` from a persisted HITL payload, if any."""
    if request is None:
        return None
    payload = request.payload if isinstance(request.payload, dict) else {}
    actions = payload.get("action_requests")
    if not isinstance(actions, list) or not actions:
        return None
    first = actions[0]
    if isinstance(first, dict):
        name = first.get("name")
        if isinstance(name, str):
            return name
    return None


class InterruptMismatch(ValueError):
    pass


class NoPendingInterrupt(LookupError):
    pass


class InterruptService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def open(
        self,
        *,
        session_id: uuid.UUID,
        run_id: uuid.UUID | None,
        kind: InterruptKind,
        payload: dict[str, Any],
        schema: dict[str, Any] | None = None,
        ttl_sec: int | None = None,
    ) -> InterruptRequest:
        settings = get_settings()
        now = datetime.now(UTC)
        ttl = ttl_sec if ttl_sec is not None else settings.INTERRUPT_TTL_SEC
        expires_at = now + timedelta(seconds=ttl) if ttl else None

        row = InterruptRequest(
            id=uuid.uuid4(),
            session_id=session_id,
            run_id=run_id,
            kind=str(kind),
            payload=payload,
            schema=schema,
            status=str(InterruptStatus.pending),
            expires_at=expires_at,
        )
        self.db.add(row)
        await self.db.flush()

        await self.db.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(
                status=str(SessionStatus.awaiting_human),
                interrupt_kind=str(kind),
                active_interrupt_id=row.id,
            )
        )
        metrics.interrupts_total.labels(kind=str(kind)).inc()
        log.info(
            "interrupt.opened",
            session_id=str(session_id),
            run_id=str(run_id) if run_id else None,
            kind=str(kind),
            expires_at=expires_at.isoformat() if expires_at else None,
        )
        return row

    async def current_for_session(self, session_id: uuid.UUID) -> InterruptRequest | None:
        result = await self.db.execute(
            select(InterruptRequest)
            .where(
                InterruptRequest.session_id == session_id,
                InterruptRequest.status == str(InterruptStatus.pending),
            )
            .order_by(InterruptRequest.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def audit(
        self, session_id: uuid.UUID, *, status: str | None = None
    ) -> list[InterruptRequest]:
        stmt = select(InterruptRequest).where(InterruptRequest.session_id == session_id)
        if status:
            stmt = stmt.where(InterruptRequest.status == status)
        stmt = stmt.order_by(InterruptRequest.created_at.desc())
        result = await self.db.execute(stmt)
        return list(result.scalars())

    async def resolve(
        self,
        session_id: uuid.UUID,
        resolution: InterruptResolution,
        *,
        resolved_by: str | None = None,
    ) -> tuple[InterruptRequest, Any]:
        """Persist resolution, apply artifact_edits BEFORE resume, return (row, resume_value)."""
        current = await self.current_for_session(session_id)
        if current is None:
            raise NoPendingInterrupt("no pending interrupt")
        if current.kind != resolution.kind:
            raise InterruptMismatch(
                f"kind mismatch: active={current.kind} payload={resolution.kind}"
            )

        if isinstance(resolution, InterruptResolutionInfo):
            if current.schema and not resolution.skipped and resolution.answer is not None:
                try:
                    Draft202012Validator(current.schema).validate(resolution.answer)
                except ValidationError as e:
                    raise ValueError(f"answer does not match schema: {e.message}") from e

        # Apply artifact_edits before flipping resume semantics.
        if resolution.artifact_edits:
            art_svc = ArtifactService(self.db)
            for edit in resolution.artifact_edits:
                await self._apply_artifact_edit(
                    art_svc,
                    session_id=session_id,
                    run_id=current.run_id,
                    edit=edit,
                    created_by=resolved_by or "user",
                )

        if isinstance(resolution, InterruptResolutionInfo) and resolution.answer:
            await self._merge_contract_provided_fields(
                session_id=session_id,
                run_id=current.run_id,
                answer=resolution.answer,
            )

        resume_value = self._resume_value(resolution, current)

        await self.db.execute(
            update(InterruptRequest)
            .where(InterruptRequest.id == current.id)
            .values(
                status=str(InterruptStatus.resolved),
                resolution=resolution.model_dump(mode="json"),
                resolved_by=resolved_by,
                resolved_at=datetime.now(UTC),
            )
        )
        self.db.add(
            UserIntervention(
                id=uuid.uuid4(),
                interrupt_request_id=current.id,
                run_id=current.run_id,
                decision=self._decision_label(resolution),
                edited_payload=resolution.model_dump(mode="json"),
                decided_by=resolved_by,
            )
        )
        await self.db.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(
                status=str(SessionStatus.running),
                interrupt_kind=None,
                active_interrupt_id=None,
            )
        )
        metrics.interrupt_resolutions_total.labels(
            kind=current.kind, decision=self._decision_label(resolution)
        ).inc()

        log.info(
            "interrupt.resolved",
            interrupt_id=str(current.id),
            session_id=str(session_id),
            kind=current.kind,
            decision=self._decision_label(resolution),
        )
        return current, resume_value

    async def expire_stalled(self) -> int:
        now = datetime.now(UTC)
        result = await self.db.execute(
            update(InterruptRequest)
            .where(
                InterruptRequest.status == str(InterruptStatus.pending),
                InterruptRequest.expires_at.is_not(None),
                InterruptRequest.expires_at < now,
            )
            .values(status=str(InterruptStatus.expired))
            .returning(InterruptRequest)
        )
        rows = list(result.scalars())
        for row in rows:
            await self.db.execute(
                update(Session)
                .where(Session.id == row.session_id)
                .values(
                    status=str(SessionStatus.failed),
                    interrupt_kind=None,
                    active_interrupt_id=None,
                )
            )
        return len(rows)

    def _resume_value(
        self, resolution: InterruptResolution, request: InterruptRequest | None = None
    ) -> Any:
        if isinstance(resolution, InterruptResolutionTool):
            return self._tool_approval_resume_value(resolution, request)
        if isinstance(resolution, InterruptResolutionInfo):
            if resolution.skipped:
                return {"skipped": True, "reason": resolution.reason}
            return resolution.answer
        if isinstance(resolution, InterruptResolutionReview):
            return {
                "decision": resolution.decision,
                "revision_notes": resolution.revision_notes,
            }
        return resolution.model_dump(mode="json")

    @staticmethod
    def _tool_approval_resume_value(
        resolution: InterruptResolutionTool, request: InterruptRequest | None
    ) -> dict[str, Any]:
        """Build the HITL-shaped resume value expected by ``HumanInTheLoopMiddleware``.

        The middleware reads ``interrupt(...)["decisions"]`` and dispatches per
        decision ``type``: ``approve``, ``edit`` (with ``edited_action``), or
        ``reject`` (with optional ``message``). The persisted ``request.payload``
        carries ``action_requests`` so we can recover the tool name needed for
        an ``edit`` decision.
        """
        decision: dict[str, Any]
        if resolution.decision == "approve":
            decision = {"type": "approve"}
        elif resolution.decision == "edit":
            tool_name = _first_action_name(request)
            decision = {
                "type": "edit",
                "edited_action": {
                    "name": tool_name or "",
                    "args": resolution.edited_args or {},
                },
            }
        else:
            decision = {"type": "reject"}
            if resolution.reason:
                decision["message"] = resolution.reason
        return {"decisions": [decision]}

    def _decision_label(self, resolution: InterruptResolution) -> str:
        if isinstance(resolution, InterruptResolutionInfo):
            return "skipped" if resolution.skipped else "answered"
        return resolution.decision  # type: ignore[attr-defined]

    async def _merge_contract_provided_fields(
        self,
        *,
        session_id: uuid.UUID,
        run_id: uuid.UUID | None,
        answer: dict[str, Any],
    ) -> None:
        """Merge human answers into analysis/report action_payload.provided_fields."""
        if not isinstance(answer, dict) or not answer:
            return
        art_svc = ArtifactService(self.db)
        try:
            report_content: dict[str, Any] = {}
            try:
                _, content = await art_svc.read(session_id=session_id, key_or_id="analysis/report")
                if isinstance(content, dict):
                    report_content = dict(content)
            except Exception:
                pass

            action_payload = dict(report_content.get("action_payload") or {})
            provided_fields = dict(action_payload.get("provided_fields") or {})
            provided_fields.update(answer)
            action_payload["provided_fields"] = provided_fields
            report_content["action_payload"] = action_payload

            await art_svc.update(
                session_id=session_id,
                key="analysis/report",
                patch_or_content=report_content,
                merge="replace",
                kind="analysis",
                run_id=run_id,
                producer="interrupt",
            )
            log.info(
                "interrupt.contract_fields_merged",
                session_id=str(session_id),
                fields=list(answer.keys()),
            )
        except Exception:
            log.warning(
                "interrupt.contract_fields_merge_failed",
                session_id=str(session_id),
            )

    async def _apply_artifact_edit(
        self,
        art_svc: ArtifactService,
        *,
        session_id: uuid.UUID,
        run_id: uuid.UUID | None,
        edit: ArtifactEditSchema,
        created_by: str,
    ) -> None:
        if edit.patch is not None:
            await art_svc.update(
                session_id=session_id,
                key=edit.key,
                patch_or_content=edit.patch,
                merge=edit.merge,
                producer="user",
                mime=edit.mime or "application/json",
                metadata=edit.metadata,
                run_id=run_id,
                created_by=created_by,
            )
        elif edit.content is not None:
            await art_svc.update(
                session_id=session_id,
                key=edit.key,
                patch_or_content=edit.content,
                merge="replace",
                producer="user",
                mime=edit.mime or "application/json",
                metadata=edit.metadata,
                run_id=run_id,
                created_by=created_by,
            )
