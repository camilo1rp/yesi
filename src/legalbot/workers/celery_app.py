"""Celery application + Beat configuration (celery-redbeat scheduler)."""

from __future__ import annotations

from celery import Celery

from legalbot.core.config import get_settings


def _build_app() -> Celery:
    settings = get_settings()
    app = Celery(
        "legalbot",
        broker=settings.CELERY_BROKER_URL,
        backend=settings.CELERY_RESULT_BACKEND,
    )
    app.conf.update(
        task_default_queue="default",
        task_default_exchange="default",
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        task_track_started=True,
        result_extended=True,
        timezone="UTC",
        enable_utc=True,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        beat_scheduler="redbeat.RedBeatScheduler",
        redbeat_redis_url=settings.CELERY_REDBEAT_URL,
        redbeat_lock_key=settings.CELERY_REDBEAT_LOCK_KEY,
        redbeat_lock_timeout=300,
    )
    app.autodiscover_tasks(
        [
            "legalbot.workers.ingest",
            "legalbot.workers.runs",
            "legalbot.workers.scheduled",
            "legalbot.workers.janitors",
            "legalbot.workers.graph",
        ]
    )
    return app


celery_app = _build_app()

# Register static beat schedules after the app is fully constructed to avoid
# a circular import when `schedules_static` imports `celery_app`.
from legalbot.workers import (  # noqa: E402,F401
    graph,
    ingest,
    janitors,
    runs,
    scheduled,
    schedules_static,
)
