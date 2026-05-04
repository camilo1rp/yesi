"""replay_trigger — invokes ReplayService.replay at the scheduled time.

Payload: {session_id, from_run_id, from_step, extra_context}.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from legalbot.db.models import ScheduledJob
from legalbot.scheduling.models import register_kind


@register_kind("replay_trigger")
async def handle_replay_trigger(db: AsyncSession, job: ScheduledJob) -> dict[str, Any] | None:
    from legalbot.services.replay import ReplayService

    payload = job.payload or {}
    session_id = payload.get("session_id")
    from_run_id = payload.get("from_run_id")
    from_step = payload.get("from_step")
    extra_context = payload.get("extra_context") or ""
    if not (session_id and from_run_id and from_step):
        return {"ok": False, "error": "missing replay fields"}

    svc = ReplayService(db)
    child = await svc.replay(
        session_id=uuid.UUID(session_id),
        from_run_id=uuid.UUID(from_run_id),
        from_step=from_step,
        extra_context=extra_context,
    )
    return {"ok": True, "child_session_id": str(child.id)}
