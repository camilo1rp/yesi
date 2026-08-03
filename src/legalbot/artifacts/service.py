"""ArtifactService: write/read/update/list with inline-vs-blob policy and version cap.

Semantics (see §7.7 of the plan):
- Writes are append-only; each write produces a new version, previous `is_latest` flips false.
- Inline JSON artifacts (≤ 32 KB serialized) live in `content_inline`.
- Larger or non-JSON artifacts go to BlobStore and the row stores `blob_ref`.
- `update_artifact` with `merge='json_merge_patch'` applies RFC 7396 on the latest inline
  JSON payload and writes the merged result as a new version.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

import jsonpatch
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from legalbot.artifacts.blobstore import BlobStore, get_blob_store
from legalbot.artifacts.models import ArtifactRef
from legalbot.core import metrics
from legalbot.core.config import get_settings
from legalbot.core.logging import get_logger
from legalbot.db.models import Artifact

log = get_logger(__name__)


class ArtifactVersionCapExceeded(RuntimeError):
    """Raised when a per-(session,key) write exceeds the configured version cap."""


class ArtifactNotFound(LookupError):
    pass


def _json_merge_patch(original: Any, patch: Any) -> Any:
    """RFC 7396 JSON merge patch."""
    if not isinstance(patch, dict):
        return patch
    if not isinstance(original, dict):
        original = {}
    out = dict(original)
    for k, v in patch.items():
        if v is None:
            out.pop(k, None)
        elif isinstance(v, dict):
            out[k] = _json_merge_patch(out.get(k), v)
        else:
            out[k] = v
    return out


class ArtifactService:
    """Bounded transactional write + inline/blob policy enforcement."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        blob_store: BlobStore | None = None,
        inline_max_bytes: int | None = None,
        version_cap: int | None = None,
    ) -> None:
        self.db = db
        self.blob = blob_store or get_blob_store()
        settings = get_settings()
        self.inline_max_bytes = inline_max_bytes or settings.ARTIFACT_INLINE_MAX_BYTES
        self.version_cap = version_cap or settings.ARTIFACT_VERSION_CAP

    @staticmethod
    def _checksum(payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()

    async def _next_version(self, session_id: uuid.UUID, key: str) -> int:
        result = await self.db.execute(
            select(func.coalesce(func.max(Artifact.version), 0)).where(
                Artifact.session_id == session_id, Artifact.key == key
            )
        )
        return int(result.scalar_one()) + 1

    async def _demote_current_latest(self, session_id: uuid.UUID, key: str) -> uuid.UUID | None:
        result = await self.db.execute(
            select(Artifact.id).where(
                Artifact.session_id == session_id,
                Artifact.key == key,
                Artifact.is_latest.is_(True),
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        await self.db.execute(update(Artifact).where(Artifact.id == row).values(is_latest=False))
        return row

    async def write(
        self,
        *,
        session_id: uuid.UUID,
        key: str,
        content: Any,
        kind: str,
        producer: str,
        run_id: uuid.UUID | None = None,
        mime: str = "application/json",
        metadata: dict[str, Any] | None = None,
        created_by: str | None = None,
    ) -> ArtifactRef:
        """Append a new version under `(session_id, key)` and return the compact ref."""

        version = await self._next_version(session_id, key)
        if version > self.version_cap:
            raise ArtifactVersionCapExceeded(
                f"artifact {key!r} exceeded version cap {self.version_cap}"
            )

        payload_bytes: bytes
        storage: str
        content_inline: Any | None = None
        blob_ref: str | None = None

        if mime == "application/json":
            payload_bytes = json.dumps(content, ensure_ascii=False).encode("utf-8")
        elif isinstance(content, (bytes, bytearray)):
            payload_bytes = bytes(content)
        else:
            payload_bytes = str(content).encode("utf-8")

        size_bytes = len(payload_bytes)

        if (
            mime == "application/json"
            and size_bytes <= self.inline_max_bytes
            and not isinstance(content, (bytes, bytearray))
        ):
            storage = "inline"
            content_inline = content
        else:
            storage = "blob"
            artifact_id = uuid.uuid4()
            blob_ref = f"sessions/{session_id}/artifacts/{artifact_id}"
            await self.blob.put(blob_ref, payload_bytes, mime=mime)

        prior_latest_id = await self._demote_current_latest(session_id, key)

        artifact_id = uuid.uuid4() if storage == "inline" else uuid.UUID(blob_ref.rsplit("/", 1)[-1])
        row_kwargs: dict[str, Any] = {
            "id": artifact_id,
            "session_id": session_id,
            "run_id": run_id,
            "producer": producer,
            "kind": kind,
            "key": key,
            "version": version,
            "mime": mime,
            "storage": storage,
            "checksum": self._checksum(payload_bytes),
            "size_bytes": size_bytes,
            "meta": metadata or {},
            "supersedes_artifact_id": prior_latest_id,
            "is_latest": True,
            "created_by": created_by,
        }
        if storage == "inline":
            row_kwargs["content_inline"] = content_inline
            row_kwargs["blob_ref"] = None
        else:
            row_kwargs["blob_ref"] = blob_ref

        row = Artifact(**row_kwargs)
        self.db.add(row)
        await self.db.flush()

        metrics.artifact_writes_total.labels(kind=kind).inc()
        metrics.artifact_bytes.labels(storage=storage).inc(size_bytes)
        metrics.artifact_version_depth.observe(version)

        log.info(
            "artifact.written",
            session_id=str(session_id),
            run_id=str(run_id) if run_id else None,
            key=key,
            kind=kind,
            version=version,
            storage=storage,
            size_bytes=size_bytes,
        )

        return ArtifactRef(
            id=row.id,
            key=key,
            version=version,
            kind=kind,
            mime=mime,
            size_bytes=size_bytes,
            storage=storage,
            metadata=row.meta or {},
            summary=(metadata or {}).get("summary"),
        )

    async def read(
        self,
        *,
        session_id: uuid.UUID | None = None,
        key_or_id: str | uuid.UUID,
        version: int | None = None,
    ) -> tuple[Artifact, Any]:
        """Returns the row + resolved payload. `key_or_id` may be a UUID (cross-session) or key."""
        stmt = select(Artifact)
        if isinstance(key_or_id, uuid.UUID):
            stmt = stmt.where(Artifact.id == key_or_id)
        else:
            if session_id is None:
                raise ValueError("session_id required when reading by key")
            stmt = stmt.where(Artifact.session_id == session_id, Artifact.key == key_or_id)
            if version is None:
                stmt = stmt.where(Artifact.is_latest.is_(True))
            else:
                stmt = stmt.where(Artifact.version == version)
        result = await self.db.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            raise ArtifactNotFound(f"artifact {key_or_id!r} not found")
        if row.storage == "inline":
            return row, row.content_inline
        assert row.blob_ref is not None
        data = await self.blob.get(row.blob_ref)
        if row.mime == "application/json":
            return row, json.loads(data)
        return row, data

    async def update(
        self,
        *,
        session_id: uuid.UUID,
        key: str,
        patch_or_content: Any,
        merge: str = "replace",
        run_id: uuid.UUID | None = None,
        producer: str = "system",
        kind: str | None = None,
        mime: str = "application/json",
        metadata: dict[str, Any] | None = None,
        created_by: str | None = None,
    ) -> ArtifactRef:
        """Write a new version. `merge='json_merge_patch'` applies RFC 7396 to latest inline JSON."""
        if merge == "json_merge_patch":
            try:
                row, previous = await self.read(session_id=session_id, key_or_id=key)
            except ArtifactNotFound:
                previous = {}
                row = None
            if mime != "application/json":
                raise ValueError("json_merge_patch only valid for application/json artifacts")
            next_content = _json_merge_patch(previous, patch_or_content)
            resolved_kind = kind or (row.kind if row else "custom")
        elif merge == "json_patch":
            row, previous = await self.read(session_id=session_id, key_or_id=key)
            next_content = jsonpatch.JsonPatch(patch_or_content).apply(previous)
            resolved_kind = kind or row.kind
        else:
            next_content = patch_or_content
            try:
                existing = await self.read(session_id=session_id, key_or_id=key)
                resolved_kind = kind or existing[0].kind
            except ArtifactNotFound:
                resolved_kind = kind or "custom"

        return await self.write(
            session_id=session_id,
            key=key,
            content=next_content,
            kind=resolved_kind,
            producer=producer,
            run_id=run_id,
            mime=mime,
            metadata=metadata,
            created_by=created_by,
        )

    async def list(
        self, *, session_id: uuid.UUID, kind: str | None = None, include_versions: bool = False
    ) -> list[Artifact]:
        stmt = select(Artifact).where(Artifact.session_id == session_id)
        if kind:
            stmt = stmt.where(Artifact.kind == kind)
        if not include_versions:
            stmt = stmt.where(Artifact.is_latest.is_(True))
        stmt = stmt.order_by(Artifact.key, Artifact.version.desc())
        result = await self.db.execute(stmt)
        return list(result.scalars())

    async def diff(self, *, session_id: uuid.UUID, key: str, v1: int, v2: int) -> dict[str, Any]:
        """Shallow diff of two inline JSON versions; returns jsonpatch ops."""
        from jsonpatch import make_patch

        _, a = await self.read(session_id=session_id, key_or_id=key, version=v1)
        _, b = await self.read(session_id=session_id, key_or_id=key, version=v2)
        return {"patch": list(make_patch(a, b))}

    async def reap_orphans(self, older_than: datetime) -> int:
        """Placeholder: a janitor would list blobstore entries and reap ones with no matching row."""
        return 0
