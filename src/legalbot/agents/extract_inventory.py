"""Build and persist a rich ``analysis/extracted`` inventory for the analyze stage."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from legalbot.agents.stage_result import build_stage_result, _artifact_keys_from_messages
from legalbot.artifacts.service import ArtifactNotFound, ArtifactService
from legalbot.db.models import (
    EmailMetadata,
    IngestionAttachment,
    IngestionItem,
    ProcessingJob,
)
from legalbot.db.models import Session as SessionRow
from legalbot.db.session import async_session_factory

_CLASSIFICATION_FIELDS = (
    "body_summary",
    "action_requested",
    "sender_trust",
    "deadline",
    "referenced_documents",
)

_PREVIEW_MAX = 500
_FINDINGS_MAX = 8
_ENTITIES_MAX = 12


async def load_email_for_session(session_id: uuid.UUID) -> dict[str, Any]:
    """Load ingestion email + attachment metadata bound to a session."""
    sm = async_session_factory()
    async with sm() as db:
        row = (
            await db.execute(
                select(EmailMetadata, IngestionItem)
                .join(IngestionItem, EmailMetadata.ingestion_item_id == IngestionItem.id)
                .join(ProcessingJob, ProcessingJob.ingestion_item_id == IngestionItem.id)
                .join(SessionRow, SessionRow.job_id == ProcessingJob.id)
                .where(SessionRow.id == session_id)
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
            "body_text": item.body_text or "",
            "body_html": item.body_html,
            "attachments": [
                {
                    "id": str(a.id),
                    "name": a.name or "attachment",
                    "mime_type": a.mime_type,
                    "size": a.size,
                }
                for a in attachments
            ],
        }


def _entity_names(entities: Any) -> list[str]:
    names: list[str] = []
    if not isinstance(entities, list):
        return names
    for ent in entities:
        if isinstance(ent, dict):
            name = str(ent.get("name") or ent.get("value") or "").strip()
        else:
            name = str(ent).strip()
        if name:
            names.append(name)
    return names


async def _read_artifact_content(
    art_svc: ArtifactService,
    session_id: uuid.UUID,
    key: str,
    *,
    thread_fallback: bool = False,
) -> Any | None:
    try:
        _, content = await art_svc.read_resolved(
            session_id=session_id,
            key_or_id=key,
            thread_fallback=thread_fallback,
        )
        return content
    except (ArtifactNotFound, LookupError):
        return None


async def _collect_thread_attachment_rows(
    art_svc: ArtifactService,
    session_id: uuid.UUID,
    email_thread_id: str | None,
) -> list[dict[str, Any]]:
    """Summarize extracted attachments from sibling emails in the same thread."""
    if not email_thread_id:
        return []

    sibling_ids = await art_svc.session_ids_in_email_thread(session_id)
    rows: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    for sibling_id in sibling_ids:
        if sibling_id == session_id:
            continue
        sibling_email = await load_email_for_session(sibling_id)
        if sibling_email.get("error"):
            continue
        for meta in sibling_email.get("attachments") or []:
            name = str(meta.get("name") or "attachment")
            if name in seen_names:
                continue
            seen_names.add(name)
            mime = meta.get("mime_type")
            is_image = str(mime or "").startswith("image/")
            data_key = f"extracted_data/{name}"
            text_key = f"extracted_text/{name}"
            image_key = f"image_analysis/{name}"

            data_exists = await _read_artifact_content(
                art_svc, sibling_id, data_key, thread_fallback=False
            ) is not None
            image_exists = await _read_artifact_content(
                art_svc, sibling_id, image_key, thread_fallback=False
            ) is not None
            text_exists = await _read_artifact_content(
                art_svc, sibling_id, text_key, thread_fallback=False
            ) is not None

            if is_image:
                active_data_key = image_key if image_exists else None
                active_text_key = None
                active_image_key = image_key if image_exists else None
            else:
                active_data_key = data_key if data_exists else None
                active_text_key = text_key if text_exists else None
                active_image_key = None

            readable = (
                active_data_key is not None
                or active_text_key is not None
                or active_image_key is not None
            )
            preview = await _attachment_content_preview(
                art_svc,
                sibling_id,
                data_key=active_data_key,
                text_key=active_text_key,
                image_key=active_image_key,
            )
            rows.append(
                {
                    "name": name,
                    "mime_type": mime,
                    "source_session_id": str(sibling_id),
                    "source_subject": sibling_email.get("subject"),
                    "data_artifact_key": active_data_key,
                    "text_artifact_key": active_text_key,
                    "image_artifact_key": active_image_key,
                    "readable": readable,
                    **preview,
                }
            )
    return rows


async def _attachment_content_preview(
    art_svc: ArtifactService,
    session_id: uuid.UUID,
    *,
    data_key: str | None,
    text_key: str | None,
    image_key: str | None,
) -> dict[str, Any]:
    preview: dict[str, Any] = {
        "content_summary": "",
        "key_findings": [],
        "entities_preview": [],
        "text_chars": 0,
        "extraction_method": None,
    }

    structured_key = data_key or image_key
    if structured_key:
        data = await _read_artifact_content(art_svc, session_id, structured_key)
        if isinstance(data, dict):
            preview["content_summary"] = str(data.get("summary") or "")[:_PREVIEW_MAX]
            findings = data.get("key_findings") or []
            if isinstance(findings, list):
                preview["key_findings"] = [str(f) for f in findings[:_FINDINGS_MAX]]
            entities = _entity_names(data.get("entities"))
            preview["entities_preview"] = entities[:_ENTITIES_MAX]
            if data.get("error"):
                preview["extraction_error"] = str(data.get("error"))
            method = data.get("method") or data.get("vision_skipped")
            if method:
                preview["extraction_method"] = str(method)

    if text_key:
        text = await _read_artifact_content(art_svc, session_id, text_key)
        if isinstance(text, str):
            preview["text_chars"] = len(text)
            if not preview["content_summary"] and text.strip():
                preview["content_summary"] = text.strip()[:_PREVIEW_MAX]

    return preview


def _classification_from_existing(existing: dict[str, Any] | None) -> dict[str, Any]:
    if not existing:
        return {}
    out: dict[str, Any] = {}
    for field in _CLASSIFICATION_FIELDS:
        if field in existing and existing[field] is not None:
            out[field] = existing[field]
    return out


def _build_provided_overview(inventory: dict[str, Any]) -> str:
    parts: list[str] = []
    subject = str(inventory.get("subject") or "").strip()
    if subject:
        parts.append(f"Subject: {subject}")
    from_addr = str(inventory.get("from_addr") or "").strip()
    if from_addr:
        parts.append(f"From: {from_addr}")

    body_summary = str(inventory.get("body_summary") or "").strip()
    body_preview = str(inventory.get("body_preview") or "").strip()
    if body_summary:
        parts.append(f"Email: {body_summary}")
    elif body_preview:
        parts.append(f"Email preview: {body_preview[:300]}")

    action = str(inventory.get("action_requested") or "").strip()
    if action:
        parts.append(f"Sender request type: {action}")

    attachments = inventory.get("attachments") or []
    if not attachments:
        parts.append("Attachments: none")
    else:
        parts.append(f"Attachments: {len(attachments)} file(s)")
        for att in attachments:
            if not isinstance(att, dict):
                continue
            name = att.get("name") or "file"
            if not att.get("processed"):
                err = att.get("error") or att.get("extraction_error") or "failed"
                parts.append(f"  - {name}: not processed ({err})")
                continue
            summary = str(att.get("content_summary") or "").strip()
            findings = att.get("key_findings") or []
            entities = att.get("entities_preview") or []
            line = f"  - {name}:"
            if summary:
                line += f" {summary[:200]}"
            if entities:
                line += f" [entities: {', '.join(entities[:5])}]"
            if findings:
                line += f" [findings: {len(findings)}]"
            if att.get("text_chars"):
                line += f" [text: {att['text_chars']} chars]"
            parts.append(line)

    referenced = inventory.get("referenced_documents") or []
    if referenced:
        parts.append(f"Referenced in email: {referenced}")

    thread_attachments = inventory.get("thread_attachments") or []
    if thread_attachments:
        parts.append("Attachments from other messages in this email thread:")
        for att in thread_attachments:
            if not isinstance(att, dict):
                continue
            name = att.get("name") or "file"
            if not att.get("readable"):
                parts.append(f"  - {name}: not extracted in thread")
                continue
            data_key = att.get("data_artifact_key") or att.get("text_artifact_key")
            summary = str(att.get("content_summary") or "").strip()
            line = f"  - {name}:"
            if data_key:
                line += f" read via `{data_key}`"
            if summary:
                line += f" {summary[:200]}"
            parts.append(line)

    return "\n".join(parts)


async def build_extract_inventory(
    art_svc: ArtifactService,
    session_id: uuid.UUID,
    *,
    classification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble a rich inventory from DB email row + session artifacts."""
    email = await load_email_for_session(session_id)
    if email.get("error"):
        return {"error": email["error"]}

    existing_classification = classification or {}
    try:
        _, existing_content = await art_svc.read(
            session_id=session_id, key_or_id="analysis/extracted"
        )
        if isinstance(existing_content, dict):
            existing_classification = {
                **_classification_from_existing(existing_content),
                **existing_classification,
            }
    except (ArtifactNotFound, LookupError):
        pass

    body_text = str(email.get("body_text") or "")
    inventory: dict[str, Any] = {
        "subject": email.get("subject"),
        "from_addr": email.get("from_addr"),
        "received_at": email.get("received_at"),
        "body_preview": body_text[:_PREVIEW_MAX] if body_text else "",
        "body_chars": len(body_text),
        "message_id": email.get("message_id"),
        "thread_id": email.get("thread_id"),
        "attachment_count": len(email.get("attachments") or []),
        **existing_classification,
    }

    if not inventory.get("body_summary") and body_text.strip():
        inventory["body_summary"] = body_text.strip()[:280]

    attachment_rows: list[dict[str, Any]] = []
    processed = 0
    failed = 0

    for meta in email.get("attachments") or []:
        name = str(meta.get("name") or "attachment")
        mime = meta.get("mime_type")
        is_image = str(mime or "").startswith("image/")
        data_key = f"extracted_data/{name}"
        text_key = f"extracted_text/{name}"
        image_key = f"image_analysis/{name}"

        data_exists = await _read_artifact_content(art_svc, session_id, data_key) is not None
        image_exists = await _read_artifact_content(art_svc, session_id, image_key) is not None
        text_exists = await _read_artifact_content(art_svc, session_id, text_key) is not None

        if is_image:
            active_data_key = image_key if image_exists else None
            active_text_key = None
            active_image_key = image_key if image_exists else None
        else:
            active_data_key = data_key if data_exists else None
            active_text_key = text_key if text_exists else None
            active_image_key = None

        ok = active_data_key is not None or active_text_key is not None or active_image_key is not None
        preview = await _attachment_content_preview(
            art_svc,
            session_id,
            data_key=active_data_key,
            text_key=active_text_key,
            image_key=active_image_key,
        )

        row: dict[str, Any] = {
            "id": meta.get("id"),
            "name": name,
            "mime_type": mime,
            "size": meta.get("size"),
            "data_artifact_key": active_data_key,
            "text_artifact_key": active_text_key,
            "image_artifact_key": active_image_key,
            "processed": ok,
            "error": None if ok else "not_extracted",
            **preview,
        }
        if not ok:
            failed += 1
        else:
            processed += 1
        attachment_rows.append(row)

    inventory["attachments"] = attachment_rows
    inventory["attachments_processed"] = processed
    inventory["attachments_failed"] = failed
    inventory["thread_attachments"] = await _collect_thread_attachment_rows(
        art_svc, session_id, str(email.get("thread_id") or "") or None
    )
    inventory["provided_overview"] = _build_provided_overview(inventory)
    return inventory


async def write_extract_inventory(
    art_svc: ArtifactService,
    *,
    session_id: uuid.UUID,
    run_id: uuid.UUID | None,
    inventory: dict[str, Any],
) -> str:
    ref = await art_svc.write(
        session_id=session_id,
        key="analysis/extracted",
        content=inventory,
        kind="analysis",
        mime="application/json",
        producer="extract",
        run_id=run_id,
    )
    return ref.key


def build_extract_stage_result(state: dict[str, Any]) -> dict[str, Any]:
    """Finalize envelope for the extract subgraph."""
    artifact_keys = sorted(
        _artifact_keys_from_messages(state.get("messages", [])) | {"analysis/extracted"}
    )
    result = build_stage_result(
        state,
        stage="extract",
        primary_artifact_key="analysis/extracted",
        next_stage="analyze",
    )
    result["artifact_keys"] = artifact_keys
    result["primary_artifact_key"] = "analysis/extracted"
    return result
