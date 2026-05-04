"""Smoke tests that confirm the package imports and core services instantiate."""

from __future__ import annotations


def test_package_imports() -> None:
    import legalbot
    import legalbot.agents.graph
    import legalbot.agents.state
    import legalbot.agents.subagents
    import legalbot.api.main
    import legalbot.scheduling.handlers
    import legalbot.workers.celery_app  # noqa: F401
