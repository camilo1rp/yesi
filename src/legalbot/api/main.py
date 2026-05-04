"""FastAPI application entrypoint."""

from __future__ import annotations

from fastapi import FastAPI
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import PlainTextResponse, Response

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
from legalbot.core.lifespan import lifespan


def create_app() -> FastAPI:
    app = FastAPI(
        title="Legalbot Automation",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/healthz", response_class=PlainTextResponse)
    async def healthz() -> str:
        return "ok"

    @app.get("/readyz", response_class=PlainTextResponse)
    async def readyz() -> str:
        return "ready"

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    app.include_router(mailboxes.router, prefix="/api/mailboxes", tags=["mailboxes"])
    app.include_router(ingestion.router, prefix="/api/ingestion-items", tags=["ingestion"])
    app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])
    app.include_router(sessions.router, prefix="/api/sessions", tags=["sessions"])
    app.include_router(interrupts.router, prefix="/api/sessions", tags=["interrupts"])
    app.include_router(artifacts.router, prefix="/api/sessions", tags=["artifacts"])
    app.include_router(runs.router, prefix="/api/runs", tags=["runs"])
    app.include_router(scheduled_jobs.router, prefix="/api/scheduled-jobs", tags=["scheduled-jobs"])
    app.include_router(admin.router, prefix="/api/admin", tags=["admin"])

    return app


app = create_app()
