"""Independent StateGraph for the reflection stage.

Writes post-session episode summary to long-term memory using:
- read_artifact tool
- list_artifacts tool
- manage_memory_episodes tool
- search_memory_episodes tool
"""

from __future__ import annotations

from typing import Any

from langchain.chat_models import init_chat_model
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from legalbot.agents.memory import build_memory_tools
from legalbot.agents.prompts import REFLECTION_PROMPT
from legalbot.agents.stage_messages import prepare_stage_messages
from legalbot.agents.stage_result import build_stage_result
from legalbot.agents.state import (
    InputState,
    LegalEmailState,
    ReflectOutputState,
)
from legalbot.agents.tools import list_artifacts, read_artifact
from legalbot.agents.models import stage_model

_MEMORY_TOOLS = build_memory_tools()
_REFLECT_TOOLS = [
    read_artifact,
    list_artifacts,
    _MEMORY_TOOLS["episode_manage_for_reflection"],
    _MEMORY_TOOLS["episode_search_for_reflection"],
]


def build_reflect_graph(
    checkpointer: AsyncPostgresSaver | None = None,
    store: Any | None = None,
) -> StateGraph:
    """Build and compile the reflect StateGraph with input/output schemas."""
    model = init_chat_model(stage_model("reflection")).bind_tools(_REFLECT_TOOLS)

    def reflect_node(state: LegalEmailState) -> dict[str, Any]:
        """LLM node for reflection - writes episodic memory."""
        messages = prepare_stage_messages(
            state.get("messages", []),
            system_prompt=REFLECTION_PROMPT,
            fallback_human="Distill the completed task into a durable episode for future runs.",
        )
        return {"messages": [model.invoke(messages)]}

    def finalize_node(state: LegalEmailState) -> dict[str, Any]:
        """Project durable memory work into the orchestrator handoff."""
        return {
            "stage_result": build_stage_result(
                state,
                stage="reflection",
                primary_artifact_key=None,
                next_stage=None,
            )
        }

    builder = StateGraph(
        LegalEmailState,
        input_schema=InputState,
        output_schema=ReflectOutputState,
    )

    # Add nodes
    builder.add_node("reflect", reflect_node)
    builder.add_node("tools", ToolNode(_REFLECT_TOOLS))
    builder.add_node("finalize", finalize_node)

    # Add edges
    builder.add_edge(START, "reflect")
    builder.add_conditional_edges(
        "reflect",
        should_continue,
        {
            "continue": "tools",
            "end": "finalize",
        },
    )
    builder.add_edge("tools", "reflect")
    builder.add_edge("finalize", END)

    # Compile with checkpointer
    return builder.compile(checkpointer=checkpointer, store=store)


def should_continue(state: LegalEmailState) -> str:
    """Determine if we should continue to tools or end."""
    messages = state.get("messages", [])
    if not messages:
        return "end"
    last_message = messages[-1]
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "continue"
    return "end"
