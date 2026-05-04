"""RunService — lightweight helpers around the Run / RunStep tables."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from legalbot.db.models import Run, RunStep
from legalbot.db.types import RunKind, RunStatus, StepName


class RunService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def start(
        self,
        *,
        session_id: uuid.UUID,
        job_id: uuid.UUID,
        kind: RunKind,
        trigger: str,
        trigger_payload: dict[str, Any] | None = None,
    ) -> Run:
        row = Run(
            id=uuid.uuid4(),
            session_id=session_id,
            job_id=job_id,
            kind=str(kind),
            status=str(RunStatus.running),
            trigger=trigger,
            trigger_payload=trigger_payload,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def mark_finished(
        self, run_id: uuid.UUID, *, status: RunStatus = RunStatus.completed
    ) -> Run:
        await self.db.execute(
            update(Run)
            .where(Run.id == run_id)
            .values(status=str(status), finished_at=datetime.now(UTC))
        )
        result = await self.db.execute(select(Run).where(Run.id == run_id))
        return result.scalar_one()

    async def add_step(
        self,
        run_id: uuid.UUID,
        *,
        step_name: StepName | str,
        checkpoint_id: str | None = None,
        checkpoint_ns: str | None = None,
        summary: str | None = None,
    ) -> RunStep:
        row = RunStep(
            id=uuid.uuid4(),
            run_id=run_id,
            step_name=str(step_name),
            checkpoint_id=checkpoint_id,
            checkpoint_ns=checkpoint_ns,
            summary=summary,
            status="running",
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def finish_step(
        self, step_id: uuid.UUID, *, status: str = "completed", summary: str | None = None
    ) -> None:
        values: dict[str, Any] = {
            "status": status,
            "finished_at": datetime.now(UTC),
        }
        if summary is not None:
            values["summary"] = summary
        await self.db.execute(update(RunStep).where(RunStep.id == step_id).values(**values))
