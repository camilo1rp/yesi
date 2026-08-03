"""Truncate large artifact payloads returned to the model."""

from __future__ import annotations

import json
from typing import Any

from legalbot.core.config import get_settings


def maybe_summarize_artifact_content(content: Any, *, key: str) -> tuple[Any, bool]:
    """Return (content, truncated). Large dicts/strings are shrunk for tool results."""
    settings = get_settings()
    max_bytes = settings.READ_ARTIFACT_SUMMARY_MAX_BYTES

    if isinstance(content, dict):
        serialized = json.dumps(content, ensure_ascii=False)
        if len(serialized.encode("utf-8")) <= max_bytes:
            return content, False
        slim: dict[str, Any] = {}
        for field in (
            "summary",
            "body_summary",
            "intention",
            "action",
            "action_payload",
            "research",
            "missing_information",
            "confidence",
            "blockers",
            "relevant_resources",
            "user_input",
            "entities",
            "key_findings",
            "source_file",
            "status",
            "missing_fields",
            "artifact_key",
            "field_values",
            "provided_overview",
            "body_preview",
            "body_chars",
            "content_summary",
            "entities_preview",
            "attachments_processed",
            "attachments_failed",
            "attachment_count",
            "sender_trust",
            "deadline",
            "referenced_documents",
        ):
            if field in content:
                slim[field] = content[field]
        if slim:
            slim["_truncated"] = True
            slim["_hint"] = (
                f"Full artifact exceeds {max_bytes} bytes; use list_artifacts or "
                f"read specific extracted_data/* keys."
            )
            return slim, True
        return {"_truncated": True, "preview": serialized[:max_bytes]}, True

    if isinstance(content, str):
        raw = content.encode("utf-8")
        if len(raw) <= max_bytes:
            return content, False
        return content[:max_bytes] + "\n… [truncated]", True

    if isinstance(content, (bytes, bytearray)):
        if len(content) <= max_bytes:
            return content, False
        return f"[binary truncated, {len(content)} bytes total]", True

    return content, False
