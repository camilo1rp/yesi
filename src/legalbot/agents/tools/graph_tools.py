"""Knowledge graph retrieval tools for the main agent."""

from __future__ import annotations

from typing import Annotated, Any

try:
    from langchain_core.tools import tool
    from langgraph.prebuilt import InjectedState
except Exception:  # pragma: no cover — test shim

    def tool(*args, **kwargs):  # type: ignore[no-redef]
        def deco(fn):
            return fn

        return deco if not args else deco(args[0])

    InjectedState = object  # type: ignore[assignment]

from legalbot.core.config import get_settings
from legalbot.db.session import async_session_factory
from legalbot.memory.service import KnowledgeGraphService


@tool
async def search_related_docs(
    query: str,
    k: int | None = None,
    state: Annotated[dict, InjectedState] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Search the knowledge graph for documents related to a person, org, or topic.

    Returns source handles (ingestion_item ids, attachment ids) with snippets and
    matched entity metadata — without reading full file contents. Use before
    drafting contracts or replying when you need prior context about parties.
    """
    state = state or {}
    user_id = state.get("user_id")
    if not user_id:
        return {"ok": False, "error": "missing user_id in agent state"}

    settings = get_settings()
    limit = k or settings.KG_SEARCH_TOP_K

    sm = async_session_factory()
    async with sm() as db:
        svc = KnowledgeGraphService(db)
        results = await svc.search_related(query, owner_user_id=user_id, k=limit)
        return {"ok": True, "query": query, "results": results}
