"""document mirrors (source independence) and section-level chunk hashes

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("chunk_hashes", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("documents", sa.Column("canonical_document_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_documents_canonical", "documents", "documents", ["canonical_document_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_documents_canonical", "documents", ["canonical_document_id"])
    op.create_index("ix_documents_domain_hash", "documents", ["domain_id", "content_hash"])
    # backfill: identical text fetched from different sources → later documents mirror the earliest one
    op.execute(
        """
        UPDATE documents d SET canonical_document_id = c.id
        FROM (
            SELECT DISTINCT ON (domain_id, content_hash) id, domain_id, content_hash, source_id
            FROM documents ORDER BY domain_id, content_hash, fetched_at ASC, id ASC
        ) c
        WHERE d.domain_id = c.domain_id AND d.content_hash = c.content_hash
          AND d.id <> c.id AND d.source_id <> c.source_id
        """
    )


def downgrade() -> None:
    op.drop_index("ix_documents_domain_hash", table_name="documents")
    op.drop_index("ix_documents_canonical", table_name="documents")
    op.drop_constraint("fk_documents_canonical", "documents", type_="foreignkey")
    op.drop_column("documents", "canonical_document_id")
    op.drop_column("documents", "chunk_hashes")
