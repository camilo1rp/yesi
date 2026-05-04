"""Handlers are imported for side-effect: each file registers its kind."""

from legalbot.scheduling.handlers import (  # noqa: F401
    poll_mailbox_once,
    reminder,
    replay_trigger,
)
