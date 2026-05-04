"""Deterministic projection from stage message history to orchestrator output."""

from __future__ import annotations

import json
from typing import Any

from legalbot.agents.state import StageResult

_HUMAN_TOOL_NAMES = {"ask_human", "request_human_approval"}


def build_stage_result(
    state: dict[str, Any],
    *,
    stage: str,
    primary_artifact_key: str | list[str] | None,
    next_stage: str | None,
) -> StageResult:
    """Return the compact structured handoff for a completed stage graph."""
    messages = state.get("messages", [])
    artifact_keys = sorted(_artifact_keys_from_messages(messages))
    needs_human = _last_message_is_human_tool(messages)

    return {
        "stage": stage,
        "status": "awaiting_human" if needs_human else "completed",
        "summary": _latest_ai_text(messages) or f"{stage} stage completed.",
        "primary_artifact_key": _primary_artifact_key(primary_artifact_key, artifact_keys),
        "artifact_keys": artifact_keys,
        "needs_human": needs_human,
        "next_stage": None if needs_human else next_stage,
    }


def _primary_artifact_key(
    candidates: str | list[str] | None, artifact_keys: list[str]
) -> str | None:
    if candidates is None:
        return None
    if isinstance(candidates, str):
        candidates = [candidates]
    for candidate in candidates:
        if candidate in artifact_keys:
            return candidate
    return None


def _artifact_keys_from_messages(messages: list[Any]) -> set[str]:
    keys: set[str] = set()
    for message in messages:
        if getattr(message, "type", None) != "tool":
            continue
        _collect_artifact_keys(_message_payload(message), keys)
    return keys


def _message_payload(message: Any) -> Any:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return content
    return content


def _collect_artifact_keys(value: Any, keys: set[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if (key == "key" or key.endswith("_artifact_key")) and isinstance(child, str):
                keys.add(child)
            elif key == "artifact_keys" and isinstance(child, list):
                keys.update(item for item in child if isinstance(item, str))
            else:
                _collect_artifact_keys(child, keys)
    elif isinstance(value, list):
        for item in value:
            _collect_artifact_keys(item, keys)


def _latest_ai_text(messages: list[Any]) -> str | None:
    for message in reversed(messages):
        if getattr(message, "type", None) not in {"ai", "assistant"}:
            continue
        text = _text_content(getattr(message, "content", None))
        if text:
            return text[:500]
    return None


def _text_content(content: Any) -> str | None:
    if isinstance(content, str):
        return content.strip() or None
    if isinstance(content, list):
        blocks: list[str] = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                blocks.append(block["text"])
        text = "\n".join(blocks).strip()
        return text or None
    return None


def _last_message_is_human_tool(messages: list[Any]) -> bool:
    if not messages:
        return False
    last = messages[-1]
    return (
        getattr(last, "type", None) == "tool" and getattr(last, "name", None) in _HUMAN_TOOL_NAMES
    )
