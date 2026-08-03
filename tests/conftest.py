"""Shared pytest fixtures: in-memory SQLite DB + FakeChatModel + frozen clock."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from legalbot.db.base import Base


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    database_url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    engine = create_async_engine(database_url, future=True)
    async with engine.begin() as conn:
        if database_url.startswith("postgresql"):
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        yield session
    await engine.dispose()


class FakeChatModel:
    """Trivial fake chat model returning a single canned response."""

    def __init__(self, response: str = "ok") -> None:
        self.response = response
        self.calls: list[Any] = []

    async def ainvoke(self, inputs: Any, config: dict | None = None) -> dict:
        self.calls.append(inputs)
        return {"messages": [{"role": "assistant", "content": self.response}]}


@pytest.fixture
def fake_chat_model() -> FakeChatModel:
    return FakeChatModel()


class FrozenClock:
    def __init__(self) -> None:
        from datetime import datetime

        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def advance(self, *, seconds: int) -> None:
        from datetime import timedelta

        self.now = self.now + timedelta(seconds=seconds)


@pytest.fixture
def frozen_clock() -> FrozenClock:
    return FrozenClock()
