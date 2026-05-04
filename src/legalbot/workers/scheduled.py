"""Dispatcher for ScheduledJob entries fired by redbeat."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

# Import handlers for their `@register_kind` side-effects.
import legalbot.scheduling.handlers  # noqa: F401
from legalbot.core.logging import get_logger
from legalbot.db.models import ScheduledJob
from legalbot.db.session import async_session_factory
from legalbot.db.types import ScheduledJobScheduleKind, ScheduledJobStatus
from legalbot.scheduling.models import JOB_KIND_REGISTRY
from legalbot.scheduling.service import SchedulingService
from legalbot.workers._async import run_async
from legalbot.workers.celery_app import celery_app

log = get_logger(__name__)


@celery_app.task(name="legalbot.workers.scheduled.run_scheduled_job", bind=True)
def run_scheduled_job(self, scheduled_job_id: str) -> dict[str, Any]:
    return run_async(_run_scheduled_job(scheduled_job_id))


async def _run_scheduled_job(scheduled_job_id: str) -> dict[str, Any]:
    sj_id = uuid.UUID(scheduled_job_id)
    sm = async_session_factory()
    async with sm() as db:
        sj = (
            await db.execute(select(ScheduledJob).where(ScheduledJob.id == sj_id))
        ).scalar_one_or_none()
        if sj is None or sj.status != ScheduledJobStatus.active:
            log.info("scheduled.skipped", id=scheduled_job_id, reason="not_active_or_missing")
            return {"ok": False, "reason": "not_active_or_missing"}

        handler = JOB_KIND_REGISTRY.get(sj.kind)
        if handler is None:
            log.warning("scheduled.unknown_kind", kind=sj.kind)
            return {"ok": False, "reason": "unknown_kind"}

        try:
            result = await handler(db, sj) or {}
        except Exception as e:
            log.exception("scheduled.handler_failed", kind=sj.kind, error=str(e))
            result = {"ok": False, "error": str(e)}

        svc = SchedulingService(db)
        await svc.mark_fired(sj.id, last_result=result)
        if sj.schedule_kind == ScheduledJobScheduleKind.one_shot:
            await svc.expire_one_shot(sj.id)
        await db.commit()
        return {"ok": True, "result": result}
