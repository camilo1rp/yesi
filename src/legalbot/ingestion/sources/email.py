"""Map a provider `RawMessage` into the generic `MappedItem` for email."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from legalbot.ingestion.base import AttachmentSpec, MappedItem
from legalbot.providers.base import RawMessage


def map_email(msg: RawMessage) -> MappedItem:
    sender = msg.from_addr or ""
    detail: dict[str, Any] = {
        "provider_message_id": msg.provider_message_id,
        "provider_thread_id": msg.provider_thread_id,
        "from_addr": msg.from_addr,
        "to_addrs": msg.to_addrs,
        "cc_addrs": msg.cc_addrs,
        "bcc_addrs": msg.bcc_addrs,
        "in_reply_to": msg.in_reply_to,
        "references": msg.references,
        "raw_headers": msg.headers,
    }
    return MappedItem(
        source="email",
        external_id=msg.provider_message_id,
        title=msg.subject,
        body_text=msg.body_text,
        body_html=msg.body_html,
        sender_identity=sender,
        raw_payload={"raw": msg.raw, "headers": msg.headers},
        received_at=msg.received_at,
        detail=detail,
        attachments=[
            AttachmentSpec(
                name=a.name,
                mime_type=a.mime_type,
                size=a.size,
                raw_uri=None,
            )
            for a in msg.attachments
        ],
    )


def raw_message_to_dict(msg: RawMessage) -> dict[str, Any]:
    return asdict(msg)
