"""Mailbox CRUD + OAuth endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from legalbot.api.schemas import MailboxCreate, MailboxRead
from legalbot.db.session import get_session
from legalbot.providers.base import MailboxCredentials
from legalbot.services.mailbox import MailboxService

router = APIRouter()


@router.get("", response_model=list[MailboxRead])
async def list_mailboxes(
    owner_user_id: str | None = None,
    db: AsyncSession = Depends(get_session),
) -> list[MailboxRead]:
    rows = await MailboxService(db).list(owner_user_id=owner_user_id)
    return [MailboxRead.model_validate(row, from_attributes=True) for row in rows]


@router.post("", response_model=MailboxRead)
async def create_mailbox(
    body: MailboxCreate,
    db: AsyncSession = Depends(get_session),
) -> MailboxRead:
    creds = MailboxCredentials(
        access_token=body.access_token,
        refresh_token=body.refresh_token,
    )
    svc = MailboxService(db)
    row = await svc.create(
        provider=body.provider,
        external_id=body.external_id,
        display_name=body.display_name,
        credentials=creds,
        owner_user_id=body.owner_user_id,
    )
    await db.commit()
    return MailboxRead.model_validate(row, from_attributes=True)


@router.get("/{mailbox_id}", response_model=MailboxRead)
async def get_mailbox(
    mailbox_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
) -> MailboxRead:
    try:
        row = await MailboxService(db).get(mailbox_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return MailboxRead.model_validate(row, from_attributes=True)


@router.delete("/{mailbox_id}", status_code=204)
async def delete_mailbox(
    mailbox_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
) -> None:
    await MailboxService(db).delete(mailbox_id)
    await db.commit()


@router.get("/{mailbox_id}/oauth/start")
async def oauth_start(
    mailbox_id: uuid.UUID,
    owner_user_id: str,
    db: AsyncSession = Depends(get_session),
) -> dict:
    svc = MailboxService(db)
    mb = await svc.get(mailbox_id)
    from legalbot.providers import get as get_adapter

    adapter = get_adapter(mb.provider)
    started = await adapter.authorize_start(owner_user_id)
    return {"authorization_url": started.authorization_url, "state": started.state}
