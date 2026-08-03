"""Stage-specific chat model resolution (cost tiering)."""

from __future__ import annotations

from legalbot.core.config import get_settings


def stage_model(stage: str) -> str:
    """Return the LangChain model id for a pipeline stage."""
    settings = get_settings()
    by_stage = {
        "orchestrator": settings.AGENT_MODEL,
        "extract": settings.EXTRACT_MODEL,
        "analyze": settings.ANALYZE_MODEL,
        "act": settings.ACT_MODEL,
        "draft_contract": settings.CONTRACT_MODEL,
        "validate_contract": settings.CONTRACT_VALIDATION_MODEL,
        "reflection": settings.REFLECT_MODEL,
    }
    return by_stage.get(stage, settings.AGENT_MODEL)
