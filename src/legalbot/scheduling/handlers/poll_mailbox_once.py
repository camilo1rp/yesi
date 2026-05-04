"""poll_mailbox_once — enqueue a single poll_mailbox run for a specific mailbox."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from legalbot.db.models import ScheduledJob
from legalbot.scheduling.models import register_kind


@register_kind("poll_mailbox_once")
async def handle_poll_mailbox_once(db: AsyncSession, job: ScheduledJob) -> dict[str, Any] | None:
    from legalbot.workers.ingest import poll_mailbox

    payload = job.payload or {}
    mailbox_id = payload.get("mailbox_id")
    if not mailbox_id:
        return {"ok": False, "error": "missing mailbox_id"}
    poll_mailbox.delay(str(uuid.UUID(mailbox_id)))
    return {"ok": True}
