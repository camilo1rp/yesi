"""MailboxService — create/fetch mailboxes, encrypt/decrypt credentials, OAuth helpers."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from legalbot.db.models import Mailbox
from legalbot.providers import get as get_adapter
from legalbot.providers.base import Cursor, MailboxCredentials
from legalbot.providers.crypto import (
    decrypt_credentials,
    encrypt_credentials,
)


class MailboxService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self,
        *,
        provider: str,
        external_id: str,
        display_name: str | None,
        credentials: MailboxCredentials,
        owner_user_id: str,
    ) -> Mailbox:
        row = Mailbox(
            id=uuid.uuid4(),
            provider=provider,
            external_id=external_id,
            display_name=display_name,
            credentials=encrypt_credentials(credentials),
            owner_user_id=owner_user_id,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def get(self, mailbox_id: uuid.UUID) -> Mailbox:
        result = await self.db.execute(select(Mailbox).where(Mailbox.id == mailbox_id))
        return result.scalar_one()

    async def list(self, *, owner_user_id: str | None = None) -> list[Mailbox]:
        stmt = select(Mailbox)
        if owner_user_id:
            stmt = stmt.where(Mailbox.owner_user_id == owner_user_id)
        result = await self.db.execute(stmt)
        return list(result.scalars())

    async def delete(self, mailbox_id: uuid.UUID) -> None:
        row = await self.get(mailbox_id)
        await self.db.delete(row)

    def with_decrypted(self, mb: Mailbox) -> Mailbox:
        if mb.credentials:
            mb.credentials_decrypted = decrypt_credentials(mb.credentials)  # type: ignore[attr-defined]
        else:
            mb.credentials_decrypted = None  # type: ignore[attr-defined]
        return mb

    async def poll(self, mailbox_id: uuid.UUID) -> tuple[list[Any], Cursor]:
        mb = await self.get(mailbox_id)
        self.with_decrypted(mb)
        adapter = get_adapter(mb.provider)
        cursor = Cursor(value=mb.cursor or {})
        messages, new_cursor = await adapter.list_new_messages(mb, cursor)
        mb.cursor = new_cursor.value
        await self.db.flush()
        return messages, new_cursor
