"""canonical knowledge snapshots

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("domain_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("base_snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("integrity_hash", sa.String(length=80), nullable=True),
        sa.Column("object_prefix", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["base_snapshot_id"], ["snapshots.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["domain_id"], ["domains.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("domain_id", "version", name="uq_snapshot_domain_version"),
    )
    op.create_index("ix_snapshots_domain_id", "snapshots", ["domain_id"])


def downgrade() -> None:
    op.drop_index("ix_snapshots_domain_id", table_name="snapshots")
    op.drop_table("snapshots")
