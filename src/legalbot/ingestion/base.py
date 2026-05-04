"""Generic source wrapper. Each source provides a mapper from its raw payload
into the common `ingestion_item` + detail-table shape.

For `email`, the mapper takes a `RawMessage` and returns:
- the fields to persist on `ingestion_item` (source='email', external_id, title, bodies, sender)
- the fields to persist on `email_metadata`
- the list of `ingestion_attachment` rows (raw_uri + mime + name).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AttachmentSpec:
    name: str
    mime_type: str | None
    size: int | None
    raw_uri: str | None


@dataclass(slots=True)
class MappedItem:
    source: str
    external_id: str
    title: str | None
    body_text: str | None
    body_html: str | None
    sender_identity: str | None
    raw_payload: dict[str, Any]
    received_at: Any | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    attachments: list[AttachmentSpec] = field(default_factory=list)
