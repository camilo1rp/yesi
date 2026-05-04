"""State machine for `processing_job`."""

from __future__ import annotations

from legalbot.db.types import JobState


class InvalidJobTransition(RuntimeError):
    """Raised when JobService.transition is called with an illegal transition."""


# (from_state, to_state) pairs that are legal. Anything missing is rejected.
ALLOWED_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        (JobState.intake, JobState.ready),
        (JobState.intake, JobState.failed),
        (JobState.ready, JobState.dispatched),
        (JobState.ready, JobState.archived),
        (JobState.dispatched, JobState.processing),
        (JobState.dispatched, JobState.ready),  # sweeper reverting stale lease
        (JobState.dispatched, JobState.failed),
        (JobState.processing, JobState.awaiting_human),
        (JobState.processing, JobState.completed),
        (JobState.processing, JobState.failed),
        (JobState.awaiting_human, JobState.processing),
        (JobState.awaiting_human, JobState.failed),
        (JobState.awaiting_human, JobState.completed),
        (JobState.failed, JobState.ready),  # operator retry
        (JobState.failed, JobState.archived),
        (JobState.completed, JobState.archived),
    }
)


def is_terminal(state: str) -> bool:
    return state in {JobState.completed, JobState.failed, JobState.archived}
