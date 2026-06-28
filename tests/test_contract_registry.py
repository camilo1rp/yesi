from __future__ import annotations

import pytest

from legalbot.contracts import (
    TemplateRenderError,
    classify_contract_type,
    list_contract_types,
    missing_fields,
    render_template,
    search_contract_examples,
    template_placeholders,
)

_NDA_VALUES = {
    "disclosing_party": "Acme Corp.",
    "receiving_party": "Beta LLC",
    "effective_date": "January 1, 2026",
    "purpose": "evaluating a partnership",
    "term_months": "24",
    "governing_law": "State of Delaware",
}


def test_list_contract_types_exposes_seed_types() -> None:
    type_ids = {t["type_id"] for t in list_contract_types()}
    assert {"nda", "service_agreement"} <= type_ids


def test_classify_single_match_resolves_type() -> None:
    result = classify_contract_type("Please draft an NDA for our vendor")
    assert result == {"type_id": "nda", "candidates": ["nda"]}


def test_classify_ambiguous_returns_multiple_candidates_and_no_type() -> None:
    result = classify_contract_type("we need an NDA and a services agreement")
    assert result["type_id"] is None
    assert set(result["candidates"]) == {"nda", "service_agreement"}


def test_classify_unsupported_returns_no_candidates() -> None:
    assert classify_contract_type("write me a haiku") == {"type_id": None, "candidates": []}


def test_missing_fields_lists_only_absent_or_blank() -> None:
    provided = {"disclosing_party": "Acme", "receiving_party": "  ", "purpose": "x"}
    missing = missing_fields("nda", provided)
    assert "disclosing_party" not in missing
    assert "receiving_party" in missing  # blank counts as missing
    assert "effective_date" in missing


def test_render_template_fills_all_placeholders() -> None:
    rendered = render_template("nda", _NDA_VALUES)
    assert "{{" not in rendered
    assert "Acme Corp." in rendered
    assert "State of Delaware" in rendered


def test_render_template_raises_when_any_field_unfilled() -> None:
    incomplete = dict(_NDA_VALUES)
    del incomplete["governing_law"]
    with pytest.raises(TemplateRenderError):
        render_template("nda", incomplete)


def test_template_placeholders_subset_of_required_fields() -> None:
    for type_summary in list_contract_types():
        type_id = type_summary["type_id"]
        field_names = {f["name"] for f in type_summary["required_fields"]}
        assert set(template_placeholders(type_id)) <= field_names


def test_search_contract_examples_ranks_relevant_snippet_first() -> None:
    results = search_contract_examples("service_agreement", "net 30 invoice late interest", k=3)
    assert results
    assert results[0]["score"] >= results[-1]["score"]
    assert "net 30" in results[0]["snippet"].lower()


def test_search_contract_examples_unknown_type_is_empty() -> None:
    assert search_contract_examples("does_not_exist", "anything") == []
