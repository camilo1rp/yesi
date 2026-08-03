"""Tests for LLM cost-control helpers."""

from __future__ import annotations

from legalbot.attachments.vision_policy import should_skip_vision_analysis
from legalbot.artifacts.read_policy import maybe_summarize_artifact_content


def test_skip_decorative_small_image() -> None:
    reason = should_skip_vision_analysis(b"x", name="acme_logo.png")
    assert reason == "decorative_small_image"


def test_do_not_skip_large_image() -> None:
    data = b"x" * 9000
    assert should_skip_vision_analysis(data, name="scan.png") is None


def test_summarize_large_dict() -> None:
    big = {"summary": "ok", "extra": "x" * 5000}
    out, truncated = maybe_summarize_artifact_content(big, key="analysis/report")
    assert truncated
    assert out.get("summary") == "ok"
    assert out.get("_truncated") is True


def test_passthrough_small_dict() -> None:
    small = {"summary": "hi", "entities": []}
    out, truncated = maybe_summarize_artifact_content(small, key="x")
    assert not truncated
    assert out == small


def test_stage_model_includes_summarization_and_vision() -> None:
    from legalbot.agents.models import stage_model

    assert stage_model("summarization")
    assert stage_model("vision")
    assert stage_model("kg_extraction")


def test_init_chat_model_configured_passes_retries() -> None:
    from unittest.mock import patch

    from legalbot.agents.models import init_chat_model_configured

    with patch("legalbot.agents.models.init_chat_model") as mock_init:
        init_chat_model_configured("openai:gpt-4o-mini")
        mock_init.assert_called_once()
        _, kwargs = mock_init.call_args
        assert kwargs.get("max_retries") == 6
        assert "rate_limiter" in kwargs
