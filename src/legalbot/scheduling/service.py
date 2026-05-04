"""SchedulingService — CRUD over `scheduled_job` + redbeat entries.

The DB row is authoritative; redbeat entries are regenerated from it on startup.
On failure between DB and redbeat writes, the background `rebuild_redbeat_from_db`
reconciler eventually re-aligns them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from legalbot.core.logging import get_logger
from legalbot.db.models import ScheduledJob
from legalbot.db.types import ScheduledJobScheduleKind, ScheduledJobStatus
from legalbot.scheduling.models import JOB_KIND_REGISTRY, ScheduleSpec

log = get_logger(__name__)


class UnknownJobKind(KeyError):
    pass


def _entry_key(job_id: uuid.UUID) -> str:
    return f"legalbot:scheduled:{job_id}"


class SchedulingService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _validate_kind(self, kind: str) -> None:
        if kind not in JOB_KIND_REGISTRY:
            raise UnknownJobKind(f"unknown scheduled-job kind: {kind}")

    async def schedule_one_shot(
        self,
        *,
        kind: str,
        payload: dict[str, Any],
        run_at: datetime,
        owner_user_id: str,
        session_id: uuid.UUID | None = None,
        job_id: uuid.UUID | None = None,
        run_id: uuid.UUID | None = None,
        parent_run_id: uuid.UUID | None = None,
    ) -> ScheduledJob:
        self._validate_kind(kind)
        row = ScheduledJob(
            id=uuid.uuid4(),
            kind=kind,
            payload=payload,
            schedule_kind=str(ScheduledJobScheduleKind.one_shot),
            schedule_spec={"run_at": run_at.isoformat()},
            status=str(ScheduledJobStatus.active),
            next_run_at=run_at,
            owner_user_id=owner_user_id,
            session_id=session_id,
            job_id=job_id,
            run_id=run_id,
            parent_run_id=parent_run_id,
        )
        row.redbeat_entry_key = _entry_key(row.id)
        self.db.add(row)
        await self.db.flush()
        await self._register_redbeat(row)
        return row

    async def schedule_recurring(
        self,
        *,
        kind: str,
        payload: dict[str, Any],
        spec: ScheduleSpec,
        owner_user_id: str,
        session_id: uuid.UUID | None = None,
        job_id: uuid.UUID | None = None,
        run_id: uuid.UUID | None = None,
    ) -> ScheduledJob:
        self._validate_kind(kind)
        if spec.kind not in {"cron", "interval"}:
            raise ValueError("spec.kind must be 'cron' or 'interval' for recurring")

        row = ScheduledJob(
            id=uuid.uuid4(),
            kind=kind,
            payload=payload,
            schedule_kind=spec.kind,
            schedule_spec=spec.to_dict(),
            status=str(ScheduledJobStatus.active),
            owner_user_id=owner_user_id,
            session_id=session_id,
            job_id=job_id,
            run_id=run_id,
        )
        row.redbeat_entry_key = _entry_key(row.id)
        self.db.add(row)
        await self.db.flush()
        await self._register_redbeat(row)
        return row

    async def pause(self, job_id: uuid.UUID) -> None:
        await self.db.execute(
            update(ScheduledJob)
            .where(ScheduledJob.id == job_id)
            .values(status=str(ScheduledJobStatus.paused))
        )
        await self._remove_redbeat(job_id)

    async def resume(self, job_id: uuid.UUID) -> None:
        await self.db.execute(
            update(ScheduledJob)
            .where(ScheduledJob.id == job_id)
            .values(status=str(ScheduledJobStatus.active))
        )
        row = (
            await self.db.execute(select(ScheduledJob).where(ScheduledJob.id == job_id))
        ).scalar_one()
        await self._register_redbeat(row)

    async def cancel(self, job_id: uuid.UUID) -> None:
        await self.db.execute(
            update(ScheduledJob)
            .where(ScheduledJob.id == job_id)
            .values(status=str(ScheduledJobStatus.cancelled))
        )
        await self._remove_redbeat(job_id)

    async def mark_fired(
        self, job_id: uuid.UUID, *, last_result: dict[str, Any] | None, when: datetime | None = None
    ) -> None:
        values: dict[str, Any] = {"last_run_at": when or datetime.now(UTC)}
        if last_result is not None:
            values["last_result"] = last_result
        await self.db.execute(
            update(ScheduledJob).where(ScheduledJob.id == job_id).values(**values)
        )

    async def expire_one_shot(self, job_id: uuid.UUID) -> None:
        await self.db.execute(
            update(ScheduledJob)
            .where(ScheduledJob.id == job_id)
            .values(status=str(ScheduledJobStatus.expired))
        )
        await self._remove_redbeat(job_id)

    async def cascade_cancel(
        self,
        *,
        run_id: uuid.UUID | None = None,
        session_id: uuid.UUID | None = None,
    ) -> int:
        q = select(ScheduledJob).where(ScheduledJob.status == str(ScheduledJobStatus.active))
        if run_id is not None:
            q = q.where(ScheduledJob.run_id == run_id)
        if session_id is not None:
            q = q.where(ScheduledJob.session_id == session_id)
        rows = (await self.db.execute(q)).scalars().all()
        for row in rows:
            await self.cancel(row.id)
        return len(rows)

    async def _register_redbeat(self, row: ScheduledJob) -> None:
        try:
            from celery.schedules import crontab
            from celery.schedules import schedule as celery_schedule
            from redbeat import RedBeatSchedulerEntry

            from legalbot.workers.celery_app import celery_app

            if row.schedule_kind == ScheduledJobScheduleKind.cron:
                spec = row.schedule_spec.get("expression", "*/15 * * * *").split()
                # crontab(minute, hour, day_of_week, day_of_month, month_of_year)
                args = spec + ["*"] * (5 - len(spec))
                minute, hour, day_of_month, month_of_year, day_of_week = args[:5]
                sched = crontab(
                    minute=minute,
                    hour=hour,
                    day_of_month=day_of_month,
                    month_of_year=month_of_year,
                    day_of_week=day_of_week,
                )
            elif row.schedule_kind == ScheduledJobScheduleKind.interval:
                sched = celery_schedule(run_every=row.schedule_spec.get("interval_seconds", 60))
            else:  # one_shot
                run_at = datetime.fromisoformat(row.schedule_spec["run_at"])
                delta = max((run_at - datetime.now(UTC)).total_seconds(), 1.0)
                sched = celery_schedule(run_every=delta)

            entry = RedBeatSchedulerEntry(
                name=row.redbeat_entry_key,
                task="legalbot.workers.scheduled.run_scheduled_job",
                schedule=sched,
                args=(str(row.id),),
                app=celery_app,
            )
            entry.save()
        except Exception as e:
            log.warning("scheduled.redbeat.register_failed", job_id=str(row.id), error=str(e))

    async def _remove_redbeat(self, job_id: uuid.UUID) -> None:
        try:
            from redbeat import RedBeatSchedulerEntry

            from legalbot.workers.celery_app import celery_app

            RedBeatSchedulerEntry(
                name=_entry_key(job_id),
                task="legalbot.workers.scheduled.run_scheduled_job",
                schedule=None,  # type: ignore[arg-type]
                app=celery_app,
            ).delete()
        except Exception as e:  # pragma: no cover
            log.warning("scheduled.redbeat.remove_failed", job_id=str(job_id), error=str(e))

    async def rebuild_redbeat_from_db(self) -> int:
        rows = (
            (
                await self.db.execute(
                    select(ScheduledJob).where(
                        ScheduledJob.status == str(ScheduledJobStatus.active)
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            await self._register_redbeat(row)
        return len(rows)
