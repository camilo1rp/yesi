"""Knowledge graph indexing Celery tasks."""

from __future__ import annotations

import uuid

from legalbot.core.config import get_settings
from legalbot.core.logging import get_logger
from legalbot.db.session import async_session_factory
from legalbot.memory.service import KnowledgeGraphService
from legalbot.workers._async import run_async
from legalbot.workers.celery_app import celery_app

log = get_logger(__name__)


@celery_app.task(name="legalbot.workers.graph.graph_index_item", bind=True)
def graph_index_item(self, ingestion_item_id: str) -> dict:
    return run_async(_graph_index_item(ingestion_item_id))


async def _graph_index_item(ingestion_item_id: str) -> dict:
    settings = get_settings()
    if not settings.KG_ENABLED:
        return {"ok": False, "reason": "disabled"}

    try:
        item_id = uuid.UUID(ingestion_item_id)
    except ValueError:
        return {"ok": False, "reason": "invalid_id"}

    sm = async_session_factory()
    async with sm() as db:
        svc = KnowledgeGraphService(db)
        result = await svc.index_ingestion_item(item_id)
        await db.commit()
        log.info("graph.index_item", ingestion_item_id=ingestion_item_id, **result)
        return result


@celery_app.task(name="legalbot.workers.graph.graph_index_run", bind=True)
def graph_index_run(self, run_id: str) -> dict:
    return run_async(_graph_index_run(run_id))


async def _graph_index_run(run_id: str) -> dict:
    settings = get_settings()
    if not settings.KG_ENABLED:
        return {"ok": False, "reason": "disabled"}

    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        return {"ok": False, "reason": "invalid_id"}

    sm = async_session_factory()
    async with sm() as db:
        svc = KnowledgeGraphService(db)
        result = await svc.index_run(run_uuid)
        await db.commit()
        log.info("graph.index_run", run_id=run_id, **result)
        return result
