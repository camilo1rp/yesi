"""IngestionItemService — one transactional write per inbound message.

ON CONFLICT (source, external_id) DO NOTHING — idempotent ingestion.
When the conflict fires (duplicate), the service returns `(None, False)` and the caller
skips the rest of Stage 1 (no processing_job created, cursor can still advance).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from legalbot.core.logging import get_logger
from legalbot.db.models import (
    EmailMetadata,
    IngestionAttachment,
    IngestionItem,
    ProcessingJob,
)
from legalbot.db.types import JobState
from legalbot.ingestion.base import MappedItem
from legalbot.jobs.service import JobService

log = get_logger(__name__)


class IngestionItemService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def upsert(
        self,
        *,
        mapped: MappedItem,
        attachments_bytes: dict[str, bytes] | None,
        owner_user_id: str,
        mailbox_id: uuid.UUID | None = None,
    ) -> tuple[IngestionItem, Any | None]:
        """Idempotent ingestion: write blob(s), create item + job, or skip on conflict."""
        from legalbot.artifacts.blobstore import get_blob_store

        blob = get_blob_store()
        if attachments_bytes:
            for spec in mapped.attachments:
                data = attachments_bytes.get(spec.name) or attachments_bytes.get(spec.raw_uri or "")
                if data and not spec.raw_uri:
                    ref = f"ingestion/{mapped.source}/{mapped.external_id}/{spec.name}"
                    await blob.put(ref, data, mime=spec.mime_type or "application/octet-stream")
                    spec.raw_uri = ref
                    if spec.size is None:
                        spec.size = len(data)

        row, created = await self.create_with_attachments(
            mapped, owner_user_id=owner_user_id, mailbox_id=mailbox_id
        )
        if not created:
            return row, None
        job = await self.promote_to_ready(row, owner_user_id=owner_user_id)
        return row, job

    async def create_with_attachments(
        self,
        mapped: MappedItem,
        *,
        owner_user_id: str,
        mailbox_id: uuid.UUID | None = None,
    ) -> tuple[IngestionItem | None, bool]:
        """Returns (row, created)."""

        stmt = (
            pg_insert(IngestionItem)
            .values(
                id=uuid.uuid4(),
                source=mapped.source,
                external_id=mapped.external_id,
                title=mapped.title,
                body_text=mapped.body_text,
                body_html=mapped.body_html,
                sender_identity=mapped.sender_identity,
                raw_payload=mapped.raw_payload,
                received_at=mapped.received_at,
                owner_user_id=owner_user_id,
            )
            .on_conflict_do_nothing(index_elements=["source", "external_id"])
            .returning(IngestionItem.id)
        )
        result = await self.db.execute(stmt)
        new_id = result.scalar_one_or_none()

        if new_id is None:
            existing = await self.db.execute(
                select(IngestionItem).where(
                    IngestionItem.source == mapped.source,
                    IngestionItem.external_id == mapped.external_id,
                )
            )
            row = existing.scalar_one()
            return row, False

        # Source-specific detail insert.
        if mapped.source == "email":
            self.db.add(
                EmailMetadata(
                    ingestion_item_id=new_id,
                    mailbox_id=mailbox_id,
                    provider_message_id=mapped.detail.get("provider_message_id"),
                    provider_thread_id=mapped.detail.get("provider_thread_id"),
                    from_addr=mapped.detail.get("from_addr"),
                    to_addrs=mapped.detail.get("to_addrs"),
                    cc_addrs=mapped.detail.get("cc_addrs"),
                    bcc_addrs=mapped.detail.get("bcc_addrs"),
                    in_reply_to=mapped.detail.get("in_reply_to"),
                    references=mapped.detail.get("references"),
                    raw_headers=mapped.detail.get("raw_headers"),
                )
            )
        for att in mapped.attachments:
            self.db.add(
                IngestionAttachment(
                    id=uuid.uuid4(),
                    ingestion_item_id=new_id,
                    name=att.name,
                    mime_type=att.mime_type,
                    size=att.size,
                    raw_uri=att.raw_uri,
                )
            )
        await self.db.flush()

        result = await self.db.execute(select(IngestionItem).where(IngestionItem.id == new_id))
        row = result.scalar_one()
        log.info(
            "ingestion.item.created",
            item_id=str(row.id),
            source=mapped.source,
            external_id=mapped.external_id,
            attachment_count=len(mapped.attachments),
        )
        return row, True

    async def promote_to_ready(
        self,
        item: IngestionItem,
        *,
        owner_user_id: str,
    ) -> ProcessingJob:
        """Creates the processing_job row and transitions intake -> ready atomically."""

        svc = JobService(self.db)
        job = await svc.create_for_item(
            ingestion_item_id=item.id,
            owner_user_id=owner_user_id,
            initial_state=JobState.intake,
        )
        if job.state == JobState.intake:
            await svc.transition(job.id, from_state=JobState.intake, to_state=JobState.ready)
        return job  # type: ignore[return-value]
