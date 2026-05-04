"""In-memory fake provider used for dev + tests.

Registers itself as provider_id='fake'. State lives in module-level dicts so a
developer shell or test can inject synthetic messages (with or without
attachments) via `inject_message` and the rest of the ingestion pipeline
(poll → fetch → ingest → dispatch → run) behaves identically to a real
adapter.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from legalbot.providers import registry
from legalbot.providers.base import (
    Cursor,
    Draft,
    MailboxCredentials,
    OAuthStartResult,
    RawAttachment,
    RawMessage,
    SendResult,
)


class FakeEmailProvider:
    provider_id: ClassVar[str] = "fake"
    ingestion_mode: ClassVar[str] = "poll"

    # mailbox external_id -> ordered list of pending provider_message_ids
    _inbox: ClassVar[dict[str, list[str]]] = {}
    # provider_message_id -> RawMessage (for fetch_message lookups after list)
    _messages: ClassVar[dict[str, RawMessage]] = {}
    _sent: ClassVar[list[RawMessage]] = []

    @classmethod
    def inject_message(cls, mailbox_external_id: str, msg: RawMessage) -> None:
        """Queue a synthetic message to be returned on the next poll."""
        cls._messages[msg.provider_message_id] = msg
        cls._inbox.setdefault(mailbox_external_id, []).append(msg.provider_message_id)

    async def authorize_start(self, owner_user_id: str) -> OAuthStartResult:
        return OAuthStartResult(
            authorization_url=f"https://example.invalid/fake-auth?user={owner_user_id}",
            state=uuid.uuid4().hex,
        )

    async def authorize_complete(self, code: str, state: str) -> MailboxCredentials:
        return MailboxCredentials(access_token=f"fake-{code}", scopes=["mail.read"])

    async def list_new_messages(self, mb: Any, cursor: Cursor) -> tuple[list[RawMessage], Cursor]:
        external_id = getattr(mb, "external_id", None) or mb.get("external_id")
        ids = self._inbox.pop(external_id, [])
        pending = [self._messages[i] for i in ids if i in self._messages]
        new_cursor = Cursor(value={"last_seen_at": datetime.now(UTC).isoformat()})
        return pending, new_cursor

    async def fetch_message(self, mb: Any, provider_message_id: str) -> RawMessage:
        msg = self._messages.get(provider_message_id)
        if msg is None:
            raise KeyError(provider_message_id)
        return msg

    async def download_attachment(self, mb: Any, message_id: str, part_id: str) -> bytes:
        msg = self._messages.get(message_id)
        if msg is None:
            raise KeyError(message_id)
        for att in msg.attachments:
            if att.part_id == part_id and att.data is not None:
                return att.data
        raise KeyError(f"{message_id}/{part_id}")

    async def send_draft(self, mb: Any, draft: Draft) -> SendResult:
        sent = RawMessage(
            provider_message_id=uuid.uuid4().hex,
            provider_thread_id=None,
            subject=draft.subject,
            body_text=draft.body_text,
            body_html=None,
            from_addr=None,
            to_addrs=draft.to_addrs,
        )
        self._sent.append(sent)
        return SendResult(provider_message_id=sent.provider_message_id, ok=True)

    @classmethod
    def sent(cls) -> list[RawMessage]:
        return list(cls._sent)

    @classmethod
    def reset(cls) -> None:
        cls._inbox.clear()
        cls._messages.clear()
        cls._sent.clear()


registry.register(FakeEmailProvider())


__all__ = ["FakeEmailProvider", "RawAttachment"]
