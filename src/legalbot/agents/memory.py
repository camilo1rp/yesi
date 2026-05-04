"""Long-term memory: namespaces, tools, and MemoryMiddleware preloader."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

try:
    from langchain.agents.middleware import (
        AgentMiddleware,
        ModelRequest,
        ModelResponse,
    )
    from langchain_core.messages import SystemMessage
except Exception:  # pragma: no cover — test shim
    AgentMiddleware = object  # type: ignore[assignment]
    ModelRequest = Any  # type: ignore[assignment]
    ModelResponse = Any  # type: ignore[assignment]
    SystemMessage = None  # type: ignore[assignment]


USER_FACTS_NS = ("users", "{langgraph_user_id}", "facts")
AGENT_EPISODES_NS = ("agent_episodes", "main")
AGENT_PROCEDURES_NS = ("procedures", "main")


def build_memory_tools() -> dict[str, Any]:
    """Assemble langmem-based memory tools. Returns a dict keyed by purpose."""
    from langmem import create_manage_memory_tool, create_search_memory_tool

    return {
        "user_manage": create_manage_memory_tool(
            namespace=USER_FACTS_NS,
            instructions=(
                "Call only for durable facts about THIS user (preferences, "
                "constraints, team conventions). Skip transient context."
            ),
        ),
        "user_search": create_search_memory_tool(namespace=USER_FACTS_NS),
        "episode_search": create_search_memory_tool(namespace=AGENT_EPISODES_NS),
        "procedure_search": create_search_memory_tool(namespace=AGENT_PROCEDURES_NS),
        "episode_manage_for_reflection": create_manage_memory_tool(
            namespace=AGENT_EPISODES_NS,
            instructions="Store a generalizable lesson from a completed task.",
            name="manage_memory_episodes",
        ),
        "episode_search_for_reflection": create_search_memory_tool(
            namespace=AGENT_EPISODES_NS,
            name="search_memory_episodes",
        ),
    }


class MemoryMiddleware(AgentMiddleware):  # type: ignore[misc]
    """Retrieve user facts + similar past episodes once per model call.

    Runs in wrap_model_call so retrieval happens once per turn, not per tool call
    — critical for prompt-cache hit rate.
    """

    def __init__(self, store: Any, user_facts_k: int = 5, episodes_k: int = 2) -> None:
        self.store = store
        self.user_facts_k = user_facts_k
        self.episodes_k = episodes_k

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        config = (
            getattr(request, "runtime", None) and getattr(request.runtime, "context", None)
        ) or {}
        user_id = config.get("user_id") if isinstance(config, dict) else None
        if not user_id:
            return await handler(request)

        messages = getattr(request, "messages", []) or []
        last_user = next(
            (m for m in reversed(messages) if getattr(m, "type", None) == "human"), None
        )
        query = getattr(last_user, "content", "") if last_user else ""

        user_facts: list[Any] = []
        episodes: list[Any] = []
        try:
            user_facts = await self.store.asearch(
                ("users", user_id, "facts"), query=query, limit=self.user_facts_k
            )
            episodes = await self.store.asearch(
                AGENT_EPISODES_NS, query=query, limit=self.episodes_k
            )
        except Exception:
            return await handler(request)

        if not user_facts and not episodes:
            return await handler(request)

        blocks: list[str] = []
        if user_facts:
            lines = [f"- {f.value.get('content', '')}" for f in user_facts]
            blocks.append("## What I know about this user\n" + "\n".join(lines))
        if episodes:
            lines = [
                f"- Past task: {e.value.get('task', '')}\n"
                f"  Approach: {e.value.get('approach', '')}\n"
                f"  Outcome: {e.value.get('outcome', '')}"
                for e in episodes
            ]
            blocks.append("## Similar past tasks (for reference only)\n" + "\n".join(lines))

        sm = getattr(request, "system_message", None)
        if sm is None or SystemMessage is None:
            return await handler(request)
        try:
            new_sm = SystemMessage(
                content=list(sm.content_blocks) + [{"type": "text", "text": "\n\n".join(blocks)}]
            )
            return await handler(request.override(system_message=new_sm))
        except Exception:
            return await handler(request)
