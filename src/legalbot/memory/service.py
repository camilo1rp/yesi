"""KnowledgeGraphService — tenant-scoped entity graph CRUD and retrieval."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from legalbot.core.config import get_settings
from legalbot.core.logging import get_logger
from legalbot.db.models import (
    EmailMetadata,
    IngestionAttachment,
    IngestionItem,
    KgAlias,
    KgEdge,
    KgEntity,
    KgMention,
)
from legalbot.memory.ontology import (
    EntityType,
    ingestion_attachment_canonical_key,
    ingestion_item_canonical_key,
    parse_email_address,
    person_canonical_key,
    person_canonical_name,
    Relation,
)

log = get_logger(__name__)


class KnowledgeGraphService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def upsert_entity(
        self,
        *,
        owner_user_id: str,
        entity_type: EntityType | str,
        canonical_key: str,
        canonical_name: str,
        attributes: dict[str, Any] | None = None,
        alias: str | None = None,
    ) -> KgEntity:
        type_str = str(entity_type)
        attrs = attributes or {}
        now = datetime.now(UTC)

        if self._is_postgres():
            stmt = (
                pg_insert(KgEntity)
                .values(
                    id=uuid.uuid4(),
                    owner_user_id=owner_user_id,
                    type=type_str,
                    canonical_key=canonical_key,
                    canonical_name=canonical_name,
                    attributes=attrs,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_update(
                    index_elements=["owner_user_id", "type", "canonical_key"],
                    set_={
                        "canonical_name": canonical_name,
                        "attributes": attrs,
                        "updated_at": now,
                    },
                )
                .returning(KgEntity.id)
            )
            result = await self.db.execute(stmt)
            entity_id = result.scalar_one()
            row = (
                await self.db.execute(select(KgEntity).where(KgEntity.id == entity_id))
            ).scalar_one()
        else:
            existing = (
                await self.db.execute(
                    select(KgEntity).where(
                        KgEntity.owner_user_id == owner_user_id,
                        KgEntity.type == type_str,
                        KgEntity.canonical_key == canonical_key,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                row = KgEntity(
                    owner_user_id=owner_user_id,
                    type=type_str,
                    canonical_key=canonical_key,
                    canonical_name=canonical_name,
                    attributes=attrs,
                    created_at=now,
                    updated_at=now,
                )
                self.db.add(row)
                await self.db.flush()
            else:
                existing.canonical_name = canonical_name
                existing.attributes = attrs
                existing.updated_at = now
                row = existing

        if alias and alias.strip():
            await self._upsert_alias(row.id, alias.strip())

        return row

    def _is_postgres(self) -> bool:
        bind = self.db.get_bind()
        return bind is not None and bind.dialect.name == "postgresql"

    async def _upsert_alias(self, entity_id: uuid.UUID, alias: str) -> None:
        if self._is_postgres():
            stmt = (
                pg_insert(KgAlias)
                .values(id=uuid.uuid4(), entity_id=entity_id, alias=alias)
                .on_conflict_do_nothing(index_elements=["entity_id", "alias"])
            )
            await self.db.execute(stmt)
            return

        existing = (
            await self.db.execute(
                select(KgAlias).where(KgAlias.entity_id == entity_id, KgAlias.alias == alias)
            )
        ).scalar_one_or_none()
        if existing is None:
            self.db.add(KgAlias(entity_id=entity_id, alias=alias))

    async def add_edge(
        self,
        *,
        owner_user_id: str,
        src_id: uuid.UUID,
        dst_id: uuid.UUID,
        relation: Relation | str,
        source_type: str,
        source_id: uuid.UUID,
        confidence: float = 1.0,
        origin: str = "deterministic",
        attributes: dict[str, Any] | None = None,
    ) -> None:
        rel = str(relation)
        attrs = attributes or {}

        if self._is_postgres():
            stmt = (
                pg_insert(KgEdge)
                .values(
                    id=uuid.uuid4(),
                    owner_user_id=owner_user_id,
                    src_id=src_id,
                    dst_id=dst_id,
                    relation=rel,
                    attributes=attrs,
                    confidence=confidence,
                    origin=origin,
                    source_type=source_type,
                    source_id=source_id,
                )
                .on_conflict_do_nothing(constraint="uq_kg_edge_dedup")
            )
            await self.db.execute(stmt)
            return

        existing = (
            await self.db.execute(
                select(KgEdge).where(
                    KgEdge.owner_user_id == owner_user_id,
                    KgEdge.src_id == src_id,
                    KgEdge.dst_id == dst_id,
                    KgEdge.relation == rel,
                    KgEdge.source_type == source_type,
                    KgEdge.source_id == source_id,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            self.db.add(
                KgEdge(
                    owner_user_id=owner_user_id,
                    src_id=src_id,
                    dst_id=dst_id,
                    relation=rel,
                    attributes=attrs,
                    confidence=confidence,
                    origin=origin,
                    source_type=source_type,
                    source_id=source_id,
                )
            )

    async def add_mention(
        self,
        *,
        owner_user_id: str,
        entity_id: uuid.UUID,
        source_type: str,
        source_id: uuid.UUID,
        snippet: str | None = None,
        confidence: float = 1.0,
    ) -> None:
        self.db.add(
            KgMention(
                entity_id=entity_id,
                owner_user_id=owner_user_id,
                source_type=source_type,
                source_id=source_id,
                snippet=snippet,
                confidence=confidence,
            )
        )

    async def index_ingestion_item(self, item_id: uuid.UUID) -> dict[str, Any]:
        """Deterministic graph projection for one ingestion item (no LLM)."""
        item = (
            await self.db.execute(
                select(IngestionItem).where(IngestionItem.id == item_id)
            )
        ).scalar_one_or_none()
        if item is None:
            return {"ok": False, "reason": "not_found"}

        meta = (
            await self.db.execute(
                select(EmailMetadata).where(EmailMetadata.ingestion_item_id == item_id)
            )
        ).scalar_one_or_none()

        attachments = (
            (
                await self.db.execute(
                    select(IngestionAttachment).where(
                        IngestionAttachment.ingestion_item_id == item_id
                    )
                )
            )
            .scalars()
            .all()
        )

        owner = item.owner_user_id
        source_type = "ingestion_item"

        email_attrs: dict[str, Any] = {
            "subject": item.title,
            "body_preview": (item.body_text or "")[:500] if item.body_text else None,
        }
        if meta:
            email_attrs["from_addr"] = meta.from_addr
            email_attrs["provider_message_id"] = meta.provider_message_id
            email_attrs["provider_thread_id"] = meta.provider_thread_id

        email_entity = await self.upsert_entity(
            owner_user_id=owner,
            entity_type=EntityType.EMAIL,
            canonical_key=ingestion_item_canonical_key(item.id),
            canonical_name=item.title or f"Email {item.id}",
            attributes=email_attrs,
        )

        await self.add_mention(
            owner_user_id=owner,
            entity_id=email_entity.id,
            source_type=source_type,
            source_id=item.id,
            snippet=item.title,
        )

        if meta and meta.from_addr:
            person = await self._upsert_person_from_addr(
                owner_user_id=owner,
                raw_addr=meta.from_addr,
                source_id=item.id,
            )
            if person is not None:
                await self.add_edge(
                    owner_user_id=owner,
                    src_id=email_entity.id,
                    dst_id=person.id,
                    relation=Relation.SENT_BY,
                    source_type=source_type,
                    source_id=item.id,
                )

        if meta:
            for raw in meta.to_addrs or []:
                person = await self._upsert_person_from_addr(
                    owner_user_id=owner,
                    raw_addr=raw,
                    source_id=item.id,
                )
                if person is not None:
                    await self.add_edge(
                        owner_user_id=owner,
                        src_id=email_entity.id,
                        dst_id=person.id,
                        relation=Relation.SENT_TO,
                        source_type=source_type,
                        source_id=item.id,
                    )

            for raw in meta.cc_addrs or []:
                person = await self._upsert_person_from_addr(
                    owner_user_id=owner,
                    raw_addr=raw,
                    source_id=item.id,
                )
                if person is not None:
                    await self.add_edge(
                        owner_user_id=owner,
                        src_id=email_entity.id,
                        dst_id=person.id,
                        relation=Relation.SENT_TO,
                        source_type=source_type,
                        source_id=item.id,
                        attributes={"addr_kind": "cc"},
                    )

        for att in attachments:
            att_entity = await self.upsert_entity(
                owner_user_id=owner,
                entity_type=EntityType.ATTACHMENT,
                canonical_key=ingestion_attachment_canonical_key(att.id),
                canonical_name=att.name,
                attributes={
                    "mime_type": att.mime_type,
                    "size": att.size,
                    "ingestion_item_id": str(item.id),
                },
            )
            await self.add_mention(
                owner_user_id=owner,
                entity_id=att_entity.id,
                source_type="ingestion_attachment",
                source_id=att.id,
                snippet=att.name,
            )
            await self.add_edge(
                owner_user_id=owner,
                src_id=att_entity.id,
                dst_id=email_entity.id,
                relation=Relation.ATTACHED_TO,
                source_type=source_type,
                source_id=item.id,
            )

        parent_email = await self._resolve_parent_email(item, meta)
        if parent_email is not None and parent_email.id != email_entity.id:
            await self.add_edge(
                owner_user_id=owner,
                src_id=email_entity.id,
                dst_id=parent_email.id,
                relation=Relation.REPLIES_TO,
                source_type=source_type,
                source_id=item.id,
            )

        return {
            "ok": True,
            "item_id": str(item.id),
            "email_entity_id": str(email_entity.id),
        }

    async def _upsert_person_from_addr(
        self,
        *,
        owner_user_id: str,
        raw_addr: str,
        source_id: uuid.UUID,
    ) -> KgEntity | None:
        display, email = parse_email_address(raw_addr)
        if not email:
            return None

        name = person_canonical_name(display, email)
        person = await self.upsert_entity(
            owner_user_id=owner_user_id,
            entity_type=EntityType.PERSON,
            canonical_key=person_canonical_key(email),
            canonical_name=name,
            attributes={"email": email, "display_name": display},
            alias=name,
        )
        await self.add_mention(
            owner_user_id=owner_user_id,
            entity_id=person.id,
            source_type="ingestion_item",
            source_id=source_id,
            snippet=raw_addr.strip(),
        )
        return person

    async def _resolve_parent_email(
        self,
        item: IngestionItem,
        meta: EmailMetadata | None,
    ) -> KgEntity | None:
        if meta is None:
            return None

        owner = item.owner_user_id
        candidates: list[str] = []
        if meta.in_reply_to:
            candidates.append(meta.in_reply_to.strip())
        for ref in meta.references or []:
            ref_s = (ref or "").strip()
            if ref_s and ref_s not in candidates:
                candidates.append(ref_s)

        for candidate in candidates:
            parent_item = await self._find_item_by_message_ref(owner, candidate)
            if parent_item is not None:
                return await self.upsert_entity(
                    owner_user_id=owner,
                    entity_type=EntityType.EMAIL,
                    canonical_key=ingestion_item_canonical_key(parent_item.id),
                    canonical_name=parent_item.title or f"Email {parent_item.id}",
                    attributes={"subject": parent_item.title},
                )

        if meta.provider_thread_id:
            thread_items = (
                (
                    await self.db.execute(
                        select(IngestionItem)
                        .join(EmailMetadata, EmailMetadata.ingestion_item_id == IngestionItem.id)
                        .where(
                            IngestionItem.owner_user_id == owner,
                            EmailMetadata.provider_thread_id == meta.provider_thread_id,
                            IngestionItem.id != item.id,
                        )
                        .order_by(IngestionItem.ingested_at.asc())
                        .limit(1)
                    )
                )
                .scalars()
                .all()
            )
            if thread_items:
                parent_item = thread_items[0]
                return await self.upsert_entity(
                    owner_user_id=owner,
                    entity_type=EntityType.EMAIL,
                    canonical_key=ingestion_item_canonical_key(parent_item.id),
                    canonical_name=parent_item.title or f"Email {parent_item.id}",
                    attributes={"subject": parent_item.title},
                )

        return None

    async def _find_item_by_message_ref(
        self,
        owner_user_id: str,
        ref: str,
    ) -> IngestionItem | None:
        """Match provider message id or external_id against a thread reference token."""
        if not ref:
            return None

        by_provider = (
            await self.db.execute(
                select(IngestionItem)
                .join(EmailMetadata, EmailMetadata.ingestion_item_id == IngestionItem.id)
                .where(
                    IngestionItem.owner_user_id == owner_user_id,
                    EmailMetadata.provider_message_id == ref,
                )
            )
        ).scalar_one_or_none()
        if by_provider is not None:
            return by_provider

        return (
            await self.db.execute(
                select(IngestionItem).where(
                    IngestionItem.owner_user_id == owner_user_id,
                    IngestionItem.external_id == ref,
                )
            )
        ).scalar_one_or_none()

    async def search_related(
        self,
        query: str,
        *,
        owner_user_id: str,
        k: int | None = None,
    ) -> list[dict[str, Any]]:
        """Hybrid entity search → provenance handles (Phase 1: trgm / ilike fallback)."""
        settings = get_settings()
        limit = k or settings.KG_SEARCH_TOP_K
        q = (query or "").strip()
        if not q:
            return []

        entity_rows = await self._search_entities(owner_user_id=owner_user_id, query=q, limit=limit)
        if not entity_rows:
            return []

        entity_ids = [row["id"] for row in entity_rows]
        score_by_id = {row["id"]: row["score"] for row in entity_rows}

        mentions = (
            (
                await self.db.execute(
                    select(KgMention, KgEntity)
                    .join(KgEntity, KgEntity.id == KgMention.entity_id)
                    .where(
                        KgMention.owner_user_id == owner_user_id,
                        KgMention.entity_id.in_(entity_ids),
                    )
                )
            )
            .all()
        )

        seen: set[tuple[str, uuid.UUID]] = set()
        results: list[dict[str, Any]] = []

        for mention, entity in mentions:
            key = (mention.source_type, mention.source_id)
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "source_type": mention.source_type,
                    "source_id": str(mention.source_id),
                    "snippet": mention.snippet,
                    "entity": {
                        "id": str(entity.id),
                        "type": entity.type,
                        "canonical_name": entity.canonical_name,
                    },
                    "score": round(score_by_id.get(entity.id, 0.0), 4),
                }
            )

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:limit]

    async def _search_entities(
        self,
        *,
        owner_user_id: str,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        bind = self.db.get_bind()
        dialect = bind.dialect.name if bind is not None else "postgresql"

        if dialect == "postgresql":
            sql = text(
                """
                SELECT e.id, e.type, e.canonical_name,
                       GREATEST(
                           similarity(e.canonical_name, :q),
                           COALESCE(MAX(similarity(a.alias, :q)), 0)
                       ) AS score
                FROM kg_entity e
                LEFT JOIN kg_alias a ON a.entity_id = e.id
                WHERE e.owner_user_id = :owner
                  AND (
                    e.canonical_name % :q
                    OR e.canonical_name ILIKE '%' || :q || '%'
                    OR a.alias % :q
                    OR a.alias ILIKE '%' || :q || '%'
                  )
                GROUP BY e.id, e.type, e.canonical_name
                ORDER BY score DESC
                LIMIT :lim
                """
            )
            rows = (
                await self.db.execute(
                    sql,
                    {"q": query, "owner": owner_user_id, "lim": limit},
                )
            ).all()
            return [
                {
                    "id": row.id,
                    "type": row.type,
                    "canonical_name": row.canonical_name,
                    "score": float(row.score or 0),
                }
                for row in rows
            ]

        pattern = f"%{query}%"
        stmt = (
            select(KgEntity)
            .outerjoin(KgAlias, KgAlias.entity_id == KgEntity.id)
            .where(
                KgEntity.owner_user_id == owner_user_id,
                or_(
                    KgEntity.canonical_name.ilike(pattern),
                    KgAlias.alias.ilike(pattern),
                ),
            )
            .group_by(KgEntity.id)
            .limit(limit)
        )
        entities = (await self.db.execute(stmt)).scalars().all()
        return [
            {
                "id": e.id,
                "type": e.type,
                "canonical_name": e.canonical_name,
                "score": 1.0,
            }
            for e in entities
        ]
