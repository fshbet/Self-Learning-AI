"""user-defined sources and discovery keywords

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("origin", sa.String(length=20), nullable=False, server_default="plugin"))
    op.execute("UPDATE sources SET origin = 'discovered' WHERE key LIKE 'discovered:%'")
    op.create_table(
        "domain_keywords",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("domain_id", sa.String(length=64), nullable=False),
        sa.Column("keyword", sa.String(length=300), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["domain_id"], ["domains.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("domain_id", "keyword", name="uq_domain_keyword"),
    )
    op.create_index("ix_domain_keywords_domain_id", "domain_keywords", ["domain_id"])


def downgrade() -> None:
    op.drop_index("ix_domain_keywords_domain_id", table_name="domain_keywords")
    op.drop_table("domain_keywords")
    op.drop_column("sources", "origin")
