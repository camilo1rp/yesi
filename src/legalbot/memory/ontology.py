"""Legal-domain ontology: entity/relation types and canonical key builders."""

from __future__ import annotations

import re
import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

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


def artifact_canonical_key(session_id: uuid.UUID, artifact_key: str) -> str:
    """Stable document node id for a session-scoped analysis artifact."""
    return f"artifact:{session_id}:{artifact_key}"


def attachment_filename_key(filename: str) -> str:
    """Placeholder for linking Attachment entities during projection apply."""
    return f"__attachment__:{normalize_name(filename)}"


def person_canonical_name(display_name: str | None, email: str) -> str:
    if display_name:
        return display_name.strip()
    return email


_ORG_SUFFIX_RE = re.compile(
    r"\b(corp\.?|inc\.?|llc|l\.l\.c\.?|ltd\.?|co\.?|company|gmbh|s\.a\.?)\b",
    re.I,
)


def infer_entity_type_from_name(name: str) -> EntityType:
    """Heuristic when artifact entities lack an explicit type."""
    if _ORG_SUFFIX_RE.search(name or ""):
        return EntityType.ORGANIZATION
    return EntityType.PERSON


def organization_canonical_key(name: str, domain: str | None = None) -> str:
    if domain:
        return f"domain:{domain.strip().lower()}"
    return f"name:{normalize_name(name)}"


def contract_canonical_key(contract_type: str, parties: list[str]) -> str:
    ctype = (contract_type or "unknown").strip().lower()
    normalized = sorted({normalize_name(p) for p in parties if (p or "").strip()})
    fingerprint = "|".join(normalized)
    return f"contract:{ctype}|{fingerprint}"


def law_area_canonical_key(area: str) -> str:
    return f"law_area:{normalize_name(area)}"


def is_party_field_name(field_name: str) -> bool:
    lower = (field_name or "").lower()
    return "party" in lower or lower in {"disclosing_party", "receiving_party"}


class ProjectionEntity(BaseModel):
    """One entity to upsert during run projection (Phase 2)."""

    entity_type: EntityType
    canonical_key: str
    canonical_name: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    aliases: list[str] = Field(default_factory=list)
    snippet: str | None = None
    confidence: float = 0.85
    origin: str = "artifact"


class ProjectionEdge(BaseModel):
    """Edge between two projection entities, keyed by canonical (type, key) tuples."""

    src_type: EntityType
    src_key: str
    dst_type: EntityType
    dst_key: str
    relation: Relation
    attributes: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.85
    origin: str = "artifact"


class GraphProjection(BaseModel):
    entities: list[ProjectionEntity] = Field(default_factory=list)
    edges: list[ProjectionEdge] = Field(default_factory=list)


class LlmExtractedEntity(BaseModel):
    entity_type: EntityType
    name: str
    aliases: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.75


class LlmGraphExtraction(BaseModel):
    """Structured output schema for optional LLM enrichment."""

    entities: list[LlmExtractedEntity] = Field(default_factory=list)
    relations: list[dict[str, Any]] = Field(default_factory=list)
