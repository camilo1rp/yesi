"""Independent StateGraph for the analyze stage.

Classifies intent and produces structured analysis using:
- read_artifact tool
- write_artifact tool
"""

from __future__ import annotations

from typing import Any

from langchain.chat_models import init_chat_model
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from legalbot.agents.prompts import ANALYZE_PROMPT
from legalbot.agents.stage_messages import prepare_stage_messages
from legalbot.agents.stage_result import build_stage_result
from legalbot.agents.state import (
    AnalyzeOutputState,
    InputState,
    LegalEmailState,
)
from legalbot.agents.tools import (
    list_artifacts,
    read_artifact,
    update_artifact,
    write_artifact,
)
from legalbot.core.config import get_settings

_ANALYZE_TOOLS = [read_artifact, list_artifacts, write_artifact, update_artifact]


def build_analyze_graph(
    checkpointer: AsyncPostgresSaver | None = None,
) -> StateGraph:
    """Build and compile the analyze StateGraph with input/output schemas."""
    model = init_chat_model(get_settings().AGENT_MODEL).bind_tools(_ANALYZE_TOOLS)

    def analyze_node(state: LegalEmailState) -> dict[str, Any]:
        """LLM node for analysis - classifies intent and produces analysis."""
        messages = prepare_stage_messages(
            state.get("messages", []),
            system_prompt=ANALYZE_PROMPT,
            fallback_human="Classify the email's intent and determine what action is needed.",
        )
        return {"messages": [model.invoke(messages)]}

    def finalize_node(state: LegalEmailState) -> dict[str, Any]:
        """Project durable artifact work into the orchestrator handoff."""
        return {
            "stage_result": build_stage_result(
                state,
                stage="analyze",
                primary_artifact_key="analysis/summary",
                next_stage="act",
            )
        }

    builder = StateGraph(
        LegalEmailState,
        input_schema=InputState,
        output_schema=AnalyzeOutputState,
    )

    # Add nodes
    builder.add_node("analyze", analyze_node)
    builder.add_node("tools", ToolNode(_ANALYZE_TOOLS))
    builder.add_node("finalize", finalize_node)

    # Add edges
    builder.add_edge(START, "analyze")
    builder.add_conditional_edges(
        "analyze",
        should_continue,
        {
            "continue": "tools",
            "end": "finalize",
        },
    )
    builder.add_edge("tools", "analyze")
    builder.add_edge("finalize", END)

    # Compile with checkpointer
    return builder.compile(checkpointer=checkpointer)


def should_continue(state: LegalEmailState) -> str:
    """Determine if we should continue to tools or end."""
    messages = state.get("messages", [])
    if not messages:
        return "end"
    last_message = messages[-1]
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "continue"
    return "end"
