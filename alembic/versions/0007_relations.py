"""knowledge dependency graph

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_relations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("domain_id", sa.String(length=64), nullable=False),
        sa.Column("from_item_id", sa.Uuid(), nullable=False),
        sa.Column("to_item_id", sa.Uuid(), nullable=False),
        sa.Column("relation_type", sa.String(length=30), nullable=False),
        sa.Column("origin", sa.String(length=20), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["domain_id"], ["domains.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["from_item_id"], ["knowledge_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["to_item_id"], ["knowledge_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("from_item_id", "to_item_id", "relation_type", name="uq_relation"),
    )
    op.create_index("ix_knowledge_relations_domain_id", "knowledge_relations", ["domain_id"])
    op.create_index("ix_knowledge_relations_from_item_id", "knowledge_relations", ["from_item_id"])
    op.create_index("ix_relations_to", "knowledge_relations", ["to_item_id"])


def downgrade() -> None:
    op.drop_index("ix_relations_to", table_name="knowledge_relations")
    op.drop_index("ix_knowledge_relations_from_item_id", table_name="knowledge_relations")
    op.drop_index("ix_knowledge_relations_domain_id", table_name="knowledge_relations")
    op.drop_table("knowledge_relations")
