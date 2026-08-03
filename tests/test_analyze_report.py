"""Unit tests for analyze report schemas and stage result projection."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from legalbot.agents.analyze_report import (
    build_action_payload,
    build_analyze_report,
    build_analyze_stage_result,
    resources_from_research,
    write_analysis_report,
    write_unsupported_outcome,
)
from legalbot.agents.schemas.analyze_report import (
    AnalyzeAction,
    AnalyzeDecision,
    CreateContractPayload,
    DraftResponsePayload,
    MissingInformation,
    ResearchStep,
)


def test_build_action_payload_create_contract() -> None:
    decision = AnalyzeDecision(
        intention="Draft NDA",
        confidence="high",
        information_sufficient=True,
        action=AnalyzeAction.create_contract,
        create_contract=CreateContractPayload(
            contract_type_hint="NDA",
            provided_fields={"party_a": "Acme"},
            missing_fields=["effective_date"],
            evidence_refs=["extracted_data/nda.docx"],
        ),
    )
    payload = build_action_payload(decision)
    assert payload["contract_type_hint"] == "NDA"
    assert payload["provided_fields"]["party_a"] == "Acme"
    assert payload["missing_fields"] == ["effective_date"]


def test_build_analyze_report_hitl_path() -> None:
    decision = AnalyzeDecision(
        intention="Unclear request",
        confidence="low",
        information_sufficient=False,
        action=None,
        missing_information=[
            MissingInformation(
                field="effective_date",
                question="What is the effective date?",
                why_needed="Required for contract drafting",
            )
        ],
    )
    report = build_analyze_report(
        user_input={"subject": "NDA please"},
        research=[],
        decision=decision,
    )
    assert report.action is None
    assert len(report.missing_information) == 1
    assert report.action_payload == {}


def test_resources_from_research_parses_kg_hits() -> None:
    steps = [
        ResearchStep(
            tool="search_related_docs",
            target="Acme",
            finding='{"query":"Acme","results":[{"ingestion_item_id":"abc-123","snippet":"Acme Corp"}]}',
        ),
        ResearchStep(tool="read_artifact", target="extracted_data/memo.pdf", finding="ok"),
    ]
    resources = resources_from_research(steps)
    types = {r.type for r in resources}
    assert "ingestion_item" in types
    assert "artifact" in types


def test_build_analyze_stage_result_completed_contract() -> None:
    result = build_analyze_stage_result(
        {
            "analyze_decision": {
                "action": "create_contract",
                "information_sufficient": True,
            },
            "analyze_user_input": {"subject": "NDA"},
            "messages": [],
        }
    )
    assert result["status"] == "completed"
    assert result["primary_artifact_key"] == "analysis/report"
    assert result["next_stage"] == "draft_contract"
    assert "analysis/report" in result["artifact_keys"]


def test_build_analyze_stage_result_awaiting_human() -> None:
    result = build_analyze_stage_result(
        {
            "analyze_needs_hitl": True,
            "analyze_decision": {
                "information_sufficient": False,
                "missing_information": [
                    {"field": "date", "question": "When?", "why_needed": "needed"}
                ],
            },
            "messages": [],
        }
    )
    assert result["status"] == "awaiting_human"
    assert result["needs_human"] is True
    assert result["next_stage"] is None


def test_build_analyze_stage_result_other_skips_act() -> None:
    result = build_analyze_stage_result(
        {
            "analyze_decision": {
                "action": "other",
                "information_sufficient": True,
            },
            "analyze_user_input": {},
            "messages": [],
        }
    )
    assert result["next_stage"] is None


@pytest.mark.asyncio
async def test_write_analysis_report_calls_artifact_service() -> None:
    art_svc = MagicMock()
    art_svc.write = AsyncMock(return_value=MagicMock(key="analysis/report"))
    key = await write_analysis_report(
        art_svc,
        session_id=__import__("uuid").uuid4(),
        run_id=None,
        report=build_analyze_report(
            user_input={},
            research=[],
            decision=AnalyzeDecision(
                intention="reply",
                confidence="high",
                information_sufficient=True,
                action=AnalyzeAction.draft_response,
                draft_response=DraftResponsePayload(response_outline="Say thanks"),
            ),
        ),
    )
    assert key == "analysis/report"
    art_svc.write.assert_awaited_once()


@pytest.mark.asyncio
async def test_write_unsupported_outcome() -> None:
    art_svc = MagicMock()
    art_svc.write = AsyncMock(return_value=MagicMock(key="act/outcome"))
    key = await write_unsupported_outcome(
        art_svc,
        session_id=__import__("uuid").uuid4(),
        run_id=None,
        reason="Unsupported",
    )
    assert key == "act/outcome"
    call_kwargs = art_svc.write.await_args.kwargs
    assert call_kwargs["content"]["action_type"] == "unsupported"


def test_analyze_graph_compiles() -> None:
    from legalbot.agents.analyze_graph import build_analyze_graph

    graph = build_analyze_graph(checkpointer=None)
    assert graph is not None
