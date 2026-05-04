"""Ingestion-items router (generic) + `/emails` typed view."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from legalbot.api.schemas import EmailView, IngestionItemRead
from legalbot.db.models import EmailMetadata, IngestionItem
from legalbot.db.session import get_session

router = APIRouter()


@router.get("", response_model=list[IngestionItemRead])
async def list_items(
    source: str | None = None,
    owner_user_id: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_session),
) -> list[IngestionItemRead]:
    stmt = select(IngestionItem)
    if source:
        stmt = stmt.where(IngestionItem.source == source)
    if owner_user_id:
        stmt = stmt.where(IngestionItem.owner_user_id == owner_user_id)
    stmt = stmt.order_by(IngestionItem.received_at.desc().nullslast()).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return [IngestionItemRead.model_validate(r, from_attributes=True) for r in rows]


@router.get("/{item_id}", response_model=IngestionItemRead)
async def get_item(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
) -> IngestionItemRead:
    row = (
        await db.execute(select(IngestionItem).where(IngestionItem.id == item_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return IngestionItemRead.model_validate(row, from_attributes=True)


email_router = APIRouter()


@email_router.get("", response_model=list[EmailView])
async def list_emails(
    owner_user_id: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_session),
) -> list[EmailView]:
    stmt = (
        select(IngestionItem, EmailMetadata)
        .join(EmailMetadata, EmailMetadata.ingestion_item_id == IngestionItem.id)
        .where(IngestionItem.source == "email")
        .order_by(IngestionItem.received_at.desc().nullslast())
        .limit(limit)
    )
    if owner_user_id:
        stmt = stmt.where(IngestionItem.owner_user_id == owner_user_id)

    rows = (await db.execute(stmt)).all()
    out: list[EmailView] = []
    for item, meta in rows:
        out.append(
            EmailView(
                id=item.id,
                source=item.source,
                external_id=item.external_id,
                title=item.title,
                sender_identity=item.sender_identity,
                received_at=item.received_at,
                owner_user_id=item.owner_user_id,
                ingested_at=item.ingested_at,
                from_addr=meta.from_addr,
                to_addrs=meta.to_addrs or [],
                subject=item.title,
                body_text_preview=(item.body_text or "")[:500],
            )
        )
    return out
