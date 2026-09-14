"""explicit freshness timestamps: source last checked / content last changed (item), content_changed_at (document)

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("content_changed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("knowledge_items", sa.Column("last_source_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("knowledge_items", sa.Column("last_content_changed_at", sa.DateTime(timezone=True), nullable=True))
    # backfill: a document's content last changed when it was fetched (version 1) - we have no better record;
    # an item's source was last checked when the newest document holding its verified quote was fetched
    op.execute("UPDATE documents SET content_changed_at = fetched_at WHERE content_changed_at IS NULL")
    op.execute(
        """
        UPDATE knowledge_items k SET last_source_checked_at = sub.checked
        FROM (
            SELECT e.knowledge_item_id AS item_id, MAX(d.fetched_at) AS checked
            FROM evidence e JOIN documents d ON d.id = e.document_id
            WHERE e.evidence_type = 'extraction' AND e.verified
            GROUP BY e.knowledge_item_id
        ) sub
        WHERE k.id = sub.item_id
        """
    )


def downgrade() -> None:
    op.drop_column("knowledge_items", "last_content_changed_at")
    op.drop_column("knowledge_items", "last_source_checked_at")
    op.drop_column("documents", "content_changed_at")
