"""Tests for KnowledgeGraphService (SQLite ilike fallback; Postgres trgm in CI)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from legalbot.db.models import EmailMetadata, IngestionItem, KgEdge, KgEntity
from legalbot.memory.ontology import EntityType, Relation
from legalbot.memory.service import KnowledgeGraphService

OWNER_A = "user-a"
OWNER_B = "user-b"


async def _add_email_item(
    db,
    *,
    owner: str,
    external_id: str,
    title: str,
    from_addr: str | None = None,
    to_addrs: list[str] | None = None,
    provider_message_id: str | None = None,
    provider_thread_id: str | None = None,
    in_reply_to: str | None = None,
) -> IngestionItem:
    item_id = uuid.uuid4()
    item = IngestionItem(
        id=item_id,
        source="email",
        external_id=external_id,
        title=title,
        owner_user_id=owner,
    )
    db.add(item)
    db.add(
        EmailMetadata(
            ingestion_item_id=item_id,
            from_addr=from_addr,
            to_addrs=to_addrs,
            provider_message_id=provider_message_id or external_id,
            provider_thread_id=provider_thread_id,
            in_reply_to=in_reply_to,
        )
    )
    await db.flush()
    return item


@pytest.mark.asyncio
async def test_upsert_entity_is_idempotent(db_session) -> None:
    owner = "user-upsert-isolated"
    svc = KnowledgeGraphService(db_session)
    first = await svc.upsert_entity(
        owner_user_id=owner,
        entity_type=EntityType.PERSON,
        canonical_key="email:jane@acme.com",
        canonical_name="Jane Doe",
        attributes={"email": "jane@acme.com"},
    )
    second = await svc.upsert_entity(
        owner_user_id=owner,
        entity_type=EntityType.PERSON,
        canonical_key="email:jane@acme.com",
        canonical_name="Jane D.",
        attributes={"email": "jane@acme.com"},
    )
    assert first.id == second.id
    assert second.canonical_name == "Jane D."

    count = (
        await db_session.execute(
            select(func.count()).select_from(KgEntity).where(KgEntity.owner_user_id == owner)
        )
    ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_add_edge_dedupes_on_conflict(db_session) -> None:
    svc = KnowledgeGraphService(db_session)
    src = await svc.upsert_entity(
        owner_user_id=OWNER_A,
        entity_type=EntityType.EMAIL,
        canonical_key="ingestion_item:1",
        canonical_name="Email 1",
    )
    dst = await svc.upsert_entity(
        owner_user_id=OWNER_A,
        entity_type=EntityType.PERSON,
        canonical_key="email:jane@acme.com",
        canonical_name="Jane Doe",
    )
    source_id = uuid.uuid4()
    await svc.add_edge(
        owner_user_id=OWNER_A,
        src_id=src.id,
        dst_id=dst.id,
        relation=Relation.SENT_BY,
        source_type="ingestion_item",
        source_id=source_id,
    )
    await svc.add_edge(
        owner_user_id=OWNER_A,
        src_id=src.id,
        dst_id=dst.id,
        relation=Relation.SENT_BY,
        source_type="ingestion_item",
        source_id=source_id,
    )
    count = (
        await db_session.execute(select(func.count()).select_from(KgEdge))
    ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_index_ingestion_item_creates_person_and_edges(db_session) -> None:
    item = await _add_email_item(
        db_session,
        owner=OWNER_A,
        external_id="msg-1",
        title="NDA request",
        from_addr="Jane Doe <jane@acme.com>",
        to_addrs=["legal@firm.com"],
    )
    svc = KnowledgeGraphService(db_session)
    result = await svc.index_ingestion_item(item.id)
    assert result["ok"] is True

    people = (
        await db_session.execute(
            select(KgEntity).where(
                KgEntity.owner_user_id == OWNER_A,
                KgEntity.type == EntityType.PERSON,
            )
        )
    ).scalars().all()
    assert len(people) == 2
    assert any(p.canonical_name == "Jane Doe" for p in people)


@pytest.mark.asyncio
async def test_search_related_isolates_tenants(db_session) -> None:
    item_a = await _add_email_item(
        db_session,
        owner=OWNER_A,
        external_id="a-1",
        title="Jane contract",
        from_addr="jane@acme.com",
    )
    item_b = await _add_email_item(
        db_session,
        owner=OWNER_B,
        external_id="b-1",
        title="Jane other tenant",
        from_addr="jane@other.com",
    )
    svc = KnowledgeGraphService(db_session)
    await svc.index_ingestion_item(item_a.id)
    await svc.index_ingestion_item(item_b.id)

    results_a = await svc.search_related("jane", owner_user_id=OWNER_A, k=5)
    source_ids_a = {r["source_id"] for r in results_a}
    assert str(item_a.id) in source_ids_a
    assert str(item_b.id) not in source_ids_a


@pytest.mark.asyncio
async def test_replies_to_via_provider_message_id(db_session) -> None:
    owner = "user-replies-isolated"
    parent = await _add_email_item(
        db_session,
        owner=owner,
        external_id="parent-msg",
        title="Original thread",
        from_addr="jane@acme.com",
        provider_message_id="parent-msg-id",
        provider_thread_id="thread-1",
    )
    child = await _add_email_item(
        db_session,
        owner=owner,
        external_id="child-msg",
        title="Re: Original thread",
        from_addr="jane@acme.com",
        provider_message_id="child-msg-id",
        provider_thread_id="thread-1",
        in_reply_to="parent-msg-id",
    )
    svc = KnowledgeGraphService(db_session)
    await svc.index_ingestion_item(parent.id)
    await svc.index_ingestion_item(child.id)

    child_email = (
        await db_session.execute(
            select(KgEntity).where(
                KgEntity.canonical_key == f"ingestion_item:{child.id}",
            )
        )
    ).scalar_one()
    parent_email = (
        await db_session.execute(
            select(KgEntity).where(
                KgEntity.canonical_key == f"ingestion_item:{parent.id}",
            )
        )
    ).scalar_one()
    edge = (
        await db_session.execute(
            select(KgEdge).where(
                KgEdge.owner_user_id == owner,
                KgEdge.relation == Relation.REPLIES_TO,
                KgEdge.src_id == child_email.id,
                KgEdge.dst_id == parent_email.id,
            )
        )
    ).scalar_one()
    assert edge.src_id == child_email.id
    assert edge.dst_id == parent_email.id


@pytest.mark.asyncio
async def test_search_related_returns_ingestion_handles(db_session) -> None:
    item = await _add_email_item(
        db_session,
        owner=OWNER_A,
        external_id="search-1",
        title="Contract for Acme",
        from_addr="Jane Doe <jane@acme.com>",
    )
    svc = KnowledgeGraphService(db_session)
    await svc.index_ingestion_item(item.id)

    results = await svc.search_related("jane", owner_user_id=OWNER_A, k=5)
    assert results
    assert any(r["source_type"] == "ingestion_item" and r["source_id"] == str(item.id) for r in results)
