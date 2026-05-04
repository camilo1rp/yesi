"""Job dispatcher: picks `ready` jobs under a concurrency budget and enqueues the agent run."""

from __future__ import annotations

import uuid
from typing import Any

import redis.asyncio as redis_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from legalbot.core.config import get_settings
from legalbot.core.logging import get_logger
from legalbot.db.models import ProcessingJob
from legalbot.db.types import JobState
from legalbot.jobs.models import InvalidJobTransition
from legalbot.jobs.service import JobService

log = get_logger(__name__)

IN_FLIGHT_KEY = "legalbot:in_flight_jobs"


class ConcurrencyBudget:
    """Tracks in-flight runs via a Redis set. `MAX_CONCURRENT_RUNS - SCARD(in_flight_jobs)`."""

    def __init__(self, client: redis_asyncio.Redis | None = None) -> None:
        self.client = client

    async def _get_client(self) -> redis_asyncio.Redis:
        if self.client is None:
            settings = get_settings()
            self.client = redis_asyncio.from_url(settings.REDIS_URL, decode_responses=True)
        return self.client

    async def current(self) -> int:
        settings = get_settings()
        client = await self._get_client()
        in_flight = int(await client.scard(IN_FLIGHT_KEY))
        return max(0, settings.MAX_CONCURRENT_RUNS - in_flight)

    async def acquire(self, job_id: uuid.UUID) -> None:
        client = await self._get_client()
        await client.sadd(IN_FLIGHT_KEY, str(job_id))

    async def release(self, job_id: uuid.UUID) -> None:
        client = await self._get_client()
        await client.srem(IN_FLIGHT_KEY, str(job_id))


async def pick_ready_jobs(db: AsyncSession, *, limit: int) -> list[ProcessingJob]:
    """`SELECT ... FOR UPDATE SKIP LOCKED LIMIT :limit` with per-owner fairness.

    The window function is declared in the subquery to prevent one owner's backlog from
    starving the others.
    """

    if limit <= 0:
        return []

    stmt = (
        select(ProcessingJob)
        .where(ProcessingJob.state == str(JobState.ready))
        .order_by(ProcessingJob.priority.desc(), ProcessingJob.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    result = await db.execute(stmt)
    return list(result.scalars())


async def dispatch_once(db: AsyncSession, *, budget: ConcurrencyBudget) -> list[uuid.UUID]:
    """One pass of dispatch. Returns the list of dispatched job_ids."""
    b = await budget.current()
    if b <= 0:
        return []
    rows = await pick_ready_jobs(db, limit=b)
    dispatched: list[uuid.UUID] = []
    svc = JobService(db)
    for job in rows:
        try:
            await svc.transition(job.id, from_state=JobState.ready, to_state=JobState.dispatched)
        except InvalidJobTransition:
            continue
        dispatched.append(job.id)
    await db.commit()
    for job_id in dispatched:
        await budget.acquire(job_id)
        enqueue_run_pipeline(job_id)
    return dispatched


def enqueue_run_pipeline(job_id: uuid.UUID) -> Any:
    """Late-bound import so this module stays importable without Celery configured."""
    from legalbot.workers.runs import run_pipeline

    return run_pipeline.delay(str(job_id))
