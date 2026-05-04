"""Static beat schedules. Imported by celery_app for side-effect.

Dynamic schedules live in `scheduled_job` rows and are registered via redbeat
from `SchedulingService._register_redbeat` at create-time, plus reconciled by
`rebuild_redbeat_from_db` on startup.
"""

from __future__ import annotations

from celery.schedules import schedule as celery_schedule

from legalbot.core.config import get_settings
from legalbot.workers.celery_app import celery_app

settings = get_settings()

celery_app.conf.beat_schedule = {
    "poll-mailboxes": {
        "task": "legalbot.workers.ingest.poll_mailboxes",
        "schedule": celery_schedule(run_every=settings.POLL_INTERVAL_SEC),
    },
    "dispatch-ready-jobs": {
        "task": "legalbot.workers.janitors.dispatch_ready_jobs",
        "schedule": celery_schedule(run_every=settings.DISPATCH_INTERVAL_SEC),
    },
    "reap-stuck-jobs": {
        "task": "legalbot.workers.janitors.reap_stuck_jobs",
        "schedule": celery_schedule(run_every=300),
    },
    "expire-stalled-interrupts": {
        "task": "legalbot.workers.janitors.expire_stalled_interrupts",
        "schedule": celery_schedule(run_every=300),
    },
    "rebuild-redbeat-from-db": {
        "task": "legalbot.workers.janitors.rebuild_redbeat_from_db",
        "schedule": celery_schedule(run_every=3600),
    },
}
