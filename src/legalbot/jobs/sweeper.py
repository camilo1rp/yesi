"""reap_stuck_jobs: revert stale dispatched leases and fail hung processing rows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from legalbot.core.config import get_settings
from legalbot.core.logging import get_logger
from legalbot.db.models import ProcessingJob
from legalbot.db.types import JobState

log = get_logger(__name__)


async def reap_stuck(db: AsyncSession) -> dict[str, int]:
    settings = get_settings()
    now = datetime.now(UTC)

    dispatch_deadline = now - timedelta(seconds=settings.DISPATCH_LEASE_SEC)
    processing_deadline = now - timedelta(seconds=settings.PROCESSING_LEASE_SEC)

    # 1. Revert stale dispatched leases back to ready.
    rev = await db.execute(
        update(ProcessingJob)
        .where(
            ProcessingJob.state == str(JobState.dispatched),
            ProcessingJob.dispatched_at < dispatch_deadline,
        )
        .values(state=str(JobState.ready), dispatched_at=None)
        .returning(ProcessingJob.id)
    )
    reverted = len(rev.scalars().all())

    # 2. Fail processing rows that have no checkpoint progress past the lease.
    fail = await db.execute(
        update(ProcessingJob)
        .where(
            ProcessingJob.state == str(JobState.processing),
            ProcessingJob.processing_started_at < processing_deadline,
        )
        .values(
            state=str(JobState.failed),
            last_error="processing lease expired without progress",
            error_kind="stuck",
            finished_at=now,
        )
        .returning(ProcessingJob.id)
    )
    failed = len(fail.scalars().all())

    await db.commit()
    if reverted or failed:
        log.warning("sweeper.reaped", reverted=reverted, failed=failed)
    return {"reverted": reverted, "failed": failed}
