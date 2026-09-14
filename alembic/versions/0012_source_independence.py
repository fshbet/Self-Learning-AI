"""source independence: text fingerprint + canonical URL on documents, declared mirror sources

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("text_fingerprint", sa.String(length=80), nullable=True))
    op.add_column("documents", sa.Column("canonical_url", sa.Text(), nullable=True))
    op.create_index("ix_documents_domain_fingerprint", "documents", ["domain_id", "text_fingerprint"])
    op.add_column("sources", sa.Column("mirror_of_source_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_sources_mirror_of", "sources", "sources", ["mirror_of_source_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint("fk_sources_mirror_of", "sources", type_="foreignkey")
    op.drop_column("sources", "mirror_of_source_id")
    op.drop_index("ix_documents_domain_fingerprint", table_name="documents")
    op.drop_column("documents", "canonical_url")
    op.drop_column("documents", "text_fingerprint")
