"""Independent StateGraph for the contract-drafting stage.

Deterministically fills a contract template once every required field is known:
- list_contract_types / get_contract_requirements — confirm support, learn fields
- read_artifact / list_artifacts — explore the email + extracted attachments for values
- fill_contract_template — strict, refuses to write unless all fields are present

This stage never sends or asks the human; if the type is unsupported/ambiguous or
fields are missing, it reports that in its final message so the orchestrator can
call ``ask_human`` (same convention as the extract stage).
"""

from __future__ import annotations

from typing import Any

from langchain.chat_models import init_chat_model
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from legalbot.agents.prompts import CONTRACT_PROMPT
from legalbot.agents.stage_messages import prepare_stage_messages
from legalbot.agents.stage_result import build_stage_result
from legalbot.agents.state import (
    ContractOutputState,
    InputState,
    LegalEmailState,
)
from legalbot.agents.tools import (
    fill_contract_template,
    get_contract_requirements,
    list_artifacts,
    list_contract_types,
    read_artifact,
)
from legalbot.core.config import get_settings

_CONTRACT_TOOLS = [
    list_contract_types,
    get_contract_requirements,
    read_artifact,
    list_artifacts,
    fill_contract_template,
]


def build_contract_graph(
    checkpointer: AsyncPostgresSaver | None = None,
) -> StateGraph:
    """Build and compile the contract-drafting StateGraph with input/output schemas."""
    model = init_chat_model(get_settings().AGENT_MODEL).bind_tools(_CONTRACT_TOOLS)

    def contract_node(state: LegalEmailState) -> dict[str, Any]:
        """LLM node: validate type, gather fields, fill the template deterministically."""
        messages = prepare_stage_messages(
            state.get("messages", []),
            system_prompt=CONTRACT_PROMPT,
            fallback_human="Validate the contract type and fill the template if all fields are present.",
        )
        return {"messages": [model.invoke(messages)]}

    def finalize_node(state: LegalEmailState) -> dict[str, Any]:
        """Project durable artifact work into the orchestrator handoff."""
        return {
            "stage_result": build_stage_result(
                state,
                stage="draft_contract",
                primary_artifact_key="contracts/draft",
                next_stage="validate_contract",
            )
        }

    builder = StateGraph(
        LegalEmailState,
        input_schema=InputState,
        output_schema=ContractOutputState,
    )

    builder.add_node("contract", contract_node)
    builder.add_node("tools", ToolNode(_CONTRACT_TOOLS))
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "contract")
    builder.add_conditional_edges(
        "contract",
        should_continue,
        {
            "continue": "tools",
            "end": "finalize",
        },
    )
    builder.add_edge("tools", "contract")
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
