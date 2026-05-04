"""Tests for _persist_interrupt_from_checkpoint in the runs worker."""

from __future__ import annotations

import contextlib
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from legalbot.db.types import InterruptKind
from legalbot.workers.runs import _persist_interrupt_from_checkpoint


class FakeInterrupt:
    def __init__(self, value: Any) -> None:
        self.value = value


class FakeGraph:
    def __init__(self, interrupts: list[Any] | None = None) -> None:
        self.state = SimpleNamespace(interrupts=interrupts or [])

    async def aget_state(self, config: dict[str, Any]) -> Any:
        return self.state


def _config() -> dict[str, Any]:
    return {"configurable": {"thread_id": "thread-1"}}


def _mock_session_factory(mock_db: AsyncMock) -> MagicMock:
    @contextlib.asynccontextmanager
    async def _session_ctx():
        yield mock_db

    mock_sm = MagicMock(side_effect=lambda: _session_ctx())
    mock_factory = MagicMock(return_value=mock_sm)
    return mock_factory


@pytest.mark.asyncio
async def test_creates_interrupt_row_for_information_request() -> None:
    envelope = {
        "kind": "information_request",
        "payload": {
            "prompt": "What is the case number?",
            "schema": {"type": "object", "properties": {"answer": {"type": "string"}}},
            "rationale": "Need case number",
            "blocking_reason": "Cannot proceed",
        },
    }
    graph = FakeGraph(interrupts=[FakeInterrupt(envelope)])
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()

    mock_svc = AsyncMock()
    mock_svc.current_for_session.return_value = None
    mock_db = AsyncMock()

    with (
        patch(
            "legalbot.workers.runs.async_session_factory",
            _mock_session_factory(mock_db),
        ),
        patch("legalbot.interrupts.service.InterruptService", return_value=mock_svc),
    ):
        await _persist_interrupt_from_checkpoint(graph, _config(), session_id, run_id)

    mock_svc.current_for_session.assert_awaited_once_with(session_id)
    mock_svc.open.assert_awaited_once()
    call_kwargs = mock_svc.open.call_args.kwargs
    assert call_kwargs["session_id"] == session_id
    assert call_kwargs["run_id"] == run_id
    assert call_kwargs["kind"] == InterruptKind.information_request
    assert call_kwargs["payload"] == envelope["payload"]
    assert call_kwargs["schema"] == envelope["payload"]["schema"]


@pytest.mark.asyncio
async def test_skips_when_interrupt_row_already_exists() -> None:
    envelope = {"kind": "information_request", "payload": {"prompt": "?"}}
    graph = FakeGraph(interrupts=[FakeInterrupt(envelope)])
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()

    mock_svc = AsyncMock()
    mock_svc.current_for_session.return_value = SimpleNamespace(id=uuid.uuid4())
    mock_db = AsyncMock()

    with (
        patch(
            "legalbot.workers.runs.async_session_factory",
            _mock_session_factory(mock_db),
        ),
        patch("legalbot.interrupts.service.InterruptService", return_value=mock_svc),
    ):
        await _persist_interrupt_from_checkpoint(graph, _config(), session_id, run_id)

    mock_svc.open.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_when_no_interrupts_on_checkpoint() -> None:
    graph = FakeGraph(interrupts=[])
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()

    mock_db = AsyncMock()

    with patch(
        "legalbot.workers.runs.async_session_factory",
        _mock_session_factory(mock_db),
    ) as mock_factory:
        await _persist_interrupt_from_checkpoint(graph, _config(), session_id, run_id)

    mock_factory.assert_not_called()


@pytest.mark.asyncio
async def test_skips_when_envelope_is_not_a_dict() -> None:
    graph = FakeGraph(interrupts=[FakeInterrupt("not-a-dict")])
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()

    mock_svc = AsyncMock()
    mock_svc.current_for_session.return_value = None
    mock_db = AsyncMock()

    with (
        patch(
            "legalbot.workers.runs.async_session_factory",
            _mock_session_factory(mock_db),
        ),
        patch("legalbot.interrupts.service.InterruptService", return_value=mock_svc),
    ):
        await _persist_interrupt_from_checkpoint(graph, _config(), session_id, run_id)

    mock_svc.open.assert_not_awaited()


@pytest.mark.asyncio
async def test_defaults_to_information_request_for_unknown_kind() -> None:
    envelope = {"kind": "unknown_kind", "payload": {"prompt": "?"}}
    graph = FakeGraph(interrupts=[FakeInterrupt(envelope)])
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()

    mock_svc = AsyncMock()
    mock_svc.current_for_session.return_value = None
    mock_db = AsyncMock()

    with (
        patch(
            "legalbot.workers.runs.async_session_factory",
            _mock_session_factory(mock_db),
        ),
        patch("legalbot.interrupts.service.InterruptService", return_value=mock_svc),
    ):
        await _persist_interrupt_from_checkpoint(graph, _config(), session_id, run_id)

    call_kwargs = mock_svc.open.call_args.kwargs
    assert call_kwargs["kind"] == InterruptKind.information_request


@pytest.mark.asyncio
async def test_translates_hitl_envelope_to_tool_approval() -> None:
    """HumanInTheLoopMiddleware emits {action_requests, review_configs} (no kind/payload).

    The persistence layer must translate that into a tool_approval row with the
    action requests preserved so the UI can render the message.
    """
    envelope = {
        "action_requests": [
            {
                "name": "send_draft",
                "args": {"draft_id": "22fe31b8-24fb-40c2-a5ac-80fe8db77dd1"},
                "description": "Tool execution requires approval\n\nTool: send_draft\nArgs: ...",
            }
        ],
        "review_configs": [
            {
                "action_name": "send_draft",
                "allowed_decisions": ["approve", "reject"],
                "args_schema": {
                    "type": "object",
                    "properties": {"draft_id": {"type": "string"}},
                    "required": ["draft_id"],
                },
            }
        ],
    }
    graph = FakeGraph(interrupts=[FakeInterrupt(envelope)])
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()

    mock_svc = AsyncMock()
    mock_svc.current_for_session.return_value = None
    mock_db = AsyncMock()

    with (
        patch(
            "legalbot.workers.runs.async_session_factory",
            _mock_session_factory(mock_db),
        ),
        patch("legalbot.interrupts.service.InterruptService", return_value=mock_svc),
    ):
        await _persist_interrupt_from_checkpoint(graph, _config(), session_id, run_id)

    call_kwargs = mock_svc.open.call_args.kwargs
    assert call_kwargs["kind"] == InterruptKind.tool_approval
    assert call_kwargs["payload"]["action_requests"] == envelope["action_requests"]
    assert call_kwargs["payload"]["review_configs"] == envelope["review_configs"]
    assert call_kwargs["schema"] == envelope["review_configs"][0]["args_schema"]


@pytest.mark.asyncio
async def test_hitl_envelope_without_args_schema_has_null_schema() -> None:
    envelope = {
        "action_requests": [{"name": "send_draft", "args": {}, "description": "..."}],
        "review_configs": [
            {"action_name": "send_draft", "allowed_decisions": ["approve", "reject"]}
        ],
    }
    graph = FakeGraph(interrupts=[FakeInterrupt(envelope)])
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()

    mock_svc = AsyncMock()
    mock_svc.current_for_session.return_value = None
    mock_db = AsyncMock()

    with (
        patch(
            "legalbot.workers.runs.async_session_factory",
            _mock_session_factory(mock_db),
        ),
        patch("legalbot.interrupts.service.InterruptService", return_value=mock_svc),
    ):
        await _persist_interrupt_from_checkpoint(graph, _config(), session_id, run_id)

    call_kwargs = mock_svc.open.call_args.kwargs
    assert call_kwargs["kind"] == InterruptKind.tool_approval
    assert call_kwargs["schema"] is None


@pytest.mark.asyncio
async def test_handles_review_draft_kind() -> None:
    envelope = {
        "kind": "review_draft",
        "payload": {
            "draft_id": "draft-1",
            "preview": "Dear client...",
            "open_questions": [],
        },
    }
    graph = FakeGraph(interrupts=[FakeInterrupt(envelope)])
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()

    mock_svc = AsyncMock()
    mock_svc.current_for_session.return_value = None
    mock_db = AsyncMock()

    with (
        patch(
            "legalbot.workers.runs.async_session_factory",
            _mock_session_factory(mock_db),
        ),
        patch("legalbot.interrupts.service.InterruptService", return_value=mock_svc),
    ):
        await _persist_interrupt_from_checkpoint(graph, _config(), session_id, run_id)

    call_kwargs = mock_svc.open.call_args.kwargs
    assert call_kwargs["kind"] == InterruptKind.review_draft
    assert call_kwargs["payload"]["draft_id"] == "draft-1"
    assert call_kwargs["schema"] is None
