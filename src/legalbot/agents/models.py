"""Stage-specific chat model resolution (cost tiering, retries, rate limits)."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.rate_limiters import InMemoryRateLimiter

from legalbot.core.config import get_settings


def stage_model(stage: str) -> str:
    """Return the LangChain model id for a pipeline stage."""
    settings = get_settings()
    by_stage = {
        "orchestrator": settings.AGENT_MODEL,
        "extract": settings.EXTRACT_MODEL,
        "analyze": settings.ANALYZE_MODEL,
        "analyze_research": settings.ANALYZE_RESEARCH_MODEL or settings.ANALYZE_MODEL,
        "analyze_decide": settings.ANALYZE_DECIDE_MODEL or settings.ANALYZE_MODEL,
        "act": settings.ACT_MODEL,
        "draft_contract": settings.CONTRACT_MODEL,
        "validate_contract": settings.CONTRACT_VALIDATION_MODEL,
        "reflection": settings.REFLECT_MODEL,
        "summarization": settings.SUMMARIZATION_MODEL,
        "vision": settings.VISION_MODEL,
        "kg_extraction": settings.KG_EXTRACTION_MODEL,
    }
    return by_stage.get(stage, settings.AGENT_MODEL)


@lru_cache(maxsize=1)
def _llm_rate_limiter() -> InMemoryRateLimiter | None:
    settings = get_settings()
    if settings.LLM_REQUESTS_PER_SECOND <= 0:
        return None
    return InMemoryRateLimiter(
        requests_per_second=settings.LLM_REQUESTS_PER_SECOND,
        check_every_n_seconds=0.1,
        max_bucket_size=settings.LLM_MAX_BUCKET_SIZE,
    )


def init_chat_model_configured(model_id: str, **kwargs: Any) -> Any:
    """``init_chat_model`` with shared retry + optional in-process rate limiting."""
    settings = get_settings()
    init_kwargs: dict[str, Any] = {
        "max_retries": settings.LLM_MAX_RETRIES,
    }
    rate_limiter = _llm_rate_limiter()
    if rate_limiter is not None and "rate_limiter" not in kwargs:
        init_kwargs["rate_limiter"] = rate_limiter
    init_kwargs.update(kwargs)
    return init_chat_model(model_id, **init_kwargs)


def init_stage_model(stage: str, **kwargs: Any) -> Any:
    """Chat model for a pipeline stage (retries + rate limit from settings)."""
    return init_chat_model_configured(stage_model(stage), **kwargs)
