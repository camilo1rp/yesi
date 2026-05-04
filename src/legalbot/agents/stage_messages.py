"""Message preparation helpers for independent stage graphs."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from legalbot.core.logging import get_logger

log = get_logger(__name__)


def sanitize_messages_for_openai_chat(messages: list[Any]) -> list[Any]:
    """Drop a leading prefix so the remainder is safe for Chat Completions APIs.

    OpenAI rejects requests where an ``assistant`` message includes ``tool_calls``
    but not every ``tool_call_id`` is answered by a following ``tool`` message.

    Typical sources: a LangGraph checkpoint paused before ``ToolNode`` ran;
    ``aupdate_state`` appending a human (replay fork); or a compiled subagent
    thread where ``add_messages`` merged a prior tail with a new ``task`` human.
    We strip from the left until the suffix satisfies the ordering rules.
    """
    n = len(messages)
    for start in range(n):
        if _openai_tool_chain_valid(messages[start:]):
            if start > 0:
                log.info(
                    "stage_messages.sanitized_prefix",
                    dropped_messages=start,
                )
            return messages[start:]
    if n > 0:
        log.warning(
            "stage_messages.no_valid_suffix",
            message_count=n,
        )
    return []


def _tool_call_ids_from_ai(msg: Any) -> set[str]:
    raw = getattr(msg, "tool_calls", None) or []
    ids: set[str] = set()
    for tc in raw:
        tid: str | None = None
        if isinstance(tc, dict):
            tid = tc.get("id")
        else:
            tid = getattr(tc, "id", None)
        if tid is not None and str(tid) != "":
            ids.add(str(tid))
    return ids


def _openai_tool_chain_valid(messages: list[Any]) -> bool:
    """Whether *messages* can be sent as chat history without tool pairing errors."""
    pending: set[str] = set()
    for msg in messages:
        msg_type = getattr(msg, "type", None)
        if msg_type == "tool":
            tid = getattr(msg, "tool_call_id", None)
            if tid is None or str(tid) not in pending:
                return False
            pending.discard(str(tid))
        elif msg_type == "ai":
            if pending:
                return False
            ids = _tool_call_ids_from_ai(msg)
            if ids:
                pending = set(ids)
        elif msg_type in ("system", "human", "developer", "chat"):
            if pending:
                return False
        else:
            if pending:
                return False
    return len(pending) == 0


def prepare_stage_messages(
    messages: list[Any],
    *,
    system_prompt: str,
    fallback_human: str,
) -> list[Any]:
    """Ensure a stage prompt is present without persisting synthetic messages."""
    if not messages:
        return [
            SystemMessage(content=system_prompt),
            HumanMessage(content=fallback_human),
        ]

    cleaned = sanitize_messages_for_openai_chat(list(messages))
    if not cleaned:
        return [
            SystemMessage(content=system_prompt),
            HumanMessage(content=fallback_human),
        ]

    if any(
        getattr(message, "type", None) == "system"
        and getattr(message, "content", None) == system_prompt
        for message in cleaned
    ):
        return cleaned

    return [SystemMessage(content=system_prompt), *cleaned]
