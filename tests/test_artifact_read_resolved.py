"""Tests for artifact key resolution and thread-scoped reads."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from legalbot.artifacts.keys import artifact_key_candidates
from legalbot.artifacts.service import ArtifactNotFound, ArtifactService


def test_artifact_key_candidates_expands_bare_filename() -> None:
    keys = artifact_key_candidates("executed_nda.docx")
    assert keys == [
        "extracted_data/executed_nda.docx",
        "extracted_text/executed_nda.docx",
        "image_analysis/executed_nda.docx",
    ]


def test_artifact_key_candidates_preserves_full_key() -> None:
    assert artifact_key_candidates("extracted_data/memo.pdf") == ["extracted_data/memo.pdf"]


@pytest.mark.asyncio
async def test_read_resolved_normalizes_filename_in_current_session() -> None:
    session_id = uuid.uuid4()
    db = MagicMock()
    svc = ArtifactService(db)
    row = MagicMock(key="extracted_data/nda.docx", session_id=session_id, storage="inline")
    row.content_inline = {"summary": "ok"}
    svc.read = AsyncMock(
        side_effect=[
            ArtifactNotFound("missing"),
            (row, {"summary": "ok"}),
        ]
    )
    svc.session_ids_in_email_thread = AsyncMock(return_value=[session_id])

    out_row, content = await svc.read_resolved(session_id=session_id, key_or_id="nda.docx")
    assert out_row.key == "extracted_data/nda.docx"
    assert content == {"summary": "ok"}


@pytest.mark.asyncio
async def test_read_resolved_falls_back_to_thread_sibling_session() -> None:
    current_sid = uuid.uuid4()
    parent_sid = uuid.uuid4()
    db = MagicMock()
    svc = ArtifactService(db)
    parent_row = MagicMock(
        key="extracted_data/executed_nda.docx",
        session_id=parent_sid,
        storage="inline",
    )
    parent_row.content_inline = {"summary": "executed"}

    async def _read(**kwargs: object) -> tuple[MagicMock, dict]:
        sid = kwargs["session_id"]
        key = kwargs["key_or_id"]
        if sid == current_sid:
            raise ArtifactNotFound("missing")
        if sid == parent_sid and key == "extracted_data/executed_nda.docx":
            return parent_row, {"summary": "executed"}
        raise ArtifactNotFound("missing")

    svc.read = AsyncMock(side_effect=_read)
    svc.session_ids_in_email_thread = AsyncMock(return_value=[parent_sid, current_sid])

    out_row, content = await svc.read_resolved(
        session_id=current_sid,
        key_or_id="executed_nda.docx",
    )
    assert out_row.session_id == parent_sid
    assert content["summary"] == "executed"
