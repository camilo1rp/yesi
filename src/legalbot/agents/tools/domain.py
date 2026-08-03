"""Native domain tools (fetch_email, extract attachment, analyze_image, draft, send, schedule).

These tools read from and write to the same database used by the rest of the app.
They're intentionally thin so the heavy work lives in services (attachments,
scheduling, mailbox).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

try:
    from langchain_core.tools import tool
    from langgraph.prebuilt import InjectedState
except Exception:  # pragma: no cover — test shim

    def tool(*args, **kwargs):  # type: ignore[no-redef]
        def deco(fn):
            return fn

        return deco if not args else deco(args[0])

    InjectedState = object  # type: ignore[assignment]

from legalbot.db.session import async_session_factory


def _session_id(state: dict[str, Any]) -> uuid.UUID:
    sid = state.get("session_id")
    if not sid:
        raise RuntimeError("tool called without session_id in state")
    return uuid.UUID(sid)


async def _resolve_attachment(
    db: Any, state: dict[str, Any], attachment_ref: str
) -> Any | None:
    """Resolve attachment by UUID or filename on the session's ingestion item."""
    from sqlalchemy import select

    from legalbot.db.models import (
        EmailMetadata,
        IngestionAttachment,
        IngestionItem,
        ProcessingJob,
    )
    from legalbot.db.models import Session as SessionRow

    try:
        att_id = uuid.UUID(attachment_ref)
        att = (
            await db.execute(
                select(IngestionAttachment).where(IngestionAttachment.id == att_id)
            )
        ).scalar_one_or_none()
        if att is not None:
            return att
    except ValueError:
        pass

    item = (
        await db.execute(
            select(IngestionItem)
            .join(EmailMetadata, EmailMetadata.ingestion_item_id == IngestionItem.id)
            .join(ProcessingJob, ProcessingJob.ingestion_item_id == IngestionItem.id)
            .join(SessionRow, SessionRow.job_id == ProcessingJob.id)
            .where(SessionRow.id == _session_id(state))
        )
    ).scalar_one_or_none()
    if item is None:
        return None

    attachments = (
        (
            await db.execute(
                select(IngestionAttachment).where(
                    IngestionAttachment.ingestion_item_id == item.id
                )
            )
        )
        .scalars()
        .all()
    )
    ref_lower = attachment_ref.lower()
    for att in attachments:
        name = (att.name or "").lower()
        if name == ref_lower or ref_lower in name:
            return att
    return None


@tool
async def fetch_email(
    state: Annotated[dict, InjectedState] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Return the email metadata bound to this session's processing job."""
    state = state or {}
    sm = async_session_factory()
    async with sm() as db:
        from sqlalchemy import select

        from legalbot.db.models import (
            EmailMetadata,
            IngestionAttachment,
            IngestionItem,
            ProcessingJob,
        )
        from legalbot.db.models import (
            Session as SessionRow,
        )

        row = (
            await db.execute(
                select(EmailMetadata, IngestionItem)
                .join(IngestionItem, EmailMetadata.ingestion_item_id == IngestionItem.id)
                .join(ProcessingJob, ProcessingJob.ingestion_item_id == IngestionItem.id)
                .join(SessionRow, SessionRow.job_id == ProcessingJob.id)
                .where(SessionRow.id == _session_id(state))
            )
        ).first()
        if row is None:
            return {"error": "no email bound to this session"}
        em, item = row
        attachments = (
            (
                await db.execute(
                    select(IngestionAttachment).where(
                        IngestionAttachment.ingestion_item_id == item.id
                    )
                )
            )
            .scalars()
            .all()
        )
        return {
            "message_id": em.provider_message_id,
            "thread_id": em.provider_thread_id,
            "from_addr": em.from_addr,
            "to_addrs": em.to_addrs or [],
            "cc_addrs": em.cc_addrs or [],
            "in_reply_to": em.in_reply_to,
            "subject": item.title,
            "received_at": item.received_at.isoformat() if item.received_at else None,
            "body_text": item.body_text,
            "body_html": item.body_html,
            "attachments": [
                {
                    "id": str(a.id),
                    "name": a.name,
                    "mime_type": a.mime_type,
                    "size": a.size,
                }
                for a in attachments
            ],
        }


@tool
async def run_attachment_extraction(
    attachment_id: str,
    state: Annotated[dict, InjectedState] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Extract text and structured data from a PDF/DOCX/XLSX attachment.

    Writes two artifacts per attachment (mirroring the attachment-processor stage):
      - ``extracted_text/<filename>``  — plain extracted text (kind=extracted_text)
      - ``extracted_data/<filename>``  — structured JSON envelope with summary,
        entities, key_findings, and source_file (kind=extracted_data)

    Returns a compact dict with both artifact keys and a text preview so the caller
    can decide whether to read the full artifact.
    """
    state = state or {}
    from sqlalchemy import select

    from legalbot.artifacts.service import ArtifactService
    from legalbot.attachments.dispatcher import extract
    from legalbot.db.models import IngestionAttachment

    sm = async_session_factory()
    async with sm() as db:
        att = await _resolve_attachment(db, state, attachment_id)
        if att is None:
            return {"error": f"attachment not found: {attachment_id}"}

        filename = att.name or "attachment"

        if att.raw_uri:
            from legalbot.artifacts.blobstore import get_blob_store

            data = await get_blob_store().get(att.raw_uri)
        else:
            data = b""

        result = await extract(
            data,
            mime=att.mime_type or "application/octet-stream",
            name=filename,
        )

        plain_text = result.text if result.ok else ""
        data_fields = result.data if result.ok else {}
        structured: dict = {
            "source_file": filename,
            "summary": data_fields.get("summary", plain_text[:200] if plain_text else result.error or ""),
            "entities": data_fields.get("entities", []),
            "key_findings": data_fields.get("key_findings", []),
            "pages": data_fields.get("pages"),
            "rows": data_fields.get("rows"),
        }
        if not result.ok:
            structured["error"] = result.error

        art_svc = ArtifactService(db)
        run_id = uuid.UUID(state["run_id"]) if state.get("run_id") else None
        session_id = _session_id(state)
        meta = {"attachment_id": str(att.id), "source_file": filename}

        text_ref = await art_svc.write(
            session_id=session_id,
            key=f"extracted_text/{filename}",
            content=plain_text,
            kind="extracted_text",
            mime="text/plain",
            producer="extract",
            run_id=run_id,
            metadata=meta,
        )
        data_ref = await art_svc.write(
            session_id=session_id,
            key=f"extracted_data/{filename}",
            content=structured,
            kind="extracted_data",
            mime="application/json",
            producer="extract",
            run_id=run_id,
            metadata=meta,
        )
        await db.commit()
        return {
            "text_artifact_key": text_ref.key,
            "data_artifact_key": data_ref.key,
            "source_file": filename,
            "preview": plain_text[:500],
        }


@tool
async def analyze_image(
    attachment_id: str,
    state: Annotated[dict, InjectedState] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Vision-LLM analysis of an image attachment (PNG/JPG/GIF/WEBP).

    Writes one artifact:
      - ``image_analysis/<filename>`` — JSON with summary, entities, key_findings,
        and source_file (kind=image_analysis)

    Use this instead of ``run_attachment_extraction`` for any image MIME type.
    """
    state = state or {}
    from sqlalchemy import select

    from legalbot.artifacts.blobstore import get_blob_store
    from legalbot.artifacts.service import ArtifactService
    from legalbot.attachments.vision import analyze_image as vision_analyze
    from legalbot.db.models import IngestionAttachment

    sm = async_session_factory()
    async with sm() as db:
        att = await _resolve_attachment(db, state, attachment_id)
        if att is None:
            return {"error": f"attachment not found: {attachment_id}"}

        filename = att.name or "image"
        data = await get_blob_store().get(att.raw_uri) if att.raw_uri else b""
        findings = await vision_analyze(
            data,
            mime=att.mime_type or "image/png",
            name=filename,
        )

        # Normalise vision envelope {method, text, data} into extracted_data shape
        if isinstance(findings, dict) and isinstance(findings.get("data"), dict):
            inner = findings["data"]
            if "summary" in inner or "entities" in inner or "raw" in inner:
                findings = dict(inner)
        if not isinstance(findings, dict):
            findings = {"summary": str(findings), "entities": [], "key_findings": []}
        if "summary" not in findings and "raw" in findings:
            findings = {
                "summary": str(findings.get("raw", "")),
                "entities": findings.get("entities", []),
                "key_findings": findings.get("key_findings", []),
            }
        findings.setdefault("source_file", filename)

        art_svc = ArtifactService(db)
        ref = await art_svc.write(
            session_id=_session_id(state),
            key=f"image_analysis/{filename}",
            content=findings,
            kind="image_analysis",
            mime="application/json",
            producer="extract",
            run_id=uuid.UUID(state["run_id"]) if state.get("run_id") else None,
            metadata={"attachment_id": str(att.id), "source_file": filename},
        )
        await db.commit()
        return {
            "artifact_key": ref.key,
            "source_file": filename,
            "findings": findings,
        }


@tool
async def write_draft(
    subject: str,
    body_text: str,
    to_addrs: list[str] | None = None,
    in_reply_to_message_id: str | None = None,
    state: Annotated[dict, InjectedState] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Persist a draft reply as an artifact + `draft` projection row."""
    state = state or {}
    from sqlalchemy import select

    from legalbot.artifacts.service import ArtifactService
    from legalbot.db.models import Draft
    from legalbot.db.models import Session as SessionRow

    sid = _session_id(state)
    sm = async_session_factory()
    async with sm() as db:
        session_row = (
            await db.execute(select(SessionRow).where(SessionRow.id == sid))
        ).scalar_one()
        art_svc = ArtifactService(db)
        content = {
            "subject": subject,
            "body_text": body_text,
            "to_addrs": to_addrs or [],
            "in_reply_to_message_id": in_reply_to_message_id,
        }
        ref = await art_svc.write(
            session_id=sid,
            key="drafts/reply",
            content=content,
            kind="draft",
            mime="application/json",
            producer="agent",
            run_id=uuid.UUID(state["run_id"]) if state.get("run_id") else None,
            metadata={"subject": subject},
        )
        draft_row = Draft(
            id=uuid.uuid4(),
            job_id=session_row.job_id,
            session_id=sid,
            run_id=uuid.UUID(state["run_id"]) if state.get("run_id") else None,
            artifact_id=ref.id,
            subject=subject,
            body_text=body_text,
            to_addrs=to_addrs or [],
            in_reply_to_message_id=in_reply_to_message_id,
            status="drafted",
        )
        db.add(draft_row)
        await db.commit()
        return {
            "draft_id": str(draft_row.id),
            "artifact_id": str(ref.id),
            "artifact_key": ref.key,
        }


@tool
async def send_draft(
    draft_id: str,
    state: Annotated[dict, InjectedState] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Send a previously-approved draft via the email provider.

    Gated by `HumanInTheLoopMiddleware` (tool_approval). The middleware pauses
    this call until a user accepts, so reaching here means approval happened.
    """
    state = state or {}
    from sqlalchemy import select, update

    from legalbot.db.models import (
        Draft,
        EmailMetadata,
        IngestionItem,
        Mailbox,
        ProcessingJob,
    )
    from legalbot.db.models import (
        Session as SessionRow,
    )
    from legalbot.providers import get as get_adapter
    from legalbot.providers.base import Draft as ProviderDraft

    sm = async_session_factory()
    async with sm() as db:
        draft = (
            await db.execute(select(Draft).where(Draft.id == uuid.UUID(draft_id)))
        ).scalar_one()
        row = (
            await db.execute(
                select(Mailbox, EmailMetadata)
                .join(IngestionItem, IngestionItem.id == EmailMetadata.ingestion_item_id)
                .join(ProcessingJob, ProcessingJob.ingestion_item_id == IngestionItem.id)
                .join(SessionRow, SessionRow.job_id == ProcessingJob.id)
                .join(Mailbox, Mailbox.id == EmailMetadata.mailbox_id)
                .where(SessionRow.id == draft.session_id)
            )
        ).first()
        if row is None:
            return {"error": "could not resolve mailbox for send"}
        mb, em = row
        from legalbot.services.mailbox import MailboxService

        MailboxService(db).with_decrypted(mb)
        adapter = get_adapter(mb.provider)
        send_result = await adapter.send_draft(
            mb,
            ProviderDraft(
                subject=draft.subject,
                body_text=draft.body_text,
                to_addrs=draft.to_addrs or [],
                cc_addrs=draft.cc_addrs or [],
                in_reply_to_message_id=draft.in_reply_to_message_id or em.provider_message_id,
            ),
        )
        await db.execute(update(Draft).where(Draft.id == draft.id).values(status="sent"))
        await db.commit()
        return {
            "sent": send_result.ok,
            "provider_message_id": send_result.provider_message_id,
            "error": send_result.error,
        }


@tool
async def schedule_followup(
    when_iso: str,
    message: str,
    wake_agent: bool = False,
    state: Annotated[dict, InjectedState] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Schedule a one-shot reminder at `when_iso` on this session."""
    state = state or {}
    from datetime import datetime

    from legalbot.scheduling.service import SchedulingService

    sm = async_session_factory()
    async with sm() as db:
        from sqlalchemy import select

        from legalbot.db.models import Session as SessionRow

        sid = _session_id(state)
        session_row = (
            await db.execute(select(SessionRow).where(SessionRow.id == sid))
        ).scalar_one()

        svc = SchedulingService(db)
        row = await svc.schedule_one_shot(
            kind="reminder",
            run_at=datetime.fromisoformat(when_iso),
            payload={
                "session_id": str(sid),
                "message": message,
                "wake_agent": wake_agent,
            },
            owner_user_id=session_row.owner_user_id,
            session_id=sid,
            job_id=session_row.job_id,
            run_id=uuid.UUID(state["run_id"]) if state.get("run_id") else None,
        )
        await db.commit()
        return {"scheduled_job_id": str(row.id), "run_at": when_iso}
