"""Cron tasks: dispatcher, sweeper, interrupt-TTL, redbeat reconciler."""

from __future__ import annotations

from typing import Any

from legalbot.core.logging import get_logger
from legalbot.db.session import async_session_factory
from legalbot.interrupts.service import InterruptService
from legalbot.jobs.dispatcher import ConcurrencyBudget, dispatch_once
from legalbot.jobs.sweeper import reap_stuck
from legalbot.scheduling.service import SchedulingService
from legalbot.workers._async import run_async
from legalbot.workers.celery_app import celery_app

log = get_logger(__name__)


@celery_app.task(name="legalbot.workers.janitors.dispatch_ready_jobs", bind=True)
def dispatch_ready_jobs(self) -> dict[str, Any]:
    return run_async(_dispatch_ready_jobs())


async def _dispatch_ready_jobs() -> dict[str, Any]:
    sm = async_session_factory()
    budget = ConcurrencyBudget()
    async with sm() as db:
        dispatched = await dispatch_once(db, budget=budget)
    result = {"dispatched": len(dispatched), "ids": [str(i) for i in dispatched]}
    log.info("dispatch.done", **result)
    return result


@celery_app.task(name="legalbot.workers.janitors.reap_stuck_jobs", bind=True)
def reap_stuck_jobs(self) -> dict[str, Any]:
    return run_async(_reap_stuck_jobs())


async def _reap_stuck_jobs() -> dict[str, Any]:
    sm = async_session_factory()
    async with sm() as db:
        result = await reap_stuck(db)
        await db.commit()
    log.info("reap.done", **result)
    return result


@celery_app.task(name="legalbot.workers.janitors.expire_stalled_interrupts", bind=True)
def expire_stalled_interrupts(self) -> dict[str, Any]:
    return run_async(_expire_stalled_interrupts())


async def _expire_stalled_interrupts() -> dict[str, Any]:
    sm = async_session_factory()
    async with sm() as db:
        svc = InterruptService(db)
        expired = await svc.expire_stalled()
        await db.commit()
    return {"expired": expired}


@celery_app.task(name="legalbot.workers.janitors.rebuild_redbeat_from_db", bind=True)
def rebuild_redbeat_from_db(self) -> dict[str, Any]:
    return run_async(_rebuild_redbeat_from_db())


async def _rebuild_redbeat_from_db() -> dict[str, Any]:
    sm = async_session_factory()
    async with sm() as db:
        svc = SchedulingService(db)
        count = await svc.rebuild_redbeat_from_db()
    return {"reregistered": count}
