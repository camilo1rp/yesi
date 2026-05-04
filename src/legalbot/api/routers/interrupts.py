"""Interrupt GET + resolution POST."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from legalbot.db.session import get_session
from legalbot.interrupts.schemas import (
    InterruptEnvelope,
    InterruptResolution,
)
from legalbot.interrupts.service import (
    InterruptMismatch,
    InterruptService,
    NoPendingInterrupt,
)

router = APIRouter()


@router.get("/{session_id}/interrupt", response_model=InterruptEnvelope | None)
async def get_interrupt(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
) -> InterruptEnvelope | None:
    row = await InterruptService(db).current_for_session(session_id)
    if row is None:
        return None
    return InterruptEnvelope(
        id=row.id,
        session_id=row.session_id,
        run_id=row.run_id,
        kind=row.kind,
        payload=row.payload,
        schema=row.schema,
        status=row.status,
        created_at=row.created_at,
        expires_at=row.expires_at,
    )


@router.get("/{session_id}/interrupts")
async def audit(
    session_id: uuid.UUID,
    status: str | None = None,
    db: AsyncSession = Depends(get_session),
) -> list[dict]:
    rows = await InterruptService(db).audit(session_id, status=status)
    return [
        {
            "id": str(r.id),
            "kind": r.kind,
            "status": r.status,
            "payload": r.payload,
            "resolution": r.resolution,
            "created_at": r.created_at.isoformat(),
            "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
        }
        for r in rows
    ]


@router.post("/{session_id}/interrupt")
async def resolve_interrupt(
    session_id: uuid.UUID,
    resolution: InterruptResolution,
    resolved_by: str | None = None,
    db: AsyncSession = Depends(get_session),
) -> dict:
    svc = InterruptService(db)
    try:
        row, resume_value = await svc.resolve(session_id, resolution, resolved_by=resolved_by)
    except NoPendingInterrupt as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except InterruptMismatch as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    await db.commit()

    if row.run_id is not None:
        from legalbot.workers.runs import resume_run

        resume_run.delay(str(row.run_id), resume_value)

    return {"ok": True, "resumed": row.run_id is not None}
