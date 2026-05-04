"""Resume worker compatibility behavior."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from legalbot.workers.runs import _ensure_resume_has_attachments


class FakeGraph:
    def __init__(self, values: dict[str, Any]) -> None:
        self.state = SimpleNamespace(values=values)
        self.updates: list[dict[str, Any]] = []

    async def aget_state(self, config: dict[str, Any]) -> Any:
        return self.state

    async def aupdate_state(self, *, config: dict[str, Any], values: dict[str, Any]) -> None:
        self.updates.append(values)


@pytest.mark.parametrize("checkpoint_values", [{}, {"has_attachments": None}])
@pytest.mark.parametrize("computed_has_attachments", [False, True])
async def test_resume_injects_missing_has_attachments(
    checkpoint_values: dict[str, Any],
    computed_has_attachments: bool,
) -> None:
    graph = FakeGraph(checkpoint_values)

    await _ensure_resume_has_attachments(
        graph=graph,
        config={"configurable": {"thread_id": "thread-1"}},
        run_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        has_attachments=computed_has_attachments,
    )

    assert graph.updates == [{"has_attachments": computed_has_attachments}]


@pytest.mark.parametrize("checkpoint_value", [False, True])
async def test_resume_preserves_existing_has_attachments(checkpoint_value: bool) -> None:
    graph = FakeGraph({"has_attachments": checkpoint_value})

    await _ensure_resume_has_attachments(
        graph=graph,
        config={"configurable": {"thread_id": "thread-1"}},
        run_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        has_attachments=not checkpoint_value,
    )

    assert graph.updates == []
