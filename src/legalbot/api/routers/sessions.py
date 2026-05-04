"""Session, session-messages, SSE, replay, cancel/archive routes."""

from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from legalbot.api.schemas import (
    PostMessageRequest,
    ReplayRequest,
    SessionMessageRead,
    SessionRead,
)
from legalbot.db.models import Session as SessionRow
from legalbot.db.session import async_session_factory, get_session
from legalbot.db.types import RunKind
from legalbot.services.replay import ReplayService
from legalbot.services.run import RunService
from legalbot.services.session import ConcurrentRun, SessionService

router = APIRouter()


@router.get("/{session_id}", response_model=SessionRead)
async def get_session_route(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
) -> SessionRead:
    row = (
        await db.execute(select(SessionRow).where(SessionRow.id == session_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return SessionRead.model_validate(row, from_attributes=True)


@router.get("/{session_id}/messages", response_model=list[SessionMessageRead])
async def list_messages(
    session_id: uuid.UUID,
    after_index: int | None = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_session),
) -> list[SessionMessageRead]:
    rows = await SessionService(db).list_messages(session_id, after_index=after_index, limit=limit)
    return [SessionMessageRead.model_validate(r, from_attributes=True) for r in rows]


@router.post("/{session_id}/messages", response_model=SessionMessageRead)
async def post_message(
    session_id: uuid.UUID,
    body: PostMessageRequest,
    db: AsyncSession = Depends(get_session),
) -> SessionMessageRead:
    svc = SessionService(db)
    try:
        session = await svc.ensure_accepting_messages(session_id)
    except ConcurrentRun as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    msg = await svc.append_message(session_id, role="user", content=body.content)
    run_svc = RunService(db)
    run = await run_svc.start(
        session_id=session_id,
        job_id=session.job_id,
        kind=RunKind.user_followup,
        trigger="user_message",
        trigger_payload={"message_id": str(msg.id)},
    )
    await db.commit()

    from legalbot.workers.runs import continue_session

    continue_session.delay(str(run.id))
    return SessionMessageRead.model_validate(msg, from_attributes=True)


@router.get("/{session_id}/stream")
async def stream_messages(session_id: uuid.UUID) -> StreamingResponse:
    async def gen():
        after = -1
        while True:
            sm = async_session_factory()
            async with sm() as db:
                rows = await SessionService(db).list_messages(
                    session_id, after_index=after, limit=100
                )
            for r in rows:
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "id": str(r.id),
                            "role": r.role,
                            "content": r.content,
                            "message_index": r.message_index,
                        }
                    )
                    + "\n\n"
                )
                after = r.message_index
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/{session_id}/replay", response_model=SessionRead)
async def replay(
    session_id: uuid.UUID,
    body: ReplayRequest,
    db: AsyncSession = Depends(get_session),
) -> SessionRead:
    svc = ReplayService(db)
    try:
        child = await svc.replay(
            session_id=session_id,
            from_run_id=body.from_run_id,
            from_step=body.from_step,
            extra_context=body.extra_context,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return SessionRead.model_validate(child, from_attributes=True)


@router.post("/{session_id}/cancel", response_model=SessionRead)
async def cancel(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
) -> SessionRead:
    await SessionService(db).cancel_active_run(session_id)
    await db.commit()
    return await get_session_route(session_id, db)


@router.post("/{session_id}/archive", response_model=SessionRead)
async def archive(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
) -> SessionRead:
    row = await SessionService(db).archive(session_id)
    await db.commit()
    return SessionRead.model_validate(row, from_attributes=True)
