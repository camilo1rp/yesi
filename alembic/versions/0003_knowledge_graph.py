"""knowledge graph tables (Phase 1 deterministic indexing)

Revision ID: 0003_knowledge_graph
Revises: 0002_run_step_checkpoint_ns
Create Date: 2026-07-31

Creates tenant-scoped entity graph tables for cross-session document linkage.
Embedding column is nullable; HNSW index deferred to Phase 2.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_knowledge_graph"
down_revision = "0002_run_step_checkpoint_ns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "kg_entity",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", sa.String(255), nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("canonical_name", sa.Text, nullable=False),
        sa.Column("canonical_key", sa.String(512), nullable=False),
        sa.Column("attributes", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("embedding", postgresql.ARRAY(sa.Float), nullable=True),  # vector in raw SQL below
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "owner_user_id",
            "type",
            "canonical_key",
            name="uq_kg_entity_owner_type_key",
        ),
    )
    # Replace ARRAY placeholder with pgvector column (Alembic-friendly).
    op.execute("ALTER TABLE kg_entity DROP COLUMN embedding")
    op.execute("ALTER TABLE kg_entity ADD COLUMN embedding vector(1536)")

    op.create_index("ix_kg_entity_owner", "kg_entity", ["owner_user_id"])
    op.create_index("ix_kg_entity_owner_type", "kg_entity", ["owner_user_id", "type"])
    op.execute(
        "CREATE INDEX ix_kg_entity_canonical_name_trgm ON kg_entity "
        "USING gin (canonical_name gin_trgm_ops)"
    )

    op.create_table(
        "kg_alias",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "entity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("kg_entity.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias", sa.Text, nullable=False),
        sa.UniqueConstraint("entity_id", "alias", name="uq_kg_alias_entity_alias"),
    )
    op.create_index("ix_kg_alias_entity", "kg_alias", ["entity_id"])
    op.execute(
        "CREATE INDEX ix_kg_alias_alias_trgm ON kg_alias USING gin (alias gin_trgm_ops)"
    )

    op.create_table(
        "kg_edge",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", sa.String(255), nullable=False),
        sa.Column(
            "src_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("kg_entity.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dst_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("kg_entity.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relation", sa.String(64), nullable=False),
        sa.Column("attributes", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("confidence", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("origin", sa.String(32), nullable=False, server_default="deterministic"),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "owner_user_id",
            "src_id",
            "dst_id",
            "relation",
            "source_type",
            "source_id",
            name="uq_kg_edge_dedup",
        ),
    )
    op.create_index("ix_kg_edge_owner", "kg_edge", ["owner_user_id"])
    op.create_index("ix_kg_edge_src", "kg_edge", ["src_id"])
    op.create_index("ix_kg_edge_dst", "kg_edge", ["dst_id"])
    op.create_index("ix_kg_edge_owner_relation", "kg_edge", ["owner_user_id", "relation"])

    op.create_table(
        "kg_mention",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "entity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("kg_entity.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner_user_id", sa.String(255), nullable=False),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("snippet", sa.Text, nullable=True),
        sa.Column("confidence", sa.Float, nullable=False, server_default="1.0"),
        sa.Column(
            "extracted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_kg_mention_entity", "kg_mention", ["entity_id"])
    op.create_index("ix_kg_mention_owner", "kg_mention", ["owner_user_id"])
    op.create_index(
        "ix_kg_mention_source",
        "kg_mention",
        ["owner_user_id", "source_type", "source_id"],
    )


def downgrade() -> None:
    op.drop_table("kg_mention")
    op.drop_table("kg_edge")
    op.drop_table("kg_alias")
    op.drop_table("kg_entity")
