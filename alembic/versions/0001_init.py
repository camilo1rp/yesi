"""initial schema

Revision ID: 0001_init
Revises:
Create Date: 2026-04-19

Creates pgvector extension plus all app-owned tables. LangGraph-owned tables
(`checkpoints`, `checkpoint_writes`, `store`) are created by
`AsyncPostgresSaver.setup()` + `AsyncPostgresStore.setup()` at app startup.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    op.create_table(
        "mailbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255)),
        sa.Column("credentials", sa.LargeBinary),
        sa.Column("cursor", postgresql.JSONB),
        sa.Column("poll_interval_sec", sa.Integer, nullable=False, server_default="60"),
        sa.Column("state", sa.String(16), nullable=False, server_default="active"),
        sa.Column("owner_user_id", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("provider", "external_id", name="uq_mailbox_provider_external"),
    )
    op.create_index("ix_mailbox_provider", "mailbox", ["provider"])
    op.create_index("ix_mailbox_owner", "mailbox", ["owner_user_id"])

    op.create_table(
        "ingestion_item",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(512), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True)),
        sa.Column("title", sa.Text),
        sa.Column("body_text", sa.Text),
        sa.Column("body_html", sa.Text),
        sa.Column("sender_identity", sa.String(512)),
        sa.Column("raw_payload", postgresql.JSONB),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("owner_user_id", sa.String(255), nullable=False),
        sa.UniqueConstraint("source", "external_id", name="uq_ingestion_item_source_external"),
    )
    op.create_index("ix_ingestion_item_source", "ingestion_item", ["source"])
    op.create_index("ix_ingestion_item_received", "ingestion_item", ["received_at"])
    op.create_index("ix_ingestion_item_owner", "ingestion_item", ["owner_user_id"])

    op.create_table(
        "email_metadata",
        sa.Column(
            "ingestion_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ingestion_item.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "mailbox_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mailbox.id", ondelete="SET NULL"),
        ),
        sa.Column("provider_message_id", sa.String(512)),
        sa.Column("provider_thread_id", sa.String(512)),
        sa.Column("from_addr", sa.String(512)),
        sa.Column("to_addrs", postgresql.JSONB),
        sa.Column("cc_addrs", postgresql.JSONB),
        sa.Column("bcc_addrs", postgresql.JSONB),
        sa.Column("in_reply_to", sa.String(512)),
        sa.Column("references", postgresql.JSONB),
        sa.Column("raw_headers", postgresql.JSONB),
    )
    op.create_index("ix_email_meta_mailbox", "email_metadata", ["mailbox_id"])
    op.create_index("ix_email_meta_provider_msg", "email_metadata", ["provider_message_id"])
    op.create_index("ix_email_meta_provider_thread", "email_metadata", ["provider_thread_id"])
    op.create_index("ix_email_meta_from", "email_metadata", ["from_addr"])

    # processing_job created before ingestion_attachment because the FK from attachment→artifact
    # references artifact. We break the chicken-and-egg cycle by creating artifact without its
    # FK first, then ingestion_attachment, then adding the optional FK.
    op.create_table(
        "processing_job",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "ingestion_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ingestion_item.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("priority", sa.Integer, nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text),
        sa.Column("error_kind", sa.String(64)),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("processing_started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("current_session_id", postgresql.UUID(as_uuid=True)),
        sa.Column("owner_user_id", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_processing_job_state", "processing_job", ["state"])
    op.create_index("ix_processing_job_state_priority", "processing_job", ["state", "priority", "created_at"])
    op.create_index("ix_processing_job_owner_state", "processing_job", ["owner_user_id", "state"])

    op.create_table(
        "session",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("processing_job.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("thread_id", sa.String(128), nullable=False, unique=True),
        sa.Column("title", sa.String(512)),
        sa.Column("kind", sa.String(16), nullable=False, server_default="primary"),
        sa.Column("parent_session_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("session.id", ondelete="SET NULL")),
        sa.Column("branch_from_run_id", postgresql.UUID(as_uuid=True)),
        sa.Column("branch_from_checkpoint_id", sa.String(128)),
        sa.Column("status", sa.String(32), nullable=False, server_default="idle"),
        sa.Column("interrupt_kind", sa.String(32)),
        sa.Column("active_interrupt_id", postgresql.UUID(as_uuid=True)),
        sa.Column("message_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_activity_at", sa.DateTime(timezone=True)),
        sa.Column("owner_user_id", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_session_job", "session", ["job_id"])
    op.create_index("ix_session_status", "session", ["status"])
    op.create_index("ix_session_owner", "session", ["owner_user_id"])

    op.create_foreign_key(
        "fk_processing_job_current_session",
        "processing_job",
        "session",
        ["current_session_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "run",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("session.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("processing_job.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("trigger", sa.String(32), nullable=False),
        sa.Column("trigger_payload", postgresql.JSONB),
    )
    op.create_index("ix_run_session", "run", ["session_id"])
    op.create_index("ix_run_job", "run", ["job_id"])
    op.create_index("ix_run_status", "run", ["status"])
    op.create_foreign_key(
        "fk_session_branch_run", "session", "run", ["branch_from_run_id"], ["id"], ondelete="SET NULL"
    )

    op.create_table(
        "run_step",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_name", sa.String(32), nullable=False),
        sa.Column("checkpoint_id", sa.String(128)),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("summary", sa.Text),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_run_step_run", "run_step", ["run_id"])

    op.create_table(
        "session_message",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("session.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("run.id", ondelete="SET NULL")),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("message_index", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("session_id", "message_index", name="uq_session_message_idx"),
    )
    op.create_index("ix_session_message_session", "session_message", ["session_id"])

    op.create_table(
        "artifact",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("session.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("run.id", ondelete="SET NULL")),
        sa.Column("producer", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("mime", sa.String(128), nullable=False, server_default="application/json"),
        sa.Column("storage", sa.String(16), nullable=False),
        sa.Column("content_inline", postgresql.JSONB),
        sa.Column("blob_ref", sa.Text),
        sa.Column("checksum", sa.String(128)),
        sa.Column("size_bytes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("metadata", postgresql.JSONB),
        sa.Column("supersedes_artifact_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("artifact.id", ondelete="SET NULL")),
        sa.Column("is_latest", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(255)),
        sa.CheckConstraint(
            "(content_inline IS NOT NULL AND blob_ref IS NULL) OR "
            "(content_inline IS NULL AND blob_ref IS NOT NULL)",
            name="ck_artifact_storage_xor",
        ),
        sa.UniqueConstraint("session_id", "key", "version", name="uq_artifact_session_key_version"),
    )
    op.create_index("ix_artifact_session", "artifact", ["session_id"])
    op.create_index("ix_artifact_kind", "artifact", ["kind"])
    op.execute(
        "CREATE UNIQUE INDEX uq_artifact_latest ON artifact (session_id, key) WHERE is_latest"
    )

    op.create_table(
        "ingestion_attachment",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ingestion_item_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("ingestion_item.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("mime_type", sa.String(128)),
        sa.Column("size", sa.Integer),
        sa.Column("raw_uri", sa.Text),
        sa.Column("extracted_artifact_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("artifact.id", ondelete="SET NULL")),
        sa.Column("data_artifact_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("artifact.id", ondelete="SET NULL")),
        sa.Column("extraction_method", sa.String(64)),
        sa.Column("extraction_ok", sa.Boolean, nullable=False, server_default="false"),
    )
    op.create_index("ix_ingestion_attachment_item", "ingestion_attachment", ["ingestion_item_id"])

    op.create_table(
        "draft",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("processing_job.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("session.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("run.id", ondelete="SET NULL")),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("artifact.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject", sa.Text),
        sa.Column("body_text", sa.Text),
        sa.Column("body_html", sa.Text),
        sa.Column("to_addrs", postgresql.JSONB),
        sa.Column("cc_addrs", postgresql.JSONB),
        sa.Column("in_reply_to_message_id", sa.String(512)),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("supersedes_draft_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("draft.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_draft_job", "draft", ["job_id"])
    op.create_index("ix_draft_session", "draft", ["session_id"])

    op.create_table(
        "interrupt_request",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("session.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("run.id", ondelete="SET NULL")),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("schema", postgresql.JSONB),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("resolution", postgresql.JSONB),
        sa.Column("resolved_by", sa.String(255)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_interrupt_session", "interrupt_request", ["session_id"])
    op.create_index("ix_interrupt_kind", "interrupt_request", ["kind"])
    op.create_index("ix_interrupt_status", "interrupt_request", ["status"])
    op.create_index("ix_interrupt_expires", "interrupt_request", ["expires_at"])
    op.create_foreign_key(
        "fk_session_active_interrupt", "session", "interrupt_request",
        ["active_interrupt_id"], ["id"], ondelete="SET NULL",
    )

    op.create_table(
        "user_intervention",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("interrupt_request_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("interrupt_request.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("run.id", ondelete="SET NULL")),
        sa.Column("step_name", sa.String(32)),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("edited_payload", postgresql.JSONB),
        sa.Column("decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("decided_by", sa.String(255)),
    )
    op.create_index("ix_intervention_interrupt", "user_intervention", ["interrupt_request_id"])

    op.create_table(
        "scheduled_job",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("schedule_kind", sa.String(16), nullable=False),
        sa.Column("schedule_spec", postgresql.JSONB, nullable=False),
        sa.Column("redbeat_entry_key", sa.String(255)),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_result", postgresql.JSONB),
        sa.Column("owner_user_id", sa.String(255), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("session.id", ondelete="SET NULL")),
        sa.Column("job_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("processing_job.id", ondelete="SET NULL")),
        sa.Column("run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("run.id", ondelete="SET NULL")),
        sa.Column("parent_run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("run.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_scheduled_job_kind", "scheduled_job", ["kind"])
    op.create_index("ix_scheduled_job_entry", "scheduled_job", ["redbeat_entry_key"])
    op.create_index("ix_scheduled_job_status", "scheduled_job", ["status"])
    op.create_index("ix_scheduled_job_next_run", "scheduled_job", ["next_run_at"])
    op.create_index("ix_scheduled_job_owner", "scheduled_job", ["owner_user_id"])
    op.create_index("ix_scheduled_job_run", "scheduled_job", ["run_id"])


def downgrade() -> None:
    op.drop_table("scheduled_job")
    op.drop_table("user_intervention")
    op.drop_constraint("fk_session_active_interrupt", "session", type_="foreignkey")
    op.drop_table("interrupt_request")
    op.drop_table("draft")
    op.drop_table("ingestion_attachment")
    op.execute("DROP INDEX IF EXISTS uq_artifact_latest")
    op.drop_table("artifact")
    op.drop_table("session_message")
    op.drop_table("run_step")
    op.drop_constraint("fk_session_branch_run", "session", type_="foreignkey")
    op.drop_table("run")
    op.drop_constraint("fk_processing_job_current_session", "processing_job", type_="foreignkey")
    op.drop_table("session")
    op.drop_table("processing_job")
    op.drop_table("email_metadata")
    op.drop_table("ingestion_item")
    op.drop_table("mailbox")
