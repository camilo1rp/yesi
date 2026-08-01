"""Legal-domain ontology: entity/relation types and canonical key builders."""

from __future__ import annotations

import re
import uuid
from enum import StrEnum

_ADDR_RE = re.compile(r"^(.+?)\s*<([^>]+)>$")
_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")


class EntityType(StrEnum):
    PERSON = "Person"
    ORGANIZATION = "Organization"
    CASE = "Case"
    CONTRACT = "Contract"
    DOCUMENT = "Document"
    EMAIL = "Email"
    ATTACHMENT = "Attachment"
    CLAUSE = "Clause"
    COURT = "Court"
    JURISDICTION = "Jurisdiction"
    DATE = "Date"
    MONETARY_AMOUNT = "MonetaryAmount"


class Relation(StrEnum):
    MENTIONS = "MENTIONS"
    SENT_BY = "SENT_BY"
    SENT_TO = "SENT_TO"
    REPLIES_TO = "REPLIES_TO"
    ATTACHED_TO = "ATTACHED_TO"
    PARTY_TO = "PARTY_TO"
    REPRESENTS = "REPRESENTS"
    BELONGS_TO_CASE = "BELONGS_TO_CASE"
    SUPERSEDES = "SUPERSEDES"
    REFERENCES = "REFERENCES"
    SIGNED_BY = "SIGNED_BY"
    GOVERNED_BY = "GOVERNED_BY"
    HAS_DEADLINE = "HAS_DEADLINE"
    EMPLOYED_BY = "EMPLOYED_BY"
    AFFILIATED_WITH = "AFFILIATED_WITH"
    CONCERNS = "CONCERNS"


def normalize_name(value: str) -> str:
    """Collapse whitespace and lowercase for fuzzy comparison."""
    return " ".join((value or "").strip().split()).lower()


def parse_email_address(raw: str) -> tuple[str | None, str]:
    """Return (display_name, email) from a RFC-like address string."""
    text = (raw or "").strip()
    if not text:
        return None, ""
    match = _ADDR_RE.match(text)
    if match:
        display = match.group(1).strip().strip('"').strip("'")
        email = match.group(2).strip().lower()
        return display or None, email
    return None, text.lower()


def person_canonical_key(email: str) -> str:
    return f"email:{email.strip().lower()}"


def ingestion_item_canonical_key(item_id: uuid.UUID) -> str:
    return f"ingestion_item:{item_id}"


def ingestion_attachment_canonical_key(attachment_id: uuid.UUID) -> str:
    return f"ingestion_attachment:{attachment_id}"


def person_canonical_name(display_name: str | None, email: str) -> str:
    if display_name:
        return display_name.strip()
    return email
