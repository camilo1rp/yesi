"""Validate discriminated-union interrupt resolution schemas."""

from __future__ import annotations

from legalbot.interrupts.schemas import (
    InterruptResolutionInfo,
    InterruptResolutionReview,
    InterruptResolutionTool,
)


def test_tool_resolution_requires_decision() -> None:
    r = InterruptResolutionTool(decision="approve")
    assert r.kind == "tool_approval"
    assert r.decision == "approve"


def test_info_resolution_carries_answer() -> None:
    r = InterruptResolutionInfo(answer={"case_number": "A123"})
    assert r.kind == "information_request"
    assert r.answer == {"case_number": "A123"}


def test_review_resolution_supports_revise() -> None:
    r = InterruptResolutionReview(decision="revise", revision_notes="tone it down")
    assert r.kind == "review_draft"
    assert r.decision == "revise"
