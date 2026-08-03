"""Build and persist analysis/report artifacts from structured analyze state."""

from __future__ import annotations

import json
import uuid
from typing import Any

from legalbot.agents.schemas.analyze_report import (
    AnalyzeAction,
    AnalyzeDecision,
    AnalyzeReport,
    ResearchStep,
    ResourceRef,
)
from legalbot.agents.stage_result import build_stage_result, _artifact_keys_from_messages
from legalbot.artifacts.service import ArtifactNotFound, ArtifactService


def resources_from_research(steps: list[ResearchStep]) -> list[ResourceRef]:
    """Derive resource refs from recorded research steps."""
    resources: list[ResourceRef] = []
    seen: set[tuple[str, str]] = set()
    for step in steps:
        if step.tool in ("read_artifact", "list_artifacts") and step.target:
            key = ("artifact", step.target)
            if key not in seen:
                seen.add(key)
                resources.append(
                    ResourceRef(
                        type="artifact",
                        ref=step.target,
                        relevance=step.finding[:200],
                    )
                )
        if step.tool == "search_related_docs":
            try:
                payload = json.loads(step.finding) if step.finding.startswith("{") else {}
            except json.JSONDecodeError:
                payload = {}
            for hit in payload.get("results") or []:
                if not isinstance(hit, dict):
                    continue
                item_id = hit.get("ingestion_item_id") or hit.get("source_id")
                if item_id:
                    key = ("ingestion_item", str(item_id))
                    if key not in seen:
                        seen.add(key)
                        resources.append(
                            ResourceRef(
                                type="ingestion_item",
                                ref=str(item_id),
                                relevance=str(hit.get("snippet") or hit.get("title") or "")[:200],
                            )
                        )
    return resources


def build_action_payload(decision: AnalyzeDecision) -> dict[str, Any]:
    if decision.action == AnalyzeAction.draft_response and decision.draft_response:
        return decision.draft_response.model_dump()
    if decision.action == AnalyzeAction.create_contract and decision.create_contract:
        return decision.create_contract.model_dump()
    if decision.action == AnalyzeAction.other:
        return {"reason": decision.other_reason or ""}
    return {}


def build_analyze_report(
    *,
    user_input: dict[str, Any],
    research: list[ResearchStep],
    decision: AnalyzeDecision,
    resources: list[ResourceRef] | None = None,
) -> AnalyzeReport:
    rel = resources if resources is not None else resources_from_research(research)
    return AnalyzeReport(
        user_input=user_input,
        intention=decision.intention,
        research=research,
        relevant_resources=rel,
        action=decision.action,
        action_payload=build_action_payload(decision),
        confidence=decision.confidence,
        blockers=list(decision.blockers),
        missing_information=list(decision.missing_information),
    )


async def write_analysis_report(
    art_svc: ArtifactService,
    *,
    session_id: uuid.UUID,
    run_id: uuid.UUID | None,
    report: AnalyzeReport,
) -> str:
    ref = await art_svc.write(
        session_id=session_id,
        key="analysis/report",
        content=report.model_dump(),
        kind="analysis",
        mime="application/json",
        producer="analyze",
        run_id=run_id,
    )
    return ref.key


async def write_unsupported_outcome(
    art_svc: ArtifactService,
    *,
    session_id: uuid.UUID,
    run_id: uuid.UUID | None,
    reason: str,
) -> str:
    ref = await art_svc.write(
        session_id=session_id,
        key="act/outcome",
        content={
            "action_type": "unsupported",
            "reason": reason,
            "report_key": "analysis/report",
        },
        kind="outcome",
        mime="application/json",
        producer="analyze",
        run_id=run_id,
    )
    return ref.key


def hitl_summary(state: dict[str, Any]) -> str:
    blockers = state.get("analyze_blockers") or []
    decision = state.get("analyze_decision") or {}
    missing = decision.get("missing_information") or []
    parts: list[str] = []
    if blockers:
        parts.append("Blockers: " + "; ".join(str(b) for b in blockers))
    if missing:
        qs = [f"{m.get('field')}: {m.get('question')}" for m in missing if isinstance(m, dict)]
        parts.append("Missing: " + "; ".join(qs))
    return " | ".join(parts) if parts else "Analyze stage needs human input."


def build_analyze_stage_result(state: dict[str, Any]) -> dict[str, Any]:
    """Finalize envelope for the analyze subgraph."""
    artifact_keys = sorted(_artifact_keys_from_messages(state.get("messages", [])))
    idx = state.get("artifact_index") or {}
    for key in idx:
        artifact_keys.append(str(key))
    artifact_keys = sorted(set(artifact_keys))
    if "analysis/report" not in artifact_keys and (
        state.get("analyze_decision")
        or state.get("analyze_needs_hitl")
        or state.get("analyze_user_input")
    ):
        artifact_keys.append("analysis/report")
        artifact_keys = sorted(set(artifact_keys))

    if state.get("analyze_needs_hitl") or state.get("analyze_load_failed"):
        result = build_stage_result(
            state,
            stage="analyze",
            primary_artifact_key="analysis/report",
            next_stage=None,
        )
        result["status"] = "awaiting_human"
        result["needs_human"] = True
        result["next_stage"] = None
        result["summary"] = hitl_summary(state)
        result["artifact_keys"] = artifact_keys
        if "analysis/report" not in artifact_keys:
            result["primary_artifact_key"] = None
        else:
            result["primary_artifact_key"] = "analysis/report"
        return result

    next_stage = "act"
    decision = state.get("analyze_decision") or {}
    action = decision.get("action")
    if action == AnalyzeAction.create_contract.value:
        next_stage = "draft_contract"
    elif action == AnalyzeAction.other.value:
        next_stage = None

    result = build_stage_result(
        state,
        stage="analyze",
        primary_artifact_key="analysis/report",
        next_stage=next_stage,
    )
    result["artifact_keys"] = artifact_keys
    if "analysis/report" in artifact_keys:
        result["primary_artifact_key"] = "analysis/report"
    return result


async def load_extracted_user_input(
    art_svc: ArtifactService,
    session_id: uuid.UUID,
) -> dict[str, Any]:
    _, content = await art_svc.read(session_id=session_id, key_or_id="analysis/extracted")
    if not isinstance(content, dict):
        raise ArtifactNotFound("analysis/extracted is not a JSON object")
    return {
        "subject": content.get("subject"),
        "from_addr": content.get("from_addr"),
        "received_at": content.get("received_at"),
        "body_summary": content.get("body_summary"),
        "body_preview": content.get("body_preview"),
        "body_chars": content.get("body_chars"),
        "action_requested": content.get("action_requested"),
        "sender_trust": content.get("sender_trust"),
        "deadline": content.get("deadline"),
        "referenced_documents": content.get("referenced_documents") or [],
        "provided_overview": content.get("provided_overview"),
        "attachment_count": content.get("attachment_count"),
        "attachments_processed": content.get("attachments_processed"),
        "attachments_failed": content.get("attachments_failed"),
        "attachments": content.get("attachments") or [],
        "thread_attachments": content.get("thread_attachments") or [],
    }
