from __future__ import annotations

import json

from langchain_core.messages import AIMessage, ToolMessage

from legalbot.agents.stage_result import build_stage_result


def test_build_stage_result_projects_artifact_keys_and_summary() -> None:
    result = build_stage_result(
        {
            "messages": [
                ToolMessage(
                    content=json.dumps(
                        {
                            "key": "analysis/extracted",
                            "data_artifact_key": "extracted_data/memo.pdf",
                        }
                    ),
                    name="write_artifact",
                    tool_call_id="call-1",
                ),
                AIMessage(content="Extraction completed with one document artifact."),
            ]
        },
        stage="extract",
        primary_artifact_key="analysis/extracted",
        next_stage="analyze",
    )

    assert result == {
        "stage": "extract",
        "status": "completed",
        "summary": "Extraction completed with one document artifact.",
        "primary_artifact_key": "analysis/extracted",
        "artifact_keys": ["analysis/extracted", "extracted_data/memo.pdf"],
        "needs_human": False,
        "next_stage": "analyze",
    }


def test_build_stage_result_marks_last_human_tool_as_awaiting_human() -> None:
    result = build_stage_result(
        {
            "messages": [
                ToolMessage(
                    content=json.dumps({"request_id": "interrupt-1"}),
                    name="ask_human",
                    tool_call_id="call-1",
                ),
            ]
        },
        stage="analyze",
        primary_artifact_key="analysis/summary",
        next_stage="act",
    )

    assert result["status"] == "awaiting_human"
    assert result["needs_human"] is True
    assert result["next_stage"] is None
