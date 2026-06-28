from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest

import legalbot.artifacts.service as artifacts_service
from legalbot.agents.tools import contract_tools

_NDA_VALUES = {
    "disclosing_party": "Acme Corp.",
    "receiving_party": "Beta LLC",
    "effective_date": "January 1, 2026",
    "purpose": "evaluating a partnership",
    "term_months": "24",
    "governing_law": "State of Delaware",
}


def _callable(tool_obj: Any) -> Callable[..., Any]:
    """Return the underlying coroutine/function for a (possibly @tool-wrapped) tool."""
    return getattr(tool_obj, "coroutine", None) or getattr(tool_obj, "func", None) or tool_obj


@pytest.mark.asyncio
async def test_fill_contract_template_unsupported_type_writes_nothing() -> None:
    result = await _callable(contract_tools.fill_contract_template)(
        contract_type="franchise_agreement",
        field_values={},
        state={"session_id": str(uuid.uuid4())},
    )
    assert result["status"] == "unsupported_type"
    assert "nda" in result["supported"]


@pytest.mark.asyncio
async def test_fill_contract_template_reports_missing_fields() -> None:
    result = await _callable(contract_tools.fill_contract_template)(
        contract_type="nda",
        field_values={"disclosing_party": "Acme"},
        state={"session_id": str(uuid.uuid4())},
    )
    assert result["status"] == "missing_fields"
    assert "receiving_party" in result["missing"]
    assert "disclosing_party" not in result["missing"]


class _FakeRef:
    def __init__(self, key: str) -> None:
        self.id = uuid.uuid4()
        self.key = key
        self.version = 1


class _FakeArtifactService:
    """Captures the write call so the test can assert on rendered content."""

    last_write: dict[str, Any] = {}

    def __init__(self, db: Any) -> None:
        self.db = db

    async def write(self, **kwargs: Any) -> _FakeRef:
        _FakeArtifactService.last_write = kwargs
        return _FakeRef(kwargs["key"])


@pytest.mark.asyncio
async def test_fill_contract_template_writes_rendered_contract_artifact(monkeypatch) -> None:
    class _FakeDB:
        async def commit(self) -> None:
            return None

    @asynccontextmanager
    async def _fake_sm():
        yield _FakeDB()

    monkeypatch.setattr(contract_tools, "async_session_factory", lambda: _fake_sm)
    monkeypatch.setattr(artifacts_service, "ArtifactService", _FakeArtifactService)

    result = await _callable(contract_tools.fill_contract_template)(
        contract_type="nda",
        field_values=_NDA_VALUES,
        state={"session_id": str(uuid.uuid4())},
    )

    assert result["status"] == "filled"
    assert result["artifact_key"] == "contracts/draft"

    write = _FakeArtifactService.last_write
    assert write["key"] == "contracts/draft"
    assert write["kind"] == "contract"
    assert write["content"]["contract_type"] == "nda"
    assert "Acme Corp." in write["content"]["markdown"]
    assert "{{" not in write["content"]["markdown"]


def test_search_contract_examples_tool_returns_results() -> None:
    out = _callable(contract_tools.search_contract_examples)(
        contract_type="nda", query="confidentiality term", k=2
    )
    assert out["contract_type"] == "nda"
    assert out["results"]
