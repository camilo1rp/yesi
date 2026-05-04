"""SessionService — create_primary, create_replay, post_system_message, list_messages.

Semantics per §7.5 of the plan:
- Exactly one `primary` session per processing_job.
- Replay children share the parent's `job_id` (audit timeline stays unified).
- `POST /sessions/{id}/messages` on a session with `status != idle` → 409 Conflict (ConcurrentRun).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from legalbot.core.logging import get_logger
from legalbot.db.models import (
    ProcessingJob,
    Run,
    Session,
    SessionMessage,
)
from legalbot.db.types import RunKind, SessionKind, SessionStatus

log = get_logger(__name__)


class ConcurrentRun(RuntimeError):
    """Raised when a user posts while a run is actively executing."""


class SessionNotFound(LookupError):
    pass


class SessionService:
    MESSAGE_SOFT_CAP = 500

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, session_id: uuid.UUID) -> Session:
        result = await self.db.execute(select(Session).where(Session.id == session_id))
        row = result.scalar_one_or_none()
        if row is None:
            raise SessionNotFound(str(session_id))
        return row

    async def create_primary(self, job: ProcessingJob) -> Session:
        thread_id = f"job-{job.id}-primary-{uuid.uuid4().hex[:8]}"
        row = Session(
            id=uuid.uuid4(),
            job_id=job.id,
            thread_id=thread_id,
            kind=str(SessionKind.primary),
            status=str(SessionStatus.idle),
            owner_user_id=job.owner_user_id,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def create_replay(
        self,
        *,
        parent: Session,
        branch_from_run_id: uuid.UUID,
        branch_from_checkpoint_id: str,
    ) -> Session:
        thread_id = f"session-{parent.id}-replay-{uuid.uuid4().hex[:8]}"
        row = Session(
            id=uuid.uuid4(),
            job_id=parent.job_id,
            thread_id=thread_id,
            kind=str(SessionKind.replay),
            parent_session_id=parent.id,
            branch_from_run_id=branch_from_run_id,
            branch_from_checkpoint_id=branch_from_checkpoint_id,
            status=str(SessionStatus.idle),
            owner_user_id=parent.owner_user_id,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def post_system_message(self, session_id: uuid.UUID, content: str) -> SessionMessage:
        idx = await self._next_message_index(session_id)
        row = SessionMessage(
            id=uuid.uuid4(),
            session_id=session_id,
            role="system",
            content=content,
            message_index=idx,
        )
        self.db.add(row)
        await self.db.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(
                message_count=Session.message_count + 1,
                last_activity_at=datetime.now(UTC),
            )
        )
        await self.db.flush()
        return row

    async def append_message(
        self,
        session_id: uuid.UUID,
        *,
        role: str,
        content: str,
        run_id: uuid.UUID | None = None,
    ) -> SessionMessage:
        idx = await self._next_message_index(session_id)
        row = SessionMessage(
            id=uuid.uuid4(),
            session_id=session_id,
            run_id=run_id,
            role=role,
            content=content,
            message_index=idx,
        )
        self.db.add(row)
        await self.db.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(
                message_count=Session.message_count + 1,
                last_activity_at=datetime.now(UTC),
            )
        )
        await self.db.flush()
        return row

    async def _next_message_index(self, session_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.coalesce(func.max(SessionMessage.message_index), -1)).where(
                SessionMessage.session_id == session_id
            )
        )
        return int(result.scalar_one()) + 1

    async def list_messages(
        self, session_id: uuid.UUID, *, after_index: int | None = None, limit: int = 100
    ) -> list[SessionMessage]:
        stmt = select(SessionMessage).where(SessionMessage.session_id == session_id)
        if after_index is not None:
            stmt = stmt.where(SessionMessage.message_index > after_index)
        stmt = stmt.order_by(SessionMessage.message_index).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars())

    async def ensure_accepting_messages(self, session_id: uuid.UUID) -> Session:
        session = await self.get(session_id)
        if session.status == SessionStatus.archived:
            raise ConcurrentRun("session is archived")
        if session.status == SessionStatus.running:
            raise ConcurrentRun("a run is already executing on this session")
        return session

    async def archive(self, session_id: uuid.UUID) -> Session:
        await self.db.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(status=str(SessionStatus.archived))
        )
        return await self.get(session_id)

    async def cancel_active_run(self, session_id: uuid.UUID) -> None:
        await self.db.execute(
            update(Run)
            .where(Run.session_id == session_id, Run.status == "running")
            .values(status="cancelled", finished_at=datetime.now(UTC))
        )
        await self.db.execute(
            update(Session).where(Session.id == session_id).values(status=str(SessionStatus.idle))
        )

    async def wake_agent_from_schedule(
        self, session_id: uuid.UUID, *, trigger_payload: dict[str, Any] | None = None
    ) -> uuid.UUID:
        """Create a scheduled-kind run and enqueue it. Returns the run_id."""
        from legalbot.services.run import RunService
        from legalbot.workers.runs import continue_session

        session = await self.get(session_id)
        run_service = RunService(self.db)
        run = await run_service.start(
            session_id=session.id,
            job_id=session.job_id,
            kind=RunKind.scheduled,
            trigger="scheduled_job",
            trigger_payload=trigger_payload or {},
        )
        await self.db.commit()
        continue_session.delay(str(run.id))
        return run.id
