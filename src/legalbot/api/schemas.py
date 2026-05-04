"""Pydantic response/request schemas for the HTTP API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class MailboxCreate(BaseModel):
    provider: str
    external_id: str
    display_name: str | None = None
    owner_user_id: str
    access_token: str | None = None
    refresh_token: str | None = None


class MailboxRead(BaseModel):
    id: uuid.UUID
    provider: str
    external_id: str
    display_name: str | None
    state: str
    owner_user_id: str
    created_at: datetime


class FakeInjectAttachment(BaseModel):
    part_id: str
    name: str
    mime_type: str | None = None
    size: int | None = None
    data_b64: str | None = None


class FakeInjectMessage(BaseModel):
    provider_message_id: str
    provider_thread_id: str | None = None
    subject: str | None = None
    body_text: str | None = None
    body_html: str | None = None
    from_addr: str | None = None
    to_addrs: list[str] = Field(default_factory=list)
    cc_addrs: list[str] = Field(default_factory=list)
    bcc_addrs: list[str] = Field(default_factory=list)
    in_reply_to: str | None = None
    references: list[str] = Field(default_factory=list)
    headers: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)
    attachments: list[FakeInjectAttachment] = Field(default_factory=list)


class FakeInjectCreate(BaseModel):
    mailbox_external_id: str
    message: FakeInjectMessage


class IngestionItemRead(BaseModel):
    id: uuid.UUID
    source: str
    external_id: str
    title: str | None
    sender_identity: str | None
    received_at: datetime | None
    owner_user_id: str
    ingested_at: datetime


class EmailView(IngestionItemRead):
    from_addr: str | None
    to_addrs: list[str] = Field(default_factory=list)
    subject: str | None
    body_text_preview: str | None


class JobRead(BaseModel):
    id: uuid.UUID
    ingestion_item_id: uuid.UUID
    state: str
    priority: int
    attempts: int
    last_error: str | None
    owner_user_id: str
    current_session_id: uuid.UUID | None
    created_at: datetime


class SessionRead(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    thread_id: str
    kind: str
    status: str
    interrupt_kind: str | None
    message_count: int
    owner_user_id: str
    created_at: datetime
    parent_session_id: uuid.UUID | None
    branch_from_run_id: uuid.UUID | None
    branch_from_checkpoint_id: str | None
    last_activity_at: datetime | None


class SessionMessageRead(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    run_id: uuid.UUID | None
    role: str
    content: str
    message_index: int
    created_at: datetime


class PostMessageRequest(BaseModel):
    content: str


class ReplayRequest(BaseModel):
    from_run_id: uuid.UUID
    from_step: str
    extra_context: str = ""


class ArtifactRead(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    key: str
    version: int
    kind: str
    mime: str
    size_bytes: int
    storage: str
    is_latest: bool
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactContent(ArtifactRead):
    content: Any | None = None


class ArtifactUpdateRequest(BaseModel):
    patch_or_content: Any
    merge: str = "replace"
    mime: str = "application/json"
    metadata: dict[str, Any] | None = None


class ScheduledJobRead(BaseModel):
    id: uuid.UUID
    kind: str
    payload: dict[str, Any]
    schedule_kind: str
    schedule_spec: dict[str, Any]
    status: str
    next_run_at: datetime | None
    last_run_at: datetime | None
    owner_user_id: str
    session_id: uuid.UUID | None
    run_id: uuid.UUID | None


class ScheduledJobCreate(BaseModel):
    kind: str
    payload: dict[str, Any]
    schedule_kind: str
    schedule_spec: dict[str, Any]
    owner_user_id: str
    session_id: uuid.UUID | None = None


class RunRead(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    job_id: uuid.UUID
    kind: str
    status: str
    trigger: str
    started_at: datetime
    finished_at: datetime | None
