"""Processing-job router."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from legalbot.api.schemas import JobRead
from legalbot.db.models import ProcessingJob, Run, RunStep
from legalbot.db.session import get_session
from legalbot.jobs.service import JobService

router = APIRouter()


@router.get("", response_model=list[JobRead])
async def list_jobs(
    state: str | None = None,
    owner_user_id: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_session),
) -> list[JobRead]:
    stmt = select(ProcessingJob)
    if state:
        stmt = stmt.where(ProcessingJob.state == state)
    if owner_user_id:
        stmt = stmt.where(ProcessingJob.owner_user_id == owner_user_id)
    stmt = stmt.order_by(ProcessingJob.created_at.desc()).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return [JobRead.model_validate(r, from_attributes=True) for r in rows]


@router.get("/{job_id}", response_model=JobRead)
async def get_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
) -> JobRead:
    row = (
        await db.execute(select(ProcessingJob).where(ProcessingJob.id == job_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return JobRead.model_validate(row, from_attributes=True)


@router.get("/{job_id}/timeline")
async def get_timeline(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
) -> dict:
    runs = (
        (await db.execute(select(Run).where(Run.job_id == job_id).order_by(Run.started_at)))
        .scalars()
        .all()
    )
    steps = (
        (
            await db.execute(
                select(RunStep)
                .where(RunStep.run_id.in_([r.id for r in runs]) if runs else False)
                .order_by(RunStep.started_at)
            )
        )
        .scalars()
        .all()
    )
    return {
        "runs": [
            {
                "id": str(r.id),
                "session_id": str(r.session_id),
                "kind": r.kind,
                "status": r.status,
                "started_at": r.started_at.isoformat(),
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in runs
        ],
        "steps": [
            {
                "id": str(s.id),
                "run_id": str(s.run_id),
                "step_name": s.step_name,
                "status": s.status,
                "started_at": s.started_at.isoformat(),
                "finished_at": s.finished_at.isoformat() if s.finished_at else None,
            }
            for s in steps
        ],
    }


@router.post("/{job_id}/retry", response_model=JobRead)
async def retry_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
) -> JobRead:
    await JobService(db).retry(job_id)
    await db.commit()
    return await get_job(job_id, db)


@router.post("/{job_id}/cancel", response_model=JobRead)
async def cancel_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
) -> JobRead:
    await JobService(db).cancel(job_id)
    await db.commit()
    return await get_job(job_id, db)


@router.post("/{job_id}/archive", response_model=JobRead)
async def archive_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
) -> JobRead:
    await JobService(db).archive(job_id)
    await db.commit()
    return await get_job(job_id, db)
