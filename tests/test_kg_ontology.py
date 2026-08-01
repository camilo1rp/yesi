"""Tests for legal ontology helpers."""

from __future__ import annotations

from legalbot.memory.ontology import (
    EntityType,
    normalize_name,
    parse_email_address,
    person_canonical_key,
    person_canonical_name,
    Relation,
)


def test_normalize_name_collapses_whitespace() -> None:
    assert normalize_name("  Jane   Doe  ") == "jane doe"


def test_parse_email_address_display_and_email() -> None:
    display, email = parse_email_address("Jane Doe <jane@acme.com>")
    assert display == "Jane Doe"
    assert email == "jane@acme.com"


def test_parse_email_address_plain_email() -> None:
    display, email = parse_email_address("jane@acme.com")
    assert display is None
    assert email == "jane@acme.com"


def test_person_canonical_key_lowercases() -> None:
    assert person_canonical_key("Jane@Acme.COM") == "email:jane@acme.com"


def test_person_canonical_name_prefers_display() -> None:
    assert person_canonical_name("Jane Doe", "jane@acme.com") == "Jane Doe"
    assert person_canonical_name(None, "jane@acme.com") == "jane@acme.com"


def test_entity_and_relation_enums_cover_legal_ontology() -> None:
    assert EntityType.PERSON == "Person"
    assert EntityType.CONTRACT == "Contract"
    assert Relation.REPLIES_TO == "REPLIES_TO"
    assert Relation.PARTY_TO == "PARTY_TO"
