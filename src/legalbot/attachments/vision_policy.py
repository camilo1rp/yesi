"""Heuristics to skip expensive vision calls on decorative / tiny images."""

from __future__ import annotations

import re

from legalbot.core.config import get_settings

_DECORATIVE_RE = re.compile(
    r"(logo|icon|favicon|badge|avatar|watermark|signature[-_]?mark)",
    re.IGNORECASE,
)


def should_skip_vision_analysis(
    data: bytes,
    *,
    name: str | None,
    mime: str | None = None,
) -> str | None:
    """Return a short reason when vision LLM should not run; else None."""
    settings = get_settings()
    size = len(data)
    if size == 0:
        return "empty_image"
    if size <= settings.VISION_SKIP_MAX_BYTES and _DECORATIVE_RE.search(name or ""):
        return "decorative_small_image"
    return None
