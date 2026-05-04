"""Admin endpoints for manual dispatch / sweeper / rebuild triggers."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, HTTPException
from fastapi.openapi.models import Example

from legalbot.api.schemas import FakeInjectCreate
from legalbot.core.config import get_settings

router = APIRouter()

# OpenAPI only — keeps Swagger ``/docs`` aligned with getting-started §5.3.
_INJECT_FAKE_OPENAPI_EXAMPLES: dict[str, Example] = {
    "simple": {
        "summary": "Single text email (getting-started §5)",
        "description": (
            "Only **mailbox_external_id** and **message.provider_message_id** are "
            "required for a minimal run. All other message fields are optional—omit "
            "them or set to null. **headers** / **raw** default to {} if omitted."
        ),
        "value": {
            "mailbox_external_id": "dev-inbox",
            "message": {
                "provider_message_id": "msg-1",
                "provider_thread_id": None,
                "subject": "Contract review request",
                "body_text": "Client wants the NDA reviewed by Friday.",
                "body_html": None,
                "from_addr": "client@acme.test",
                "to_addrs": ["dev@example.com"],
            },
        },
    },
}


@router.post("/dispatch")
async def dispatch_now() -> dict:
    from legalbot.workers.janitors import dispatch_ready_jobs

    result = dispatch_ready_jobs.delay()
    return {"task_id": result.id}


@router.post("/reap")
async def reap_now() -> dict:
    from legalbot.workers.janitors import reap_stuck_jobs

    result = reap_stuck_jobs.delay()
    return {"task_id": result.id}


@router.post("/poll")
async def poll_now() -> dict:
    from legalbot.workers.ingest import poll_mailboxes

    result = poll_mailboxes.delay()
    return {"task_id": result.id}


@router.post("/inject-fake")
async def inject_fake(
    body: Annotated[
        FakeInjectCreate,
        Body(openapi_examples=_INJECT_FAKE_OPENAPI_EXAMPLES),
    ],
) -> dict[str, str]:
    """Enqueue fake mail into the Celery worker (in-memory fake provider).

    Shell ``docker compose exec … python`` runs a different process than the
    worker and cannot see the fake inbox — use this endpoint or the
    ``inject_fake_message`` Celery task instead.
    """
    if not get_settings().DEV_FAKE_PROVIDER_ENABLED:
        raise HTTPException(status_code=403, detail="DEV_FAKE_PROVIDER_ENABLED is false")
    from legalbot.workers.ingest import inject_fake_message

    payload = body.message.model_dump(mode="json")
    result = inject_fake_message.delay(body.mailbox_external_id, payload)
    return {"task_id": result.id}


@router.post("/rebuild-redbeat")
async def rebuild_redbeat() -> dict:
    from legalbot.workers.janitors import rebuild_redbeat_from_db

    result = rebuild_redbeat_from_db.delay()
    return {"task_id": result.id}
