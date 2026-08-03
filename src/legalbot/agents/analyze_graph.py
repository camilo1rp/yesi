"""Multi-node StateGraph for the analyze stage.

Loads extraction context, runs tool-based research, produces a structured decision,
and deterministically writes ``analysis/report`` (or ``act/outcome`` for unsupported).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from legalbot.agents.analyze_report import (
    build_analyze_report,
    build_analyze_stage_result,
    load_extracted_user_input,
    write_analysis_report,
    write_unsupported_outcome,
)
from legalbot.agents.models import init_stage_model
from legalbot.agents.prompts import ANALYZE_DECIDE_PROMPT, ANALYZE_RESEARCH_PROMPT
from legalbot.agents.schemas.analyze_report import AnalyzeDecision, ResearchStep
from legalbot.agents.stage_messages import prepare_stage_messages
from legalbot.agents.state import (
    AnalyzeOutputState,
    InputState,
    LegalEmailState,
)
from legalbot.agents.tools import list_artifacts, read_artifact, search_related_docs
from legalbot.artifacts.service import ArtifactNotFound, ArtifactService
from legalbot.core.config import get_settings
from legalbot.db.session import async_session_factory

_RESEARCH_TOOLS = [read_artifact, list_artifacts, search_related_docs]


def _session_uuid(state: LegalEmailState) -> uuid.UUID:
    sid = state.get("session_id")
    if not sid:
        raise RuntimeError("analyze graph invoked without session_id")
    return uuid.UUID(sid)


def _run_uuid(state: LegalEmailState) -> uuid.UUID | None:
    rid = state.get("run_id")
    return uuid.UUID(rid) if rid else None


def _research_step_from_tool_message(msg: Any) -> ResearchStep | None:
    name = str(getattr(msg, "name", "") or "unknown")
    content = str(getattr(msg, "content", "") or "")
    target = ""
    finding = content[:4000]
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            if name == "read_artifact":
                target = str(data.get("key") or "")
            elif name == "list_artifacts":
                target = str(data.get("key_prefix") or "")
                keys = data.get("keys") or []
                finding = json.dumps(keys[:30], ensure_ascii=False)
            elif name == "search_related_docs":
                target = str(data.get("query") or "")
    except json.JSONDecodeError:
        pass
    return ResearchStep(tool=name, target=target, finding=finding)


def build_analyze_graph(
    checkpointer: AsyncPostgresSaver | None = None,
) -> StateGraph:
    """Build and compile the analyze StateGraph with input/output schemas."""
    research_model = init_stage_model("analyze_research").bind_tools(_RESEARCH_TOOLS)
    decide_model = init_stage_model("analyze_decide").with_structured_output(AnalyzeDecision)

    async def load_context_node(state: LegalEmailState) -> dict[str, Any]:
        session_id = _session_uuid(state)
        sm = async_session_factory()
        async with sm() as db:
            art_svc = ArtifactService(db)
            try:
                user_input = await load_extracted_user_input(art_svc, session_id)
            except (ArtifactNotFound, LookupError, ValueError):
                return {
                    "analyze_load_failed": True,
                    "analyze_needs_hitl": True,
                    "analyze_blockers": ["analysis/extracted is missing or invalid"],
                }
            await db.commit()
        return {"analyze_user_input": user_input, "analyze_research": [], "analyze_research_recorded": 0}

    async def research_node(state: LegalEmailState) -> dict[str, Any]:
        settings = get_settings()
        max_steps = settings.ANALYZE_MAX_RESEARCH_STEPS
        step_count = len(state.get("analyze_research") or [])
        user_input = state.get("analyze_user_input") or {}
        overview = str(user_input.get("provided_overview") or "").strip()
        step_line = f"Research steps so far: {step_count}/{max_steps}."
        if overview:
            context_note = f"{overview}\n\n{step_line}"
        else:
            context_note = (
                f"{step_line} Extraction inventory: "
                f"{json.dumps(user_input, ensure_ascii=False)[:3000]}"
            )
        messages = prepare_stage_messages(
            state.get("messages", []),
            system_prompt=ANALYZE_RESEARCH_PROMPT,
            fallback_human="Investigate the email and attachments. Record findings via tools only.",
        )
        messages = [*messages, HumanMessage(content=context_note)]
        response = await research_model.ainvoke(messages)
        return {"messages": [response]}

    async def record_research_node(state: LegalEmailState) -> dict[str, Any]:
        messages = state.get("messages", [])
        recorded = int(state.get("analyze_research_recorded") or 0)
        tool_messages = [m for m in messages if getattr(m, "type", None) == "tool"]
        new_steps: list[dict[str, Any]] = []
        for msg in tool_messages[recorded:]:
            step = _research_step_from_tool_message(msg)
            if step is not None:
                new_steps.append(step.model_dump())
        if not new_steps:
            return {"analyze_research_recorded": len(tool_messages)}
        existing = list(state.get("analyze_research") or [])
        return {
            "analyze_research": existing + new_steps,
            "analyze_research_recorded": len(tool_messages),
        }

    async def decide_node(state: LegalEmailState) -> dict[str, Any]:
        user_input = state.get("analyze_user_input") or {}
        research = state.get("analyze_research") or []
        bundle = {
            "user_input": user_input,
            "research": research,
        }
        messages = [
            SystemMessage(content=ANALYZE_DECIDE_PROMPT),
            HumanMessage(content=json.dumps(bundle, ensure_ascii=False)),
        ]
        try:
            decision: AnalyzeDecision = await decide_model.ainvoke(messages)
            return {"analyze_decision": decision.model_dump()}
        except Exception as exc:
            return {
                "analyze_needs_hitl": True,
                "analyze_blockers": [f"structured decision failed: {exc}"],
            }

    async def write_report_node(state: LegalEmailState) -> dict[str, Any]:
        session_id = _session_uuid(state)
        run_id = _run_uuid(state)
        decision = AnalyzeDecision.model_validate(state.get("analyze_decision") or {})
        user_input = state.get("analyze_user_input") or {}
        research = [
            ResearchStep.model_validate(s) for s in (state.get("analyze_research") or [])
        ]
        report = build_analyze_report(
            user_input=user_input,
            research=research,
            decision=decision,
        )
        sm = async_session_factory()
        async with sm() as db:
            art_svc = ArtifactService(db)
            ref_key = await write_analysis_report(
                art_svc,
                session_id=session_id,
                run_id=run_id,
                report=report,
            )
            await db.commit()
        return {}

    async def unsupported_outcome_node(state: LegalEmailState) -> dict[str, Any]:
        session_id = _session_uuid(state)
        run_id = _run_uuid(state)
        decision = AnalyzeDecision.model_validate(state.get("analyze_decision") or {})
        user_input = state.get("analyze_user_input") or {}
        research = [
            ResearchStep.model_validate(s) for s in (state.get("analyze_research") or [])
        ]
        report = build_analyze_report(
            user_input=user_input,
            research=research,
            decision=decision,
        )
        reason = decision.other_reason or "Request is outside supported actions."
        sm = async_session_factory()
        async with sm() as db:
            art_svc = ArtifactService(db)
            report_key = await write_analysis_report(
                art_svc,
                session_id=session_id,
                run_id=run_id,
                report=report,
            )
            outcome_key = await write_unsupported_outcome(
                art_svc,
                session_id=session_id,
                run_id=run_id,
                reason=reason,
            )
            await db.commit()
        return {}

    async def prepare_hitl_node(state: LegalEmailState) -> dict[str, Any]:
        session_id = _session_uuid(state)
        run_id = _run_uuid(state)
        user_input = state.get("analyze_user_input") or {}
        research = [
            ResearchStep.model_validate(s) for s in (state.get("analyze_research") or [])
        ]
        decision_raw = state.get("analyze_decision")
        blockers = list(state.get("analyze_blockers") or [])
        if decision_raw:
            decision = AnalyzeDecision.model_validate(decision_raw)
            if blockers:
                decision.blockers = list(decision.blockers) + blockers
        else:
            decision = AnalyzeDecision(
                intention="unknown",
                confidence="low",
                information_sufficient=False,
                blockers=blockers or ["Insufficient information to proceed"],
            )
        report = build_analyze_report(
            user_input=user_input,
            research=research,
            decision=decision,
        )
        sm = async_session_factory()
        async with sm() as db:
            art_svc = ArtifactService(db)
            ref_key = await write_analysis_report(
                art_svc,
                session_id=session_id,
                run_id=run_id,
                report=report,
            )
            await db.commit()
        return {"analyze_needs_hitl": True}

    def finalize_node(state: LegalEmailState) -> dict[str, Any]:
        return {"stage_result": build_analyze_stage_result(state)}

    def route_after_load(state: LegalEmailState) -> str:
        if state.get("analyze_load_failed"):
            return "prepare_hitl"
        return "research"

    def should_continue_research(state: LegalEmailState) -> str:
        settings = get_settings()
        if len(state.get("analyze_research") or []) >= settings.ANALYZE_MAX_RESEARCH_STEPS:
            return "decide"
        messages = state.get("messages", [])
        if not messages:
            return "decide"
        last_message = messages[-1]
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"
        return "decide"

    def route_after_decide(state: LegalEmailState) -> str:
        if state.get("analyze_needs_hitl") and not state.get("analyze_decision"):
            return "prepare_hitl"
        decision = state.get("analyze_decision") or {}
        if not decision.get("information_sufficient"):
            return "prepare_hitl"
        action = decision.get("action")
        if action == "other":
            return "unsupported"
        return "write_report"

    builder = StateGraph(
        LegalEmailState,
        input_schema=InputState,
        output_schema=AnalyzeOutputState,
    )

    builder.add_node("load_context", load_context_node)
    builder.add_node("research", research_node)
    builder.add_node("tools", ToolNode(_RESEARCH_TOOLS))
    builder.add_node("record_research", record_research_node)
    builder.add_node("decide", decide_node)
    builder.add_node("write_report", write_report_node)
    builder.add_node("unsupported", unsupported_outcome_node)
    builder.add_node("prepare_hitl", prepare_hitl_node)
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "load_context")
    builder.add_conditional_edges(
        "load_context",
        route_after_load,
        {"prepare_hitl": "prepare_hitl", "research": "research"},
    )
    builder.add_conditional_edges(
        "research",
        should_continue_research,
        {"tools": "tools", "decide": "decide"},
    )
    builder.add_edge("tools", "record_research")
    builder.add_edge("record_research", "research")
    builder.add_conditional_edges(
        "decide",
        route_after_decide,
        {
            "prepare_hitl": "prepare_hitl",
            "unsupported": "unsupported",
            "write_report": "write_report",
        },
    )
    builder.add_edge("write_report", "finalize")
    builder.add_edge("unsupported", "finalize")
    builder.add_edge("prepare_hitl", "finalize")
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer)
