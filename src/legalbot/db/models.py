"""All SQLAlchemy ORM models: mailbox, ingestion, job, session, run, artifact, interrupt, scheduled_job."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from legalbot.db.base import Base, TimestampMixin, UUIDPKMixin


class Mailbox(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "mailbox"

    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255))
    credentials: Mapped[bytes | None] = mapped_column(LargeBinary)
    cursor: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    poll_interval_sec: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="uq_mailbox_provider_external"),
    )


class IngestionItem(Base, UUIDPKMixin):
    __tablename__ = "ingestion_item"

    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(512), nullable=False)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    title: Mapped[str | None] = mapped_column(Text)
    body_text: Mapped[str | None] = mapped_column(Text)
    body_html: Mapped[str | None] = mapped_column(Text)
    sender_identity: Mapped[str | None] = mapped_column(String(512))
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    owner_user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    email_metadata: Mapped[EmailMetadata | None] = relationship(
        back_populates="ingestion_item", uselist=False, cascade="all, delete-orphan"
    )
    attachments: Mapped[list[IngestionAttachment]] = relationship(
        back_populates="ingestion_item", cascade="all, delete-orphan"
    )
    job: Mapped[ProcessingJob | None] = relationship(
        back_populates="ingestion_item", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_ingestion_item_source_external"),
    )


class EmailMetadata(Base):
    __tablename__ = "email_metadata"

    ingestion_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ingestion_item.id", ondelete="CASCADE"),
        primary_key=True,
    )
    mailbox_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mailbox.id", ondelete="SET NULL"), index=True
    )
    provider_message_id: Mapped[str | None] = mapped_column(String(512), index=True)
    provider_thread_id: Mapped[str | None] = mapped_column(String(512), index=True)
    from_addr: Mapped[str | None] = mapped_column(String(512), index=True)
    to_addrs: Mapped[list[str] | None] = mapped_column(JSONB)
    cc_addrs: Mapped[list[str] | None] = mapped_column(JSONB)
    bcc_addrs: Mapped[list[str] | None] = mapped_column(JSONB)
    in_reply_to: Mapped[str | None] = mapped_column(String(512))
    references: Mapped[list[str] | None] = mapped_column(JSONB)
    raw_headers: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    ingestion_item: Mapped[IngestionItem] = relationship(back_populates="email_metadata")


class IngestionAttachment(Base, UUIDPKMixin):
    __tablename__ = "ingestion_attachment"

    ingestion_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingestion_item.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(128))
    size: Mapped[int | None] = mapped_column(Integer)
    raw_uri: Mapped[str | None] = mapped_column(Text)
    extracted_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact.id", ondelete="SET NULL")
    )
    data_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact.id", ondelete="SET NULL")
    )
    extraction_method: Mapped[str | None] = mapped_column(String(64))
    extraction_ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    ingestion_item: Mapped[IngestionItem] = relationship(back_populates="attachments")


class ProcessingJob(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "processing_job"

    ingestion_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingestion_item.id", ondelete="CASCADE"), unique=True
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    error_kind: Mapped[str | None] = mapped_column(String(64))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("session.id", ondelete="SET NULL", use_alter=True)
    )
    owner_user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    ingestion_item: Mapped[IngestionItem] = relationship(back_populates="job")
    sessions: Mapped[list[Session]] = relationship(
        "Session",
        back_populates="job",
        foreign_keys="Session.job_id",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_processing_job_state_priority", "state", "priority", "created_at"),
        Index(
            "ix_processing_job_owner_state",
            "owner_user_id",
            "state",
        ),
    )


class Session(Base, UUIDPKMixin):
    __tablename__ = "session"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("processing_job.id", ondelete="CASCADE"),
        index=True,
    )
    thread_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(512))
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="primary")
    parent_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("session.id", ondelete="SET NULL")
    )
    branch_from_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("run.id", ondelete="SET NULL", use_alter=True)
    )
    branch_from_checkpoint_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="idle", nullable=False, index=True)
    interrupt_kind: Mapped[str | None] = mapped_column(String(32))
    active_interrupt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interrupt_request.id", ondelete="SET NULL", use_alter=True)
    )
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner_user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    job: Mapped[ProcessingJob] = relationship(back_populates="sessions", foreign_keys=[job_id])
    runs: Mapped[list[Run]] = relationship(
        back_populates="session", foreign_keys="Run.session_id", cascade="all, delete-orphan"
    )


class Run(Base, UUIDPKMixin):
    __tablename__ = "run"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("session.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("processing_job.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running", index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    session: Mapped[Session] = relationship(back_populates="runs", foreign_keys=[session_id])
    steps: Mapped[list[RunStep]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="RunStep.started_at"
    )


class RunStep(Base, UUIDPKMixin):
    __tablename__ = "run_step"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("run.id", ondelete="CASCADE"), index=True
    )
    step_name: Mapped[str] = mapped_column(String(32), nullable=False)
    checkpoint_id: Mapped[str | None] = mapped_column(String(128))
    # LangGraph subgraph namespace, e.g. extract:{run_id}:{attempt_uuid}
    checkpoint_ns: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[Run] = relationship(back_populates="steps")


class SessionMessage(Base, UUIDPKMixin):
    __tablename__ = "session_message"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("session.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("run.id", ondelete="SET NULL")
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_index: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("session_id", "message_index", name="uq_session_message_idx"),
    )


class Artifact(Base, UUIDPKMixin):
    __tablename__ = "artifact"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("session.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("run.id", ondelete="SET NULL")
    )
    producer: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    mime: Mapped[str] = mapped_column(String(128), nullable=False, default="application/json")
    storage: Mapped[str] = mapped_column(String(16), nullable=False)
    content_inline: Mapped[Any | None] = mapped_column(JSONB)
    blob_ref: Mapped[str | None] = mapped_column(Text)
    checksum: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    meta: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)
    supersedes_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact.id", ondelete="SET NULL")
    )
    is_latest: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (
        UniqueConstraint("session_id", "key", "version", name="uq_artifact_session_key_version"),
        Index(
            "uq_artifact_latest",
            "session_id",
            "key",
            unique=True,
            postgresql_where=text("is_latest"),
        ),
    )


class Draft(Base, UUIDPKMixin):
    __tablename__ = "draft"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("processing_job.id", ondelete="CASCADE"), index=True
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("session.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("run.id", ondelete="SET NULL")
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact.id", ondelete="CASCADE")
    )
    subject: Mapped[str | None] = mapped_column(Text)
    body_text: Mapped[str | None] = mapped_column(Text)
    body_html: Mapped[str | None] = mapped_column(Text)
    to_addrs: Mapped[list[str] | None] = mapped_column(JSONB)
    cc_addrs: Mapped[list[str] | None] = mapped_column(JSONB)
    in_reply_to_message_id: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    supersedes_draft_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("draft.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class InterruptRequest(Base, UUIDPKMixin):
    __tablename__ = "interrupt_request"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("session.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("run.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    schema: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    resolution: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    resolved_by: Mapped[str | None] = mapped_column(String(255))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class UserIntervention(Base, UUIDPKMixin):
    __tablename__ = "user_intervention"

    interrupt_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interrupt_request.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("run.id", ondelete="SET NULL")
    )
    step_name: Mapped[str | None] = mapped_column(String(32))
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    edited_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    decided_by: Mapped[str | None] = mapped_column(String(255))


class KgEntity(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "kg_entity"

    owner_user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_key: Mapped[str] = mapped_column(String(512), nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "type",
            "canonical_key",
            name="uq_kg_entity_owner_type_key",
        ),
        Index("ix_kg_entity_owner_type", "owner_user_id", "type"),
    )


class KgAlias(Base, UUIDPKMixin):
    __tablename__ = "kg_alias"

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("kg_entity.id", ondelete="CASCADE"),
        index=True,
    )
    alias: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("entity_id", "alias", name="uq_kg_alias_entity_alias"),
    )


class KgEdge(Base, UUIDPKMixin):
    __tablename__ = "kg_edge"

    owner_user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    src_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("kg_entity.id", ondelete="CASCADE"),
        index=True,
    )
    dst_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("kg_entity.id", ondelete="CASCADE"),
        index=True,
    )
    relation: Mapped[str] = mapped_column(String(64), nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    origin: Mapped[str] = mapped_column(String(32), nullable=False, default="deterministic")
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "src_id",
            "dst_id",
            "relation",
            "source_type",
            "source_id",
            name="uq_kg_edge_dedup",
        ),
        Index("ix_kg_edge_owner_relation", "owner_user_id", "relation"),
    )


class KgMention(Base, UUIDPKMixin):
    __tablename__ = "kg_mention"

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("kg_entity.id", ondelete="CASCADE"),
        index=True,
    )
    owner_user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    snippet: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_kg_mention_source", "owner_user_id", "source_type", "source_id"),
    )


class ScheduledJob(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "scheduled_job"

    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    schedule_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    schedule_spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    redbeat_entry_key: Mapped[str | None] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    owner_user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("session.id", ondelete="SET NULL")
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("processing_job.id", ondelete="SET NULL")
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("run.id", ondelete="SET NULL"), index=True
    )
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("run.id", ondelete="SET NULL")
    )


__all__ = [
    "Artifact",
    "Draft",
    "EmailMetadata",
    "IngestionAttachment",
    "IngestionItem",
    "InterruptRequest",
    "KgAlias",
    "KgEdge",
    "KgEntity",
    "KgMention",
    "Mailbox",
    "ProcessingJob",
    "Run",
    "RunStep",
    "ScheduledJob",
    "Session",
    "SessionMessage",
    "UserIntervention",
]
