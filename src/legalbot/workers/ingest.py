"""Email-ingestion tasks.

poll_mailboxes (beat-triggered fanout) → poll_mailbox (one per mailbox) →
ingest_message (one transactional write per provider message).

All tasks are idempotent via ON CONFLICT DO NOTHING on `(source, external_id)`
inside `IngestionItemService.upsert`.
"""

from __future__ import annotations

import base64
import uuid
from typing import Any

from sqlalchemy import select

from legalbot.core.logging import get_logger
from legalbot.db.models import Mailbox
from legalbot.db.session import async_session_factory
from legalbot.ingestion.items import IngestionItemService
from legalbot.ingestion.sources.email import map_email
from legalbot.providers import get as get_adapter
from legalbot.providers.base import Cursor, RawAttachment, RawMessage
from legalbot.providers.fake import FakeEmailProvider
from legalbot.services.mailbox import MailboxService
from legalbot.workers._async import run_async
from legalbot.workers.celery_app import celery_app

log = get_logger(__name__)


def _raw_message_from_payload(payload: dict[str, Any]) -> RawMessage:
    attachments: list[RawAttachment] = []
    for row in payload.get("attachments") or []:
        data: bytes | None = None
        if row.get("data_b64"):
            data = base64.b64decode(row["data_b64"])
        attachments.append(
            RawAttachment(
                part_id=row["part_id"],
                name=row["name"],
                mime_type=row.get("mime_type") or "application/octet-stream",
                size=row.get("size"),
                data=data,
            )
        )
    return RawMessage(
        provider_message_id=payload["provider_message_id"],
        provider_thread_id=payload.get("provider_thread_id"),
        subject=payload.get("subject"),
        body_text=payload.get("body_text"),
        body_html=payload.get("body_html"),
        from_addr=payload.get("from_addr"),
        to_addrs=list(payload.get("to_addrs") or []),
        cc_addrs=list(payload.get("cc_addrs") or []),
        bcc_addrs=list(payload.get("bcc_addrs") or []),
        in_reply_to=payload.get("in_reply_to"),
        references=list(payload.get("references") or []),
        headers=dict(payload.get("headers") or {}),
        received_at=None,
        attachments=attachments,
        raw=dict(payload.get("raw") or {}),
    )


@celery_app.task(name="legalbot.workers.ingest.inject_fake_message", bind=True)
def inject_fake_message(self, mailbox_external_id: str, message: dict[str, Any]) -> dict[str, Any]:
    """Queue synthetic mail in the **Celery worker process** (fake inbox is in-memory).

    `docker compose exec … python` cannot reach this memory — use this task or
    ``POST /api/admin/inject-fake`` instead.
    """
    from legalbot.core.config import get_settings

    if not get_settings().DEV_FAKE_PROVIDER_ENABLED:
        return {"ok": False, "error": "DEV_FAKE_PROVIDER_ENABLED is false"}
    msg = _raw_message_from_payload(message)
    FakeEmailProvider.inject_message(mailbox_external_id, msg)
    log.info(
        "inject.fake.queued",
        mailbox_external_id=mailbox_external_id,
        provider_message_id=msg.provider_message_id,
    )
    return {"ok": True, "provider_message_id": msg.provider_message_id}


@celery_app.task(name="legalbot.workers.ingest.poll_mailboxes", bind=True)
def poll_mailboxes(self) -> dict[str, Any]:
    return run_async(_poll_mailboxes())


async def _poll_mailboxes() -> dict[str, Any]:
    sm = async_session_factory()
    async with sm() as db:
        rows = (await db.execute(select(Mailbox).where(Mailbox.state == "active"))).scalars().all()
    count = 0
    for row in rows:
        poll_mailbox.delay(str(row.id))
        count += 1
    log.info("poll.fanout", count=count)
    return {"dispatched": count}


@celery_app.task(name="legalbot.workers.ingest.poll_mailbox", bind=True)
def poll_mailbox(self, mailbox_id: str) -> dict[str, Any]:
    return run_async(_poll_mailbox(mailbox_id))


async def _poll_mailbox(mailbox_id: str) -> dict[str, Any]:
    sm = async_session_factory()
    mb_id = uuid.UUID(mailbox_id)
    async with sm() as db:
        svc = MailboxService(db)
        mb = await svc.get(mb_id)
        svc.with_decrypted(mb)
        adapter = get_adapter(mb.provider)
        messages, new_cursor = await adapter.list_new_messages(mb, Cursor(value=mb.cursor or {}))
        mb.cursor = new_cursor.value
        await db.commit()

    dispatched = 0
    for raw in messages:
        ingest_message.delay(mailbox_id, raw.provider_message_id)
        dispatched += 1
    log.info("poll.mailbox.done", mailbox_id=mailbox_id, dispatched=dispatched)
    return {"dispatched": dispatched}


@celery_app.task(name="legalbot.workers.ingest.ingest_message", bind=True)
def ingest_message(self, mailbox_id: str, provider_message_id: str) -> dict[str, Any]:
    return run_async(_ingest_message(mailbox_id, provider_message_id))


async def _ingest_message(mailbox_id: str, provider_message_id: str) -> dict[str, Any]:
    sm = async_session_factory()
    mb_id = uuid.UUID(mailbox_id)
    async with sm() as db:
        svc = MailboxService(db)
        mb = await svc.get(mb_id)
        svc.with_decrypted(mb)
        adapter = get_adapter(mb.provider)
        raw = await adapter.fetch_message(mb, provider_message_id)

        attachments_bytes: dict[str, bytes] = {}
        for att in raw.attachments:
            if att.data is not None:
                attachments_bytes[att.name] = att.data
            else:
                try:
                    attachments_bytes[att.name] = await adapter.download_attachment(
                        mb, raw.provider_message_id, att.part_id
                    )
                except Exception as e:  # pragma: no cover
                    log.warning(
                        "ingest.attachment_download_failed",
                        provider_message_id=provider_message_id,
                        part_id=att.part_id,
                        error=str(e),
                    )
                    attachments_bytes[att.name] = b""

        mapped = map_email(raw)
        item_svc = IngestionItemService(db)
        item, job = await item_svc.upsert(
            mapped=mapped,
            attachments_bytes=attachments_bytes,
            owner_user_id=mb.owner_user_id,
            mailbox_id=mb.id,
        )
        await db.commit()
        log.info(
            "ingest.done",
            mailbox_id=mailbox_id,
            provider_message_id=provider_message_id,
            item_id=str(item.id),
            job_id=str(job.id) if job else None,
        )
        return {"item_id": str(item.id), "job_id": str(job.id) if job else None}
