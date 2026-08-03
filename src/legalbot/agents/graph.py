"""Main graph factory — assembles create_agent + the full middleware stack.

The compiled graph is cached per-process alongside the psycopg pool it uses.
If the pool is closed or replaced, the next `build_agent()` call rebuilds the
graph with a fresh checkpointer/store.
"""

from __future__ import annotations

from typing import Any, cast

from legalbot.agents.memory import MemoryMiddleware, build_memory_tools
from legalbot.agents.prompts import (
    MEMORY_GUIDANCE,
    SESSION_GUIDANCE,
    build_main_system_prompt,
)
from legalbot.agents.state import AgentState
from legalbot.agents.subagents import build_compiled_subagents
from legalbot.agents.tools import list_artifacts, read_artifact, search_related_docs, send_draft
from legalbot.core.config import get_settings
from legalbot.core.logging import get_logger
from legalbot.interrupts.tools import ask_human, request_human_approval

log = get_logger(__name__)

_compiled_graph: tuple[Any, Any, Any] | None = None


def _pool_is_closed(pool: Any) -> bool:
    return bool(getattr(pool, "closed", False))


async def build_agent() -> tuple[Any, Any]:
    """Return (compiled_graph, store). Caches the graph for reuse."""
    global _compiled_graph

    from legalbot.db.session import psycopg_pool

    settings = get_settings()
    pool = await psycopg_pool()

    if _compiled_graph is not None:
        agent, store, cached_pool = _compiled_graph
        if cached_pool is pool and not _pool_is_closed(pool):
            return agent, store

    from deepagents.backends import StateBackend
    from langchain.agents import create_agent
    from langchain.agents.middleware import (
        HumanInTheLoopMiddleware,
        SummarizationMiddleware,
        TodoListMiddleware,
    )
    from langchain.chat_models import init_chat_model
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.store.postgres.aio import AsyncPostgresStore

    # One AsyncPostgresSaver for the whole process is intentional: stage isolation
    # is (thread_id, checkpoint_ns), not separate saver instances. Subagents bind
    # checkpoint_ns in subagents._StageCheckpointNamespace ({stage}:{run_id}:{attempt});
    # see docs/architecture.md.
    checkpointer = AsyncPostgresSaver(pool)
    await checkpointer.setup()

    store = AsyncPostgresStore(
        pool,
        index={
            "embed": settings.EMBED_MODEL,
            "dims": settings.EMBED_DIMS,
            "fields": ["content", "task", "approach"],
        },
    )
    await store.setup()

    memory_tools = build_memory_tools()
    main_model = init_chat_model(settings.AGENT_MODEL)

    native_tools = [
        read_artifact,
        list_artifacts,
        search_related_docs,
        send_draft,
        ask_human,
        request_human_approval,
        memory_tools["user_manage"],
        memory_tools["user_search"],
        memory_tools["episode_search"],
        memory_tools["procedure_search"],
    ]

    middleware: list[Any] = [
        SummarizationMiddleware(
            model=settings.SUMMARIZATION_MODEL,
            trigger=("tokens", 60_000),
            keep=("messages", 30),
        ),
    ]

    from deepagents.middleware import FilesystemMiddleware, SubAgentMiddleware

    backend = StateBackend()
    middleware.extend(
        [
            TodoListMiddleware(),
            FilesystemMiddleware(backend=backend),
            SubAgentMiddleware(
                backend=backend,
                subagents=build_compiled_subagents(checkpointer=checkpointer, store=store),
            ),
        ]
    )

    if settings.BIGTOOL_ENABLED:
        from legalbot.agents.bigtool import LLMToolSelectorMiddleware

        middleware.append(LLMToolSelectorMiddleware())

    middleware.extend(
        [
            MemoryMiddleware(store=store),
            HumanInTheLoopMiddleware(
                interrupt_on={
                    # Only ``send_draft`` is gated. ``write_draft`` and ``schedule_followup``
                    # run inside the act subagent and would bypass this middleware anyway,
                    # since deepagents' ``task`` tool restarts subagents with fresh state on
                    # resume (the subagent-local interrupt would not survive a parent
                    # ``Command(resume=...)`` round-trip). ``send_draft`` is parent-only,
                    # so this gate fires before any outbound communication.
                    "send_draft": {"allowed_decisions": ["approve", "reject"]},
                }
            ),
        ]
    )

    agent = create_agent(
        model=main_model,
        tools=native_tools,
        system_prompt=build_main_system_prompt() + SESSION_GUIDANCE + MEMORY_GUIDANCE,
        middleware=middleware,
        state_schema=cast(Any, AgentState),
        checkpointer=checkpointer,
        store=store,
    )

    _compiled_graph = (agent, store, pool)
    return agent, store


async def get_compiled_graph() -> Any:
    agent, _ = await build_agent()
    return agent
