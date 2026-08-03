"""Tests for rich analysis/extracted inventory building."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from legalbot.agents.extract_inventory import (
    _build_provided_overview,
    build_extract_inventory,
    build_extract_stage_result,
)


def test_build_provided_overview_lists_attachments() -> None:
    inventory = {
        "subject": "NDA request",
        "from_addr": "partner@firm.test",
        "body_summary": "Please draft an NDA.",
        "action_requested": "review_document",
        "attachments": [
            {
                "name": "prior_nda.docx",
                "processed": True,
                "content_summary": "Mutual NDA template",
                "entities_preview": ["Acme Corp", "Jane Doe"],
                "text_chars": 4200,
            },
            {
                "name": "broken.pdf",
                "processed": False,
                "error": "not_extracted",
            },
        ],
    }
    overview = _build_provided_overview(inventory)
    assert "NDA request" in overview
    assert "prior_nda.docx" in overview
    assert "Acme Corp" in overview
    assert "not processed" in overview


def test_build_provided_overview_lists_thread_attachments() -> None:
    inventory = {
        "subject": "Draft NDA",
        "attachments": [],
        "thread_attachments": [
            {
                "name": "executed_nda.docx",
                "readable": True,
                "data_artifact_key": "extracted_data/executed_nda.docx",
                "content_summary": "Executed mutual NDA",
            }
        ],
    }
    overview = _build_provided_overview(inventory)
    assert "other messages in this email thread" in overview
    assert "executed_nda.docx" in overview
    assert "extracted_data/executed_nda.docx" in overview


def test_build_extract_stage_result_always_lists_primary_artifact() -> None:
    result = build_extract_stage_result({"messages": []})
    assert result["primary_artifact_key"] == "analysis/extracted"
    assert "analysis/extracted" in result["artifact_keys"]
    assert result["next_stage"] == "analyze"


@pytest.mark.asyncio
async def test_build_extract_inventory_merges_artifact_previews() -> None:
    session_id = uuid.uuid4()
    art_svc = MagicMock()
    art_svc.read = AsyncMock(
        side_effect=[
            (MagicMock(), {"body_summary": "Draft NDA", "action_requested": "review_document"}),
        ]
    )
    art_svc.read_resolved = AsyncMock(
        side_effect=[
            (MagicMock(), {"summary": "NDA template", "entities": [{"name": "Acme Corp"}], "key_findings": ["24 months"]}),
            (MagicMock(), None),
            (MagicMock(), "Full mutual NDA text with parties and terms."),
            (MagicMock(), {"summary": "NDA template", "entities": [{"name": "Acme Corp"}], "key_findings": ["24 months"]}),
            (MagicMock(), "Full mutual NDA text with parties and terms."),
        ]
    )
    art_svc.session_ids_in_email_thread = AsyncMock(return_value=[session_id])

    email_payload = {
        "subject": "NDA",
        "from_addr": "a@b.test",
        "received_at": "2026-01-01T00:00:00",
        "body_text": "Please draft NDA using attached template.",
        "attachments": [
            {
                "id": "att-1",
                "name": "prior_nda.docx",
                "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "size": 1000,
            }
        ],
    }

    with patch(
        "legalbot.agents.extract_inventory.load_email_for_session",
        new=AsyncMock(return_value=email_payload),
    ):
        inventory = await build_extract_inventory(art_svc, session_id)

    assert inventory["action_requested"] == "review_document"
    assert inventory["attachments_processed"] == 1
    assert inventory["provided_overview"]
    att = inventory["attachments"][0]
    assert att["processed"] is True
    assert att["content_summary"] == "NDA template"
    assert "Acme Corp" in att["entities_preview"]
    assert att["text_chars"] > 0
