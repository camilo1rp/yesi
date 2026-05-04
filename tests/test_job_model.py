"""Validate the allowed-transition matrix statically."""

from __future__ import annotations

from legalbot.db.types import JobState
from legalbot.jobs.models import ALLOWED_TRANSITIONS


def test_archived_is_terminal() -> None:
    for src, _ in ALLOWED_TRANSITIONS:
        assert src != JobState.archived, "archived must be terminal"


def test_ready_can_go_dispatched() -> None:
    assert (JobState.ready, JobState.dispatched) in ALLOWED_TRANSITIONS


def test_awaiting_human_resumes_processing() -> None:
    assert (JobState.awaiting_human, JobState.processing) in ALLOWED_TRANSITIONS


def test_failed_can_retry() -> None:
    assert (JobState.failed, JobState.ready) in ALLOWED_TRANSITIONS


def test_intake_cannot_skip_to_completed() -> None:
    assert (JobState.intake, JobState.completed) not in ALLOWED_TRANSITIONS
