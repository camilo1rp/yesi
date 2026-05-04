"""Layer 3 — BigTool pattern (retrieve_tools meta-tool backed by pgvector).

When `settings.BIGTOOL_ENABLED` is true, `build_agent()` registers
`LLMToolSelectorMiddleware` which keeps only a small resident set of always-on
tools (artifact + ask_human) and adds a `retrieve_tools` meta-tool that the
agent calls to pull in relevant tools on demand.
"""

from __future__ import annotations

from typing import Any

try:
    from langchain.agents.middleware import AgentMiddleware
except Exception:  # pragma: no cover
    AgentMiddleware = object  # type: ignore[assignment]


RESIDENT_TOOL_NAMES = frozenset(
    {
        "write_artifact",
        "read_artifact",
        "update_artifact",
        "list_artifacts",
        "ask_human",
        "retrieve_tools",
    }
)


class LLMToolSelectorMiddleware(AgentMiddleware):  # type: ignore[misc]
    """Placeholder: progressive-disclosure tool selector keyed off the system prompt.

    A production implementation embeds tool descriptions into the store via pgvector
    and pulls top-k candidates into the model_call request. For now we expose a stub
    that restricts the tool set to the resident set + anything the agent has already
    surfaced via a `retrieve_tools` call (tracked in `state.files['_tools_revealed']`).
    """

    def __init__(self, catalog: dict[str, Any] | None = None) -> None:
        self.catalog = catalog or {}

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        state = getattr(request, "state", {}) or {}
        revealed = set((state.get("files") or {}).get("_tools_revealed", "").split(","))
        allowed = RESIDENT_TOOL_NAMES | revealed
        tools = getattr(request, "tools", None) or []
        filtered = [t for t in tools if getattr(t, "name", None) in allowed]
        try:
            new_request = request.override(tools=filtered)
        except Exception:
            return await handler(request)
        return await handler(new_request)
