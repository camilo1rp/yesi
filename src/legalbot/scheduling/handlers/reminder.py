"""Reminder handler — posts a system message into the target session's transcript.

Payload shape: {session_id, message, wake_agent: bool}.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from legalbot.db.models import ScheduledJob
from legalbot.scheduling.models import register_kind


@register_kind("reminder")
async def handle_reminder(db: AsyncSession, job: ScheduledJob) -> dict[str, Any] | None:
    from legalbot.services.session import SessionService

    payload = job.payload or {}
    session_id_raw = payload.get("session_id")
    if session_id_raw is None:
        return {"ok": False, "error": "missing session_id"}
    session_id = uuid.UUID(session_id_raw)
    message = payload.get("message") or "Reminder"
    wake = bool(payload.get("wake_agent"))

    svc = SessionService(db)
    await svc.post_system_message(session_id, message)
    if wake:
        run_id = await svc.wake_agent_from_schedule(session_id, trigger_payload=payload)
        return {"ok": True, "run_id": str(run_id)}
    return {"ok": True}
