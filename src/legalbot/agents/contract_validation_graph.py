"""Independent StateGraph for the contract-validation stage.

Open-ended LLM review of a freshly rendered contract against reference examples:
- read_artifact — load the ``contracts/draft`` contract and any context
- search_contract_examples — retrieve partial snippets of comparable contracts
- write_artifact — record observations as ``contracts/review``

The validator does not edit the contract or decide what happens next; it surfaces
pattern adherence and possible mismatches for the orchestrator to act on.
"""

from __future__ import annotations

from typing import Any

from langchain.chat_models import init_chat_model
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from legalbot.agents.prompts import CONTRACT_VALIDATION_PROMPT
from legalbot.agents.stage_messages import prepare_stage_messages
from legalbot.agents.stage_result import build_stage_result
from legalbot.agents.state import (
    ContractValidationOutputState,
    InputState,
    LegalEmailState,
)
from legalbot.agents.tools import (
    read_artifact,
    search_contract_examples,
    write_artifact,
)
from legalbot.core.config import get_settings

_CONTRACT_VALIDATION_TOOLS = [
    read_artifact,
    search_contract_examples,
    write_artifact,
]


def build_contract_validation_graph(
    checkpointer: AsyncPostgresSaver | None = None,
) -> StateGraph:
    """Build and compile the contract-validation StateGraph with input/output schemas."""
    model = init_chat_model(get_settings().AGENT_MODEL).bind_tools(_CONTRACT_VALIDATION_TOOLS)

    def validate_node(state: LegalEmailState) -> dict[str, Any]:
        """LLM node: compare the drafted contract against retrieved example patterns."""
        messages = prepare_stage_messages(
            state.get("messages", []),
            system_prompt=CONTRACT_VALIDATION_PROMPT,
            fallback_human="Validate the drafted contract against example contracts and record observations.",
        )
        return {"messages": [model.invoke(messages)]}

    def finalize_node(state: LegalEmailState) -> dict[str, Any]:
        """Project durable artifact work into the orchestrator handoff."""
        return {
            "stage_result": build_stage_result(
                state,
                stage="validate_contract",
                primary_artifact_key="contracts/review",
                next_stage=None,
            )
        }

    builder = StateGraph(
        LegalEmailState,
        input_schema=InputState,
        output_schema=ContractValidationOutputState,
    )

    builder.add_node("validate", validate_node)
    builder.add_node("tools", ToolNode(_CONTRACT_VALIDATION_TOOLS))
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "validate")
    builder.add_conditional_edges(
        "validate",
        should_continue,
        {
            "continue": "tools",
            "end": "finalize",
        },
    )
    builder.add_edge("tools", "validate")
    builder.add_edge("finalize", END)

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
