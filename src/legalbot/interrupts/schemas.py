"""Pydantic discriminated-union for interrupt resolutions + artifact edit piggy-back."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class ArtifactEditSchema(BaseModel):
    key: str
    content: Any | None = None
    patch: dict[str, Any] | None = None
    merge: Literal["replace", "json_merge_patch"] = "replace"
    mime: str | None = None
    metadata: dict[str, Any] | None = None


class InterruptResolutionTool(BaseModel):
    kind: Literal["tool_approval"] = "tool_approval"
    decision: Literal["approve", "edit", "reject"]
    edited_args: dict[str, Any] | None = None
    reason: str | None = None
    artifact_edits: list[ArtifactEditSchema] = Field(default_factory=list)


class InterruptResolutionInfo(BaseModel):
    kind: Literal["information_request"] = "information_request"
    answer: dict[str, Any] | None = None
    skipped: bool = False
    reason: str | None = None
    artifact_edits: list[ArtifactEditSchema] = Field(default_factory=list)


class InterruptResolutionReview(BaseModel):
    kind: Literal["review_draft"] = "review_draft"
    decision: Literal["accept", "revise", "reject"]
    revision_notes: str | None = None
    artifact_edits: list[ArtifactEditSchema] = Field(default_factory=list)


InterruptResolution = Annotated[
    InterruptResolutionTool | InterruptResolutionInfo | InterruptResolutionReview,
    Field(discriminator="kind"),
]


class InterruptEnvelope(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    run_id: uuid.UUID | None = None
    kind: Literal["tool_approval", "information_request", "review_draft"]
    payload: dict[str, Any]
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")
    status: str
    created_at: datetime
    expires_at: datetime | None = None
    artifacts: dict[str, Any] = Field(default_factory=dict)
