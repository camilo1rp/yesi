"""run_step.checkpoint_ns for subgraph replay audit

Revision ID: 0002_run_step_checkpoint_ns
Revises: 0001_init
Create Date: 2026-05-02

Stores the LangGraph ``checkpoint_ns`` used for a stage attempt so replay and
API consumers can target the exact subgraph stream (see ``_StageCheckpointNamespace``).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_run_step_checkpoint_ns"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("run_step", sa.Column("checkpoint_ns", sa.String(256), nullable=True))


def downgrade() -> None:
    op.drop_column("run_step", "checkpoint_ns")
