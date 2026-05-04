from __future__ import annotations

from typing import Any

import pytest

from legalbot.agents.subagents import _StageCheckpointNamespace


class CapturingRunnable:
    def __init__(self) -> None:
        self.config: dict[str, Any] | None = None

    async def ainvoke(
        self,
        input: dict[str, Any],
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.config = config
        return {"messages": []}


@pytest.mark.asyncio
async def test_stage_checkpoint_namespace_applies_thread_and_namespace() -> None:
    runnable = CapturingRunnable()
    wrapped = _StageCheckpointNamespace(runnable, "analyze")

    await wrapped.ainvoke(
        {"graph_thread_id": "thread-1"},
        config={"configurable": {"user_id": "user-1"}},
    )

    cfg = runnable.config
    assert cfg is not None
    assert cfg.get("recursion_limit") == 10
    conf = cfg["configurable"]
    assert conf["user_id"] == "user-1"
    assert conf["thread_id"] == "thread-1"
    ns = conf["checkpoint_ns"]
    assert ns.startswith("analyze:norun:")
    assert len(ns.split(":")) == 3
