"""Tests for InterruptService._resume_value, especially HITL tool_approval mapping.

``HumanInTheLoopMiddleware`` reads the resume value as
``interrupt(...)["decisions"]`` and dispatches per ``decision["type"]``. The
service must therefore emit ``{"decisions": [{"type": ..., ...}]}`` for
``tool_approval``, not the legacy flat ``{decision, edited_args, reason}``
shape.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from legalbot.interrupts.schemas import (
    InterruptResolutionInfo,
    InterruptResolutionReview,
    InterruptResolutionTool,
)
from legalbot.interrupts.service import InterruptService


def _request(payload: dict[str, Any] | None = None) -> Any:
    """Build a stand-in for an ``InterruptRequest`` row carrying ``payload`` only."""
    return SimpleNamespace(payload=payload or {})


def test_tool_approval_approve_emits_hitl_decisions_envelope() -> None:
    svc = InterruptService.__new__(InterruptService)
    request = _request({"action_requests": [{"name": "send_draft", "args": {"draft_id": "d1"}}]})
    resolution = InterruptResolutionTool(decision="approve")

    value = svc._resume_value(resolution, request)

    assert value == {"decisions": [{"type": "approve"}]}


def test_tool_approval_reject_includes_reason_as_message() -> None:
    svc = InterruptService.__new__(InterruptService)
    request = _request({"action_requests": [{"name": "send_draft", "args": {}}]})
    resolution = InterruptResolutionTool(decision="reject", reason="not ready to send")

    value = svc._resume_value(resolution, request)

    assert value == {"decisions": [{"type": "reject", "message": "not ready to send"}]}


def test_tool_approval_reject_without_reason_omits_message() -> None:
    svc = InterruptService.__new__(InterruptService)
    request = _request({"action_requests": [{"name": "send_draft", "args": {}}]})
    resolution = InterruptResolutionTool(decision="reject")

    value = svc._resume_value(resolution, request)

    assert value == {"decisions": [{"type": "reject"}]}


def test_tool_approval_edit_carries_tool_name_from_persisted_payload() -> None:
    svc = InterruptService.__new__(InterruptService)
    request = _request({"action_requests": [{"name": "send_draft", "args": {"draft_id": "d1"}}]})
    resolution = InterruptResolutionTool(
        decision="edit", edited_args={"draft_id": "d1", "subject": "Updated"}
    )

    value = svc._resume_value(resolution, request)

    assert value == {
        "decisions": [
            {
                "type": "edit",
                "edited_action": {
                    "name": "send_draft",
                    "args": {"draft_id": "d1", "subject": "Updated"},
                },
            }
        ]
    }


def test_tool_approval_edit_without_request_falls_back_to_empty_name() -> None:
    """Defensive: if the persisted payload is missing, edit still produces a valid envelope."""
    svc = InterruptService.__new__(InterruptService)
    resolution = InterruptResolutionTool(decision="edit", edited_args={"x": 1})

    value = svc._resume_value(resolution, None)

    assert value == {
        "decisions": [{"type": "edit", "edited_action": {"name": "", "args": {"x": 1}}}]
    }


def test_information_request_answer_is_returned_directly() -> None:
    svc = InterruptService.__new__(InterruptService)
    resolution = InterruptResolutionInfo(answer={"case_number": "A-123"})

    value = svc._resume_value(resolution, _request())

    assert value == {"case_number": "A-123"}


def test_information_request_skip_returns_skipped_envelope() -> None:
    svc = InterruptService.__new__(InterruptService)
    resolution = InterruptResolutionInfo(skipped=True, reason="not applicable")

    value = svc._resume_value(resolution, _request())

    assert value == {"skipped": True, "reason": "not applicable"}


def test_review_draft_resume_value_unchanged() -> None:
    svc = InterruptService.__new__(InterruptService)
    resolution = InterruptResolutionReview(decision="revise", revision_notes="tone it down")

    value = svc._resume_value(resolution, _request())

    assert value == {"decision": "revise", "revision_notes": "tone it down"}
