"""FastAPI lifespan: opens shared psycopg pool + langgraph checkpointer/store."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI

from legalbot.core.logging import configure_logging, get_logger
from legalbot.core.otel import configure_tracing
from legalbot.db.session import psycopg_pool, shutdown_engine

if TYPE_CHECKING:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.store.postgres.aio import AsyncPostgresStore

log = get_logger(__name__)

_checkpointer: AsyncPostgresSaver | None = None
_store: AsyncPostgresStore | None = None


async def get_checkpointer() -> AsyncPostgresSaver:
    global _checkpointer
    if _checkpointer is None:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        pool = await psycopg_pool()
        _checkpointer = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
        await _checkpointer.setup()
    return _checkpointer


async def get_store() -> AsyncPostgresStore:
    global _store
    if _store is None:
        from langgraph.store.postgres.aio import AsyncPostgresStore

        from legalbot.core.config import get_settings

        settings = get_settings()
        pool = await psycopg_pool()
        index_cfg = None
        if settings.OPENAI_API_KEY is not None:
            index_cfg = {
                "embed": settings.EMBED_MODEL,
                "dims": settings.EMBED_DIMS,
                "fields": ["content", "task", "approach"],
            }
        _store = AsyncPostgresStore(pool, index=index_cfg)  # type: ignore[arg-type]
        await _store.setup()
    return _store


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    configure_tracing()
    log.info("lifespan.startup")
    await psycopg_pool()
    await get_checkpointer()
    await get_store()
    try:
        yield
    finally:
        log.info("lifespan.shutdown")
        await shutdown_engine()
