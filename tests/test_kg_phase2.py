"""Tests for Phase 2 knowledge graph run projection from artifacts."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from legalbot.artifacts.service import ArtifactService
from legalbot.db.models import (
    KgEdge,
    KgEntity,
    ProcessingJob,
    Run,
    Session,
)
from legalbot.db.types import JobState, RunKind, RunStatus, SessionKind, SessionStatus
from legalbot.memory.extraction import (
    build_artifact_anchor_projection,
    build_projection_from_artifacts,
    qa_acme_nda_artifact_bundle,
)
from legalbot.memory.ontology import EntityType, Relation
from legalbot.memory.service import KnowledgeGraphService
from tests.test_kg_service import _add_email_item, OWNER_A


async def _seed_pipeline_with_artifacts(db, *, bundle: dict) -> tuple[Run, uuid.UUID]:
    item = await _add_email_item(
        db,
        owner=OWNER_A,
        external_id=f"phase2-{uuid.uuid4().hex[:8]}",
        title=bundle.get("analysis/extracted", {}).get("subject", "NDA request"),
        from_addr="partner@firm.test",
    )
    job = ProcessingJob(
        ingestion_item_id=item.id,
        state=JobState.completed,
        owner_user_id=OWNER_A,
    )
    db.add(job)
    await db.flush()

    session = Session(
        job_id=job.id,
        thread_id=f"thread-{uuid.uuid4().hex}",
        kind=SessionKind.primary,
        status=SessionStatus.idle,
        owner_user_id=OWNER_A,
    )
    db.add(session)
    await db.flush()

    run = Run(
        session_id=session.id,
        job_id=job.id,
        kind=RunKind.pipeline,
        status=RunStatus.completed,
        trigger="dispatch",
    )
    db.add(run)
    await db.flush()

    art_svc = ArtifactService(db)
    for key, content in bundle.items():
        await art_svc.write(
            session_id=session.id,
            key=key,
            content=content,
            kind="analysis",
            producer="test",
            run_id=run.id,
        )
    await db.flush()
    return run, item.id


@pytest.mark.asyncio
async def test_build_projection_from_qa_acme_bundle() -> None:
    bundle = qa_acme_nda_artifact_bundle()
    projection = build_projection_from_artifacts(bundle)
    types = {str(e.entity_type) for e in projection.entities}
    assert EntityType.ORGANIZATION in types
    assert EntityType.PERSON in types
    assert EntityType.CONTRACT in types
    assert any(e.canonical_name == "Acme Corp" for e in projection.entities)
    assert any(r.relation == Relation.PARTY_TO for r in projection.edges)


@pytest.mark.asyncio
async def test_artifact_anchor_links_report_to_source_files() -> None:
    bundle = qa_acme_nda_artifact_bundle()
    session_id = uuid.uuid4()
    projection = build_artifact_anchor_projection(bundle, session_id)

    doc_keys = {
        (str(e.entity_type), e.canonical_key)
        for e in projection.entities
        if e.entity_type == EntityType.DOCUMENT
    }
    assert (EntityType.DOCUMENT, f"artifact:{session_id}:analysis/report") in doc_keys
    assert (EntityType.DOCUMENT, f"artifact:{session_id}:extracted_data/prior_nda.docx") in doc_keys

    report_key = f"artifact:{session_id}:analysis/report"
    source_key = f"artifact:{session_id}:extracted_data/prior_nda.docx"
    assert any(
        e.src_key == report_key and e.dst_key == source_key and e.relation == Relation.REFERENCES
        for e in projection.edges
    )


@pytest.mark.asyncio
async def test_index_run_projects_acme_org_and_contract(db_session) -> None:
    bundle = qa_acme_nda_artifact_bundle()
    run, item_id = await _seed_pipeline_with_artifacts(db_session, bundle=bundle)

    svc = KnowledgeGraphService(db_session)
    result = await svc.index_run(run.id)
    assert result["ok"] is True
    assert result["entities_upserted"] >= 3

    org = (
        await db_session.execute(
            select(KgEntity).where(
                KgEntity.owner_user_id == OWNER_A,
                KgEntity.type == EntityType.ORGANIZATION,
                KgEntity.canonical_name == "Acme Corp",
            )
        )
    ).scalar_one_or_none()
    assert org is not None

    contract = (
        await db_session.execute(
            select(KgEntity).where(
                KgEntity.owner_user_id == OWNER_A,
                KgEntity.type == EntityType.CONTRACT,
            )
        )
    ).scalar_one_or_none()
    assert contract is not None

    party_edges = (
        await db_session.execute(
            select(func.count()).select_from(KgEdge).where(KgEdge.relation == Relation.PARTY_TO)
        )
    ).scalar_one()
    assert party_edges >= 2

    search_hits = await svc.search_related("Acme", owner_user_id=OWNER_A, k=8)
    source_ids = {h["source_id"] for h in search_hits}
    assert str(item_id) in source_ids


@pytest.mark.asyncio
async def test_index_run_skips_non_completed_run(db_session) -> None:
    bundle = qa_acme_nda_artifact_bundle()
    run, _ = await _seed_pipeline_with_artifacts(db_session, bundle=bundle)
    run.status = RunStatus.failed
    await db_session.flush()

    svc = KnowledgeGraphService(db_session)
    result = await svc.index_run(run.id)
    assert result["ok"] is False
    assert result["reason"] == "run_not_completed"


@pytest.mark.asyncio
async def test_index_run_resolves_person_by_trgm(db_session) -> None:
    """Phase 1 Person from email header should merge with Phase 2 name-only Jane Doe."""
    bundle = qa_acme_nda_artifact_bundle()
    item = await _add_email_item(
        db_session,
        owner=OWNER_A,
        external_id="phase2-jane",
        title="NDA",
        from_addr="Jane Doe <jane@acme.com>",
    )
    job = ProcessingJob(
        ingestion_item_id=item.id,
        state=JobState.completed,
        owner_user_id=OWNER_A,
    )
    db_session.add(job)
    await db_session.flush()

    session = Session(
        job_id=job.id,
        thread_id=f"thread-{uuid.uuid4().hex}",
        kind=SessionKind.primary,
        status=SessionStatus.idle,
        owner_user_id=OWNER_A,
    )
    db_session.add(session)
    await db_session.flush()

    run = Run(
        session_id=session.id,
        job_id=job.id,
        kind=RunKind.pipeline,
        status=RunStatus.completed,
        trigger="dispatch",
    )
    db_session.add(run)
    await db_session.flush()

    art_svc = ArtifactService(db_session)
    for key, content in bundle.items():
        await art_svc.write(
            session_id=session.id,
            key=key,
            content=content,
            kind="analysis",
            producer="test",
            run_id=run.id,
        )

    svc = KnowledgeGraphService(db_session)
    await svc.index_ingestion_item(item.id)
    result = await svc.index_run(run.id)
    assert result["ok"] is True

    people = (
        await db_session.execute(
            select(func.count()).select_from(KgEntity).where(
                KgEntity.owner_user_id == OWNER_A,
                KgEntity.type == EntityType.PERSON,
                KgEntity.canonical_key == "email:jane@acme.com",
            )
        )
    ).scalar_one()
    assert people == 1
