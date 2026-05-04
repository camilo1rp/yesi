"""Artifact-related value objects carried in agent state (cheap refs, not payloads)."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field


class ArtifactRef(BaseModel):
    """Compact pointer to an `artifact` row. Carried in `AgentState.artifact_index`."""

    id: uuid.UUID
    key: str
    version: int
    kind: str
    mime: str = "application/json"
    size_bytes: int = 0
    storage: str = "inline"
    summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    class Config:
        frozen = True


class ArtifactEdit(BaseModel):
    """Inline patch supplied on interrupt resume or out-of-band via POST artifact endpoint."""

    key: str
    content: Any | None = None
    patch: dict[str, Any] | None = None
    merge: str = "replace"  # "replace" | "json_merge_patch"
    mime: str | None = None
    metadata: dict[str, Any] | None = None


def artifact_index_reducer(
    current: dict[str, ArtifactRef] | None,
    update: dict[str, ArtifactRef] | list[ArtifactRef] | None,
) -> dict[str, ArtifactRef]:
    """Reducer for `AgentState.artifact_index`: additive upsert keyed by `key`."""
    result: dict[str, ArtifactRef] = dict(current or {})
    if update is None:
        return result
    items: list[ArtifactRef]
    if isinstance(update, dict):
        items = list(update.values())
    else:
        items = list(update)
    for ref in items:
        prior = result.get(ref.key)
        if prior is None or ref.version >= prior.version:
            result[ref.key] = ref
    return result
