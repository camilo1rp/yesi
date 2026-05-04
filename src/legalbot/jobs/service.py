"""JobService — guarded state transitions and lifecycle helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from legalbot.core import metrics
from legalbot.core.logging import get_logger
from legalbot.db.models import ProcessingJob, Run, RunStep
from legalbot.db.types import JobState
from legalbot.jobs.models import ALLOWED_TRANSITIONS, InvalidJobTransition

log = get_logger(__name__)


class JobService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_for_item(
        self,
        *,
        ingestion_item_id: uuid.UUID,
        owner_user_id: str,
        initial_state: JobState = JobState.intake,
        priority: int = 0,
    ) -> ProcessingJob:
        """Idempotent: `ON CONFLICT (ingestion_item_id) DO NOTHING RETURNING *`."""
        stmt = (
            pg_insert(ProcessingJob)
            .values(
                id=uuid.uuid4(),
                ingestion_item_id=ingestion_item_id,
                state=str(initial_state),
                priority=priority,
                owner_user_id=owner_user_id,
            )
            .on_conflict_do_nothing(index_elements=["ingestion_item_id"])
            .returning(ProcessingJob)
        )
        result = await self.db.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            existing = await self.db.execute(
                select(ProcessingJob).where(ProcessingJob.ingestion_item_id == ingestion_item_id)
            )
            row = existing.scalar_one()
        return row

    async def transition(
        self,
        job_id: uuid.UUID,
        *,
        from_state: str,
        to_state: str,
        extra: dict[str, Any] | None = None,
    ) -> ProcessingJob:
        """Guarded transition: `UPDATE ... WHERE state = :from_state`.

        Returns the updated row; raises InvalidJobTransition on either illegal pair or
        `from_state` mismatch (stale view).
        """

        if (from_state, to_state) not in ALLOWED_TRANSITIONS:
            raise InvalidJobTransition(f"{from_state} -> {to_state} not allowed")

        now = datetime.now(UTC)
        values: dict[str, Any] = {"state": to_state, "updated_at": now}
        if to_state == JobState.dispatched:
            values["dispatched_at"] = now
        if to_state == JobState.processing:
            values["processing_started_at"] = now
        if to_state in (JobState.completed, JobState.failed, JobState.archived):
            values["finished_at"] = now
        if extra:
            values.update(extra)

        stmt = (
            update(ProcessingJob)
            .where(ProcessingJob.id == job_id, ProcessingJob.state == from_state)
            .values(**values)
            .returning(ProcessingJob)
        )
        result = await self.db.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            raise InvalidJobTransition(f"job {job_id} not in expected state {from_state!r}")

        log.info(
            "job.transition",
            job_id=str(job_id),
            from_state=from_state,
            to_state=to_state,
        )
        metrics.job_state_gauge.labels(state=to_state).inc()
        metrics.job_state_gauge.labels(state=from_state).dec()
        if to_state == JobState.dispatched and row.created_at is not None:
            metrics.job_dispatch_latency.observe((now - row.created_at).total_seconds())
        if to_state == JobState.completed and row.processing_started_at is not None:
            metrics.job_runtime_seconds.observe((now - row.processing_started_at).total_seconds())
        return row

    async def set_current_session(self, job_id: uuid.UUID, session_id: uuid.UUID | None) -> None:
        await self.db.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == job_id)
            .values(current_session_id=session_id)
        )

    async def retry(self, job_id: uuid.UUID) -> ProcessingJob:
        job = await self._get(job_id)
        return await self.transition(
            job_id,
            from_state=JobState.failed,
            to_state=JobState.ready,
            extra={
                "attempts": (job.attempts or 0) + 1,
                "last_error": None,
                "error_kind": None,
            },
        )

    async def cancel(
        self, job_id: uuid.UUID, *, reason: str = "cancelled_by_user"
    ) -> ProcessingJob:
        job = await self._get(job_id)
        to_state = JobState.failed
        return await self.transition(
            job_id,
            from_state=job.state,
            to_state=to_state,
            extra={"last_error": reason, "error_kind": "cancelled_by_user"},
        )

    async def archive(self, job_id: uuid.UUID) -> ProcessingJob:
        job = await self._get(job_id)
        return await self.transition(job_id, from_state=job.state, to_state=JobState.archived)

    async def mark_failed(
        self, job_id: uuid.UUID, *, from_state: str, error: str, error_kind: str = "exception"
    ) -> ProcessingJob:
        return await self.transition(
            job_id,
            from_state=from_state,
            to_state=JobState.failed,
            extra={"last_error": error[:2000], "error_kind": error_kind},
        )

    async def _get(self, job_id: uuid.UUID) -> ProcessingJob:
        result = await self.db.execute(select(ProcessingJob).where(ProcessingJob.id == job_id))
        return result.scalar_one()


class JobTimelineService:
    """Interleaves runs and run_steps across every session of a job for audit."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def timeline(self, job_id: uuid.UUID) -> list[dict[str, Any]]:
        runs_q = await self.db.execute(
            select(Run).where(Run.job_id == job_id).order_by(Run.started_at)
        )
        events: list[dict[str, Any]] = []
        for r in runs_q.scalars():
            events.append(
                {
                    "type": "run",
                    "at": r.started_at,
                    "run_id": str(r.id),
                    "session_id": str(r.session_id),
                    "kind": r.kind,
                    "status": r.status,
                    "trigger": r.trigger,
                }
            )
            steps_q = await self.db.execute(
                select(RunStep).where(RunStep.run_id == r.id).order_by(RunStep.started_at)
            )
            for s in steps_q.scalars():
                events.append(
                    {
                        "type": "step",
                        "at": s.started_at,
                        "run_id": str(r.id),
                        "step": s.step_name,
                        "status": s.status,
                        "summary": s.summary,
                        "checkpoint_ns": s.checkpoint_ns,
                    }
                )
        events.sort(key=lambda e: e["at"] or datetime.min.replace(tzinfo=UTC))
        return events
