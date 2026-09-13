"""knowledge origin, provenance, polarity, details; evidence relation/retrieved_at; source class

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_items", sa.Column("origin", sa.String(32), nullable=False, server_default="DIRECT"))
    op.add_column("knowledge_items", sa.Column("provenance", sa.String(32), nullable=False, server_default="EXTERNAL"))
    op.add_column("knowledge_items", sa.Column("polarity", sa.String(16), nullable=False, server_default="positive"))
    op.add_column("knowledge_items", sa.Column("effective_date", sa.String(40), nullable=True))
    op.add_column("knowledge_items", sa.Column("details", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("knowledge_items", sa.Column("needs_revalidation", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("knowledge_items", sa.Column("revalidation_reason", sa.Text(), nullable=True))
    op.add_column("knowledge_items", sa.Column("validator_versions", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("evidence", sa.Column("relation", sa.String(20), nullable=False, server_default="supports"))
    op.add_column("evidence", sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("evidence", sa.Column("source_version", sa.Integer(), nullable=True))
    op.add_column("sources", sa.Column("source_class", sa.String(20), nullable=False, server_default="external"))
    op.add_column("sources", sa.Column("relevance", sa.Integer(), nullable=False, server_default="50"))

    # ---- backfill with the same rules as core/quality/provenance.py
    op.execute("UPDATE sources SET source_class = 'official' WHERE authority >= 80")
    op.execute("UPDATE sources SET source_class = 'community' WHERE origin = 'discovered'")
    op.execute("UPDATE evidence SET relation = 'validates' WHERE evidence_type = 'validator'")
    op.execute("UPDATE evidence SET relation = 'approves' WHERE evidence_type = 'human'")
    op.execute(
        "UPDATE evidence e SET retrieved_at = d.fetched_at, source_version = d.version "
        "FROM documents d WHERE e.document_id = d.id AND e.retrieved_at IS NULL"
    )
    op.execute(
        "UPDATE knowledge_items SET polarity = 'negative' "
        "WHERE lower(knowledge_type) IN ('limitation','warning','anti_pattern','common_mistake','pitfall')"
    )
    op.execute(
        """
        UPDATE knowledge_items k SET provenance = sub.prov FROM (
            SELECT e.knowledge_item_id AS item_id,
                   CASE (array_agg(s.source_class ORDER BY s.authority DESC))[1]
                     WHEN 'official' THEN 'OFFICIAL'
                     WHEN 'community' THEN 'COMMUNITY'
                     WHEN 'organization' THEN 'ORGANIZATION'
                     ELSE 'EXTERNAL'
                   END AS prov
            FROM evidence e JOIN sources s ON s.id = e.source_id
            WHERE e.evidence_type = 'extraction'
            GROUP BY e.knowledge_item_id
        ) sub WHERE k.id = sub.item_id
        """
    )
    op.execute(
        "UPDATE knowledge_items k SET origin = 'EXPERIMENTALLY_VALIDATED' WHERE EXISTS ("
        "SELECT 1 FROM evidence e WHERE e.knowledge_item_id = k.id AND e.evidence_type = 'validator' "
        "AND (e.details->>'passed')::boolean IS TRUE)"
    )


def downgrade() -> None:
    for col in ("relevance", "source_class"):
        op.drop_column("sources", col)
    for col in ("source_version", "retrieved_at", "relation"):
        op.drop_column("evidence", col)
    for col in (
        "validator_versions",
        "revalidation_reason",
        "needs_revalidation",
        "details",
        "effective_date",
        "polarity",
        "provenance",
        "origin",
    ):
        op.drop_column("knowledge_items", col)
