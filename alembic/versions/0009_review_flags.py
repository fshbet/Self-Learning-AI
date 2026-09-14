"""separate review flags (falsification / manual / quality) from dependency revalidation state

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_items", sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("knowledge_items", sa.Column("review_kind", sa.String(length=30), nullable=True))
    op.add_column("knowledge_items", sa.Column("review_reason", sa.Text(), nullable=True))
    op.add_column("knowledge_items", sa.Column("review_flagged_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_knowledge_items_needs_review", "knowledge_items", ["needs_review"])
    # falsification findings were stored in the revalidation fields (and got cleared by revalidation): move them
    op.execute(
        """
        UPDATE knowledge_items
        SET needs_review = TRUE, review_kind = 'falsification', review_reason = revalidation_reason,
            review_flagged_at = updated_at, needs_revalidation = FALSE, revalidation_reason = NULL
        WHERE revalidation_reason LIKE 'falsification:%'
        """
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_items_needs_review", table_name="knowledge_items")
    op.drop_column("knowledge_items", "review_flagged_at")
    op.drop_column("knowledge_items", "review_reason")
    op.drop_column("knowledge_items", "review_kind")
    op.drop_column("knowledge_items", "needs_review")
