"""Scheduled-jobs CRUD (distinct from processing_job)."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from legalbot.api.schemas import ScheduledJobCreate, ScheduledJobRead
from legalbot.db.models import ScheduledJob
from legalbot.db.session import get_session
from legalbot.db.types import ScheduledJobScheduleKind
from legalbot.scheduling.models import ScheduleSpec
from legalbot.scheduling.service import SchedulingService, UnknownJobKind

router = APIRouter()


@router.get("", response_model=list[ScheduledJobRead])
async def list_jobs(
    owner_user_id: str | None = None,
    session_id: uuid.UUID | None = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_session),
) -> list[ScheduledJobRead]:
    stmt = select(ScheduledJob)
    if owner_user_id:
        stmt = stmt.where(ScheduledJob.owner_user_id == owner_user_id)
    if session_id:
        stmt = stmt.where(ScheduledJob.session_id == session_id)
    if status:
        stmt = stmt.where(ScheduledJob.status == status)
    stmt = stmt.order_by(ScheduledJob.next_run_at.asc().nullslast()).limit(200)
    rows = (await db.execute(stmt)).scalars().all()
    return [ScheduledJobRead.model_validate(r, from_attributes=True) for r in rows]


@router.post("", response_model=ScheduledJobRead)
async def create_job(
    body: ScheduledJobCreate,
    db: AsyncSession = Depends(get_session),
) -> ScheduledJobRead:
    svc = SchedulingService(db)
    try:
        if body.schedule_kind == ScheduledJobScheduleKind.one_shot:
            run_at = datetime.fromisoformat(body.schedule_spec["run_at"])
            row = await svc.schedule_one_shot(
                kind=body.kind,
                payload=body.payload,
                run_at=run_at,
                owner_user_id=body.owner_user_id,
                session_id=body.session_id,
            )
        else:
            spec = ScheduleSpec(
                kind=body.schedule_kind,
                cron_expression=body.schedule_spec.get("expression"),
                interval_seconds=body.schedule_spec.get("interval_seconds"),
            )
            row = await svc.schedule_recurring(
                kind=body.kind,
                payload=body.payload,
                spec=spec,
                owner_user_id=body.owner_user_id,
                session_id=body.session_id,
            )
    except UnknownJobKind as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await db.commit()
    return ScheduledJobRead.model_validate(row, from_attributes=True)


@router.post("/{job_id}/pause")
async def pause(job_id: uuid.UUID, db: AsyncSession = Depends(get_session)) -> dict:
    await SchedulingService(db).pause(job_id)
    await db.commit()
    return {"ok": True}


@router.post("/{job_id}/resume")
async def resume(job_id: uuid.UUID, db: AsyncSession = Depends(get_session)) -> dict:
    await SchedulingService(db).resume(job_id)
    await db.commit()
    return {"ok": True}


@router.post("/{job_id}/cancel")
async def cancel(job_id: uuid.UUID, db: AsyncSession = Depends(get_session)) -> dict:
    await SchedulingService(db).cancel(job_id)
    await db.commit()
    return {"ok": True}
