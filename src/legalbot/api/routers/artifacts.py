"""Artifact CRUD under a session."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from legalbot.api.schemas import (
    ArtifactContent,
    ArtifactRead,
    ArtifactUpdateRequest,
)
from legalbot.artifacts.service import ArtifactNotFound, ArtifactService
from legalbot.db.session import get_session

router = APIRouter()


def _row_to_read(r: Any) -> ArtifactRead:
    return ArtifactRead(
        id=r.id,
        session_id=r.session_id,
        key=r.key,
        version=r.version,
        kind=r.kind,
        mime=r.mime,
        size_bytes=r.size_bytes,
        storage=r.storage,
        is_latest=r.is_latest,
        created_at=r.created_at,
        metadata=r.meta or {},
    )


@router.get("/{session_id}/artifacts", response_model=list[ArtifactRead])
async def list_artifacts(
    session_id: uuid.UUID,
    kind: str | None = None,
    include_versions: bool = False,
    db: AsyncSession = Depends(get_session),
) -> list[ArtifactRead]:
    rows = await ArtifactService(db).list(
        session_id=session_id, kind=kind, include_versions=include_versions
    )
    return [_row_to_read(r) for r in rows]


@router.get("/{session_id}/artifacts/{key:path}", response_model=ArtifactContent)
async def get_artifact(
    session_id: uuid.UUID,
    key: str,
    version: int | None = Query(default=None),
    db: AsyncSession = Depends(get_session),
) -> ArtifactContent:
    try:
        row, content = await ArtifactService(db).read_resolved(
            session_id=session_id, key_or_id=key, version=version
        )
    except ArtifactNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    base = _row_to_read(row)
    return ArtifactContent(**base.model_dump(), content=content)


@router.post("/{session_id}/artifacts/{key:path}", response_model=ArtifactRead)
async def update_artifact(
    session_id: uuid.UUID,
    key: str,
    body: ArtifactUpdateRequest,
    db: AsyncSession = Depends(get_session),
) -> ArtifactRead:
    svc = ArtifactService(db)
    ref = await svc.update(
        session_id=session_id,
        key=key,
        patch_or_content=body.patch_or_content,
        merge=body.merge,
        mime=body.mime,
        metadata=body.metadata,
        producer="user",
    )
    await db.commit()
    row, _ = await svc.read(session_id=session_id, key_or_id=ref.id)
    return _row_to_read(row)


@router.get("/{session_id}/artifacts/{key:path}/diff")
async def diff_artifact(
    session_id: uuid.UUID,
    key: str,
    v1: int,
    v2: int,
    db: AsyncSession = Depends(get_session),
) -> dict:
    return await ArtifactService(db).diff(session_id=session_id, key=key, v1=v1, v2=v2)
