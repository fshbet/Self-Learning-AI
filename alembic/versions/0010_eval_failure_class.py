"""primary failure class per evaluation result

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evaluation_results", sa.Column("failure_class", sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column("evaluation_results", "failure_class")
