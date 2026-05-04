"""LangChain-compatible `ask_human` and `request_human_approval` tools.

`ask_human` validates the agent-supplied JSONSchema at call time (fail-closed if the
schema itself is invalid) and then raises `interrupt({"kind": "information_request", ...})`
so the main graph pauses. The return value is whatever the resume envelope provides.
"""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator, SchemaError

try:
    from langchain_core.tools import tool
except Exception:  # pragma: no cover — langchain optional at import time

    def tool(*args, **kwargs):  # type: ignore[no-redef]
        def deco(fn):
            return fn

        return deco if not args else deco(args[0])


def _interrupt(envelope: dict[str, Any]) -> Any:
    """Call LangGraph's `interrupt()` primitive; raises a GraphInterrupt.

    Late-imported so the module stays importable without langgraph in test shim envs.
    """
    from langgraph.types import interrupt

    return interrupt(envelope)


_DEFAULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}


@tool
def ask_human(
    prompt: str,
    rationale: str,
    blocking_reason: str,
    response_schema: dict[str, Any] | None = None,
    suggested_choices: list[str] | None = None,
) -> dict[str, Any]:
    """Pause and ask the user for information the agent cannot determine from state.

    Args:
        prompt: The question to present to the user.
        rationale: Why this information is needed.
        blocking_reason: What cannot proceed without the answer.
        response_schema: Optional JSON Schema for the expected answer shape.
            Defaults to a single free-text ``answer`` field when omitted.
        suggested_choices: Optional list of suggested answers.

    Returns a dict shaped by ``response_schema`` when the user answers, or
    ``{"skipped": true, "reason": ...}`` when they decline.
    """
    resolved_schema = response_schema or _DEFAULT_SCHEMA
    try:
        Draft202012Validator.check_schema(resolved_schema)
    except SchemaError as e:
        return {
            "error": "invalid schema",
            "detail": str(e),
            "hint": "ask_human must supply a valid JSONSchema (Draft 2020-12).",
        }

    envelope = {
        "kind": "information_request",
        "payload": {
            "prompt": prompt,
            "schema": resolved_schema,
            "rationale": rationale,
            "blocking_reason": blocking_reason,
            "suggested_choices": suggested_choices,
        },
    }
    return _interrupt(envelope)


@tool
def request_human_approval(
    draft_id: str,
    preview: str,
    open_questions: list[str] | None = None,
) -> dict[str, Any]:
    """Raise a review_draft interrupt for explicit human review of a finished draft."""
    envelope = {
        "kind": "review_draft",
        "payload": {
            "draft_id": draft_id,
            "preview": preview,
            "open_questions": open_questions or [],
        },
    }
    return _interrupt(envelope)
