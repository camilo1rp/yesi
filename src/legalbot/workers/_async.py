"""Helper to run async coroutines from sync Celery tasks."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

T = TypeVar("T")


def run_async(coro: Awaitable[T]) -> T:
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            raise RuntimeError("cannot nest running loops")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)
