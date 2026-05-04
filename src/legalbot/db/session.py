"""Shared async engine + session factory + psycopg connection pool."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, cast

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from legalbot.core.config import get_settings

if TYPE_CHECKING:
    from psycopg import AsyncConnection
    from psycopg.rows import DictRow
    from psycopg_pool import AsyncConnectionPool

    PsycopgPool = AsyncConnectionPool[AsyncConnection[DictRow]]


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_psycopg_pool: PsycopgPool | None = None


def engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.DATABASE_URL,
            pool_pre_ping=True,
            pool_size=settings.DB_POOL_MIN,
            max_overflow=settings.DB_POOL_MAX - settings.DB_POOL_MIN,
        )
    return _engine


def async_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(engine(), expire_on_commit=False, autoflush=False)
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with async_session_factory()() as session:
        yield session


async def shutdown_engine() -> None:
    global _engine, _session_factory, _psycopg_pool
    if _psycopg_pool is not None:
        await _psycopg_pool.close()
        _psycopg_pool = None
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


async def psycopg_pool() -> PsycopgPool:
    """Lazily-created psycopg AsyncConnectionPool for LangGraph checkpointer + store.

    Blueprint requires: autocommit=True, prepare_threshold=0, row_factory=dict_row.
    """

    global _psycopg_pool
    if _psycopg_pool is None or getattr(_psycopg_pool, "closed", False):
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool

        settings = get_settings()
        _psycopg_pool = cast(
            "PsycopgPool",
            AsyncConnectionPool(
                conninfo=settings.DATABASE_URL_PSYCOPG,
                kwargs={
                    "autocommit": True,
                    "prepare_threshold": 0,
                    "row_factory": dict_row,
                },
                min_size=settings.DB_POOL_MIN,
                max_size=settings.DB_POOL_MAX,
                open=False,
            ),
        )
        await _psycopg_pool.open()
    return _psycopg_pool
