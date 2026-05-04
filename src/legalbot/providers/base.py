"""EmailProviderAdapter Protocol + provider-agnostic dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar, Protocol, runtime_checkable


@dataclass(slots=True)
class MailboxCredentials:
    access_token: str | None = None
    refresh_token: str | None = None
    token_uri: str | None = None
    scopes: list[str] = field(default_factory=list)
    expires_at: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Cursor:
    value: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RawAttachment:
    part_id: str
    name: str
    mime_type: str
    size: int | None = None
    data: bytes | None = None


@dataclass(slots=True)
class RawMessage:
    provider_message_id: str
    provider_thread_id: str | None
    subject: str | None
    body_text: str | None
    body_html: str | None
    from_addr: str | None
    to_addrs: list[str] = field(default_factory=list)
    cc_addrs: list[str] = field(default_factory=list)
    bcc_addrs: list[str] = field(default_factory=list)
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)
    headers: dict[str, Any] = field(default_factory=dict)
    received_at: datetime | None = None
    attachments: list[RawAttachment] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OAuthStartResult:
    authorization_url: str
    state: str


@dataclass(slots=True)
class SendResult:
    provider_message_id: str | None
    ok: bool
    error: str | None = None


@dataclass(slots=True)
class Draft:
    subject: str | None
    body_text: str | None
    to_addrs: list[str] = field(default_factory=list)
    cc_addrs: list[str] = field(default_factory=list)
    in_reply_to_message_id: str | None = None


@runtime_checkable
class EmailProviderAdapter(Protocol):
    provider_id: ClassVar[str]
    ingestion_mode: ClassVar[str]

    async def authorize_start(self, owner_user_id: str) -> OAuthStartResult: ...

    async def authorize_complete(self, code: str, state: str) -> MailboxCredentials: ...

    async def list_new_messages(
        self, mb: Any, cursor: Cursor
    ) -> tuple[list[RawMessage], Cursor]: ...

    async def fetch_message(self, mb: Any, provider_message_id: str) -> RawMessage: ...

    async def download_attachment(self, mb: Any, message_id: str, part_id: str) -> bytes: ...

    async def send_draft(self, mb: Any, draft: Draft) -> SendResult: ...
