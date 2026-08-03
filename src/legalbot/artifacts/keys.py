"""Artifact key normalization helpers."""

from __future__ import annotations


def artifact_key_candidates(key: str) -> list[str]:
    """Expand bare attachment filenames into standard artifact key prefixes."""
    key = (key or "").strip()
    if not key:
        return []
    if "/" in key:
        return [key]
    return [
        f"extracted_data/{key}",
        f"extracted_text/{key}",
        f"image_analysis/{key}",
    ]
