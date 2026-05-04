"""Read-only Run router."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from legalbot.api.schemas import RunRead
from legalbot.db.models import Run, RunStep
from legalbot.db.session import get_session

router = APIRouter()


@router.get("/{run_id}", response_model=RunRead)
async def get_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
) -> RunRead:
    row = (await db.execute(select(Run).where(Run.id == run_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return RunRead.model_validate(row, from_attributes=True)


@router.get("/{run_id}/steps")
async def list_steps(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
) -> list[dict]:
    rows = (
        (
            await db.execute(
                select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.started_at)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(s.id),
            "step_name": s.step_name,
            "checkpoint_id": s.checkpoint_id,
            "checkpoint_ns": s.checkpoint_ns,
            "status": s.status,
            "summary": s.summary,
            "started_at": s.started_at.isoformat(),
            "finished_at": s.finished_at.isoformat() if s.finished_at else None,
        }
        for s in rows
    ]
