"""SubAgent definitions (extract, analyze, act, reflection) for the main graph.

Each subagent is an independent StateGraph wrapped in CompiledSubAgent. All stages
share the same AsyncPostgresSaver passed from graph.build_agent — that does not
merge checkpoint history: LangGraph keys checkpoints by thread_id and
checkpoint_ns. _StageCheckpointNamespace sets ``{stage}:{run_id}:{attempt_id}`` so
each ``task(...)`` invocation gets a fresh subgraph stream (audit-friendly) while
internal extract↔tool loops reuse the same attempt id for one ``ainvoke``.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from deepagents import CompiledSubAgent  # type: ignore[import-not-found]
from langchain_core.runnables import Runnable, RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from legalbot.agents.act_graph import build_act_graph
from legalbot.agents.analyze_graph import build_analyze_graph
from legalbot.agents.contract_graph import build_contract_graph
from legalbot.agents.contract_validation_graph import build_contract_validation_graph
from legalbot.agents.extract_graph import build_extract_graph
from legalbot.agents.reflect_graph import build_reflect_graph
from legalbot.core.logging import get_logger

log = get_logger(__name__)


class _StageCheckpointNamespace(Runnable[dict[str, Any], dict[str, Any]]):
    """Bind a compiled stage graph to its checkpoint namespace.

    DeepAgents invokes compiled subagents without forwarding the parent runnable
    config, so the session thread id is carried in graph state and reapplied here.
    """

    def __init__(self, runnable: Any, checkpoint_ns: str) -> None:
        self.runnable: Runnable[dict[str, Any], dict[str, Any]] = runnable
        self.checkpoint_ns = checkpoint_ns

    def _stage_config(
        self,
        state: dict[str, Any],
        config: RunnableConfig | None,
        *,
        attempt_id: str,
    ) -> RunnableConfig:
        merged: dict[str, Any] = dict(config or {})
        configurable: dict[str, Any] = dict(merged.get("configurable") or {})
        thread_id = state.get("graph_thread_id")
        if thread_id:
            configurable["thread_id"] = thread_id
        run_slug = str(state.get("run_id") or "").strip() or "norun"
        configurable["checkpoint_ns"] = f"{self.checkpoint_ns}:{run_slug}:{attempt_id}"
        merged["configurable"] = configurable
        merged["recursion_limit"] = 10
        return cast(RunnableConfig, merged)

    def invoke(
        self,
        input: dict[str, Any],
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        attempt_id = str(uuid.uuid4())
        cfg = self._stage_config(input, config, attempt_id=attempt_id)
        _conf = cfg.get("configurable")
        _ns = _conf.get("checkpoint_ns") if isinstance(_conf, dict) else None
        log.info(
            "subagent.invoke",
            stage=self.checkpoint_ns,
            checkpoint_ns=_ns,
            run_id=input.get("run_id"),
        )
        return self.runnable.invoke(input, config=cfg, **kwargs)

    async def ainvoke(
        self,
        input: dict[str, Any],
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        attempt_id = str(uuid.uuid4())
        cfg = self._stage_config(input, config, attempt_id=attempt_id)
        _conf = cfg.get("configurable")
        _ns = _conf.get("checkpoint_ns") if isinstance(_conf, dict) else None
        log.info(
            "subagent.ainvoke",
            stage=self.checkpoint_ns,
            checkpoint_ns=_ns,
            run_id=input.get("run_id"),
        )
        return await self.runnable.ainvoke(input, config=cfg, **kwargs)


def build_compiled_subagents(
    checkpointer: AsyncPostgresSaver | None = None,
    store: Any | None = None,
) -> list[CompiledSubAgent]:
    """Build each stage graph; reuse `checkpointer` for all (see module docstring)."""
    extract_graph = build_extract_graph(checkpointer=checkpointer)
    analyze_graph = build_analyze_graph(checkpointer=checkpointer)
    act_graph = build_act_graph(checkpointer=checkpointer)
    contract_graph = build_contract_graph(checkpointer=checkpointer)
    contract_validation_graph = build_contract_validation_graph(checkpointer=checkpointer)
    reflect_graph = build_reflect_graph(checkpointer=checkpointer, store=store)

    return [
        CompiledSubAgent(
            name="extract",
            description="Pull structured facts from the email and its attachments.",
            runnable=_StageCheckpointNamespace(extract_graph, "extract"),
        ),
        CompiledSubAgent(
            name="analyze",
            description="Classify intent and produce a structured analysis artifact.",
            runnable=_StageCheckpointNamespace(analyze_graph, "analyze"),
        ),
        CompiledSubAgent(
            name="act",
            description="Draft replies, schedule follow-ups, and request approvals.",
            runnable=_StageCheckpointNamespace(act_graph, "act"),
        ),
        CompiledSubAgent(
            name="draft_contract",
            description=(
                "Validate the requested contract type is supported and unambiguous, "
                "gather required fields from the email and extracted documents, and "
                "deterministically fill the contract template."
            ),
            runnable=_StageCheckpointNamespace(contract_graph, "draft_contract"),
        ),
        CompiledSubAgent(
            name="validate_contract",
            description=(
                "Review a drafted contract against example contracts and report "
                "pattern adherence and possible mismatches."
            ),
            runnable=_StageCheckpointNamespace(contract_validation_graph, "validate_contract"),
        ),
        CompiledSubAgent(
            name="reflection",
            description=(
                "Writes a post-session episode summary to long-term memory. "
                "Call this as the FINAL action of any non-trivial task."
            ),
            runnable=_StageCheckpointNamespace(reflect_graph, "reflection"),
        ),
    ]


# Backward compatibility alias
def build_subagents(
    checkpointer: AsyncPostgresSaver | None = None,
    store: Any | None = None,
) -> list[CompiledSubAgent]:
    """Alias for build_compiled_subagents for backward compatibility."""
    return build_compiled_subagents(checkpointer=checkpointer, store=store)
