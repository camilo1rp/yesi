"""FastAPI routers."""

from legalbot.api.routers import (
    admin,
    artifacts,
    ingestion,
    interrupts,
    jobs,
    mailboxes,
    runs,
    scheduled_jobs,
    sessions,
)

__all__ = [
    "admin",
    "artifacts",
    "ingestion",
    "interrupts",
    "jobs",
    "mailboxes",
    "runs",
    "scheduled_jobs",
    "sessions",
]
