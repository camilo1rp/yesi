"""Independent StateGraph for the act stage.

Executes actions: draft replies and schedule follow-ups using:
- read_artifact tool
- write_artifact tool
- write_draft tool
- schedule_followup tool

``send_draft`` deliberately lives on the parent agent only. ``HumanInTheLoopMiddleware``
gates it before each send, and that middleware is wired on the parent graph; tools
invoked from inside this subgraph would bypass the gate (deepagents' ``task`` tool
restarts subagents with fresh state on resume, so a subagent-local interrupt would
not survive a parent ``Command(resume=...)`` round-trip).
"""

from __future__ import annotations

from typing import Any

from langchain.chat_models import init_chat_model
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from legalbot.agents.prompts import ACT_PROMPT
from legalbot.agents.stage_messages import prepare_stage_messages
from legalbot.agents.stage_result import build_stage_result
from legalbot.agents.state import (
    ActOutputState,
    InputState,
    LegalEmailState,
)
from legalbot.agents.tools import (
    list_artifacts,
    read_artifact,
    schedule_followup,
    update_artifact,
    write_artifact,
    write_draft,
)
from legalbot.agents.models import stage_model

_ACT_TOOLS = [
    read_artifact,
    write_artifact,
    update_artifact,
    list_artifacts,
    write_draft,
    schedule_followup,
]


def build_act_graph(
    checkpointer: AsyncPostgresSaver | None = None,
) -> StateGraph:
    """Build and compile the act StateGraph with input/output schemas."""
    model = init_chat_model(stage_model("act")).bind_tools(_ACT_TOOLS)

    def act_node(state: LegalEmailState) -> dict[str, Any]:
        """LLM node for actions - drafts replies, schedules follow-ups, requests approvals."""
        messages = prepare_stage_messages(
            state.get("messages", []),
            system_prompt=ACT_PROMPT,
            fallback_human="Execute actions: draft replies, schedule follow-ups, or ask the user.",
        )
        return {"messages": [model.invoke(messages)]}

    def finalize_node(state: LegalEmailState) -> dict[str, Any]:
        """Project durable artifact work into the orchestrator handoff."""
        return {
            "stage_result": build_stage_result(
                state,
                stage="act",
                primary_artifact_key=["act/outcome", "drafts/reply"],
                next_stage="reflection",
            )
        }

    builder = StateGraph(
        LegalEmailState,
        input_schema=InputState,
        output_schema=ActOutputState,
    )

    # Add nodes
    builder.add_node("act", act_node)
    builder.add_node("tools", ToolNode(_ACT_TOOLS))
    builder.add_node("finalize", finalize_node)

    # Add edges
    builder.add_edge(START, "act")
    builder.add_conditional_edges(
        "act",
        should_continue,
        {
            "continue": "tools",
            "end": "finalize",
        },
    )
    builder.add_edge("tools", "act")
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
