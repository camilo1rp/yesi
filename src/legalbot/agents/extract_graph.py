"""Independent StateGraph for the extract stage.

Materializes attachment artifacts via tools, then deterministically writes a rich
``analysis/extracted`` inventory in finalize for the analyze stage.
"""

from __future__ import annotations

import uuid
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from legalbot.agents.extract_inventory import (
    build_extract_inventory,
    build_extract_stage_result,
    write_extract_inventory,
)
from legalbot.agents.models import init_stage_model
from legalbot.agents.prompts import EXTRACT_PROMPT
from legalbot.agents.stage_messages import prepare_stage_messages
from legalbot.agents.state import (
    ExtractOutputState,
    InputState,
    LegalEmailState,
)
from legalbot.agents.tools import (
    analyze_image,
    fetch_email,
    read_artifact,
    run_attachment_extraction,
    write_artifact,
)
from legalbot.artifacts.service import ArtifactService
from legalbot.db.session import async_session_factory

_EXTRACT_TOOLS = [
    fetch_email,
    run_attachment_extraction,
    analyze_image,
    write_artifact,
    read_artifact,
]


def _session_uuid(state: LegalEmailState) -> uuid.UUID:
    sid = state.get("session_id")
    if not sid:
        raise RuntimeError("extract graph invoked without session_id")
    return uuid.UUID(sid)


def _run_uuid(state: LegalEmailState) -> uuid.UUID | None:
    rid = state.get("run_id")
    return uuid.UUID(rid) if rid else None


def build_extract_graph(
    checkpointer: AsyncPostgresSaver | None = None,
) -> StateGraph:
    """Build and compile the extract StateGraph with input/output schemas."""
    model = init_stage_model("extract").bind_tools(_EXTRACT_TOOLS)

    def extract_node(state: LegalEmailState) -> dict[str, Any]:
        """LLM node for extraction - calls tools to extract facts."""
        messages = prepare_stage_messages(
            state.get("messages", []),
            system_prompt=EXTRACT_PROMPT,
            fallback_human="Extract structured facts from the email and attachments.",
        )
        return {"messages": [model.invoke(messages)]}

    async def finalize_node(state: LegalEmailState) -> dict[str, Any]:
        """Write enriched inventory artifact and project stage handoff."""
        session_id = _session_uuid(state)
        run_id = _run_uuid(state)
        sm = async_session_factory()
        async with sm() as db:
            art_svc = ArtifactService(db)
            inventory = await build_extract_inventory(art_svc, session_id)
            if not inventory.get("error"):
                await write_extract_inventory(
                    art_svc,
                    session_id=session_id,
                    run_id=run_id,
                    inventory=inventory,
                )
            await db.commit()

        stage_result = build_extract_stage_result(state)
        if inventory.get("error"):
            stage_result["summary"] = str(inventory["error"])
            stage_result["primary_artifact_key"] = None
        return {"stage_result": stage_result}

    builder = StateGraph(
        LegalEmailState,
        input_schema=InputState,
        output_schema=ExtractOutputState,
    )

    builder.add_node("extract", extract_node)
    builder.add_node("tools", ToolNode(_EXTRACT_TOOLS))
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "extract")
    builder.add_conditional_edges(
        "extract",
        should_continue,
        {
            "continue": "tools",
            "end": "finalize",
        },
    )
    builder.add_edge("tools", "extract")
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
