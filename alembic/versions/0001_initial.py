"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-13
"""

from __future__ import annotations

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TSV_EXPR = (
    "to_tsvector('english', coalesce(subject,'') || ' ' || coalesce(statement,'') || ' ' || "
    "coalesce(explanation,'') || ' ' || coalesce(topic,''))"
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "domains",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("domain_id", sa.String(length=64), nullable=True),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triggered_by", sa.String(length=120), nullable=False),
        sa.ForeignKeyConstraint(["domain_id"], ["domains.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_runs_domain_id"), "runs", ["domain_id"], unique=False)
    op.create_table(
        "sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("domain_id", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("publisher", sa.String(length=200), nullable=False),
        sa.Column("source_type", sa.String(length=60), nullable=False),
        sa.Column("authority", sa.Integer(), nullable=False),
        sa.Column("access_type", sa.String(length=40), nullable=False),
        sa.Column("license", sa.String(length=200), nullable=False),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("crawl_frequency_hours", sa.Integer(), nullable=False),
        sa.Column("max_depth", sa.Integer(), nullable=False),
        sa.Column("max_pages", sa.Integer(), nullable=False),
        sa.Column("allow_patterns", sa.JSON(), nullable=False),
        sa.Column("deny_patterns", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("reliability_score", sa.Float(), nullable=False),
        sa.Column("robots_info", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["domain_id"], ["domains.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("domain_id", "url", name="uq_sources_domain_url"),
    )
    op.create_index(op.f("ix_sources_domain_id"), "sources", ["domain_id"], unique=False)
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("domain_id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=80), nullable=False),
        sa.Column("previous_content_hash", sa.String(length=80), nullable=True),
        sa.Column("raw_object_key", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=12), nullable=False),
        sa.Column("http_etag", sa.String(length=300), nullable=True),
        sa.Column("http_last_modified", sa.String(length=100), nullable=True),
        sa.Column("published_at", sa.String(length=40), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["domain_id"], ["domains.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("domain_id", "url", name="uq_documents_domain_url"),
    )
    op.create_index("ix_documents_content_hash", "documents", ["content_hash"], unique=False)
    op.create_index(op.f("ix_documents_domain_id"), "documents", ["domain_id"], unique=False)
    op.create_index(op.f("ix_documents_source_id"), "documents", ["source_id"], unique=False)
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("type", sa.String(length=60), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=300), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("locked_by", sa.String(length=120), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_jobs_idempotency"),
    )
    op.create_index("ix_jobs_pick", "jobs", ["status", "run_at", "priority"], unique=False)
    op.create_index(op.f("ix_jobs_run_id"), "jobs", ["run_id"], unique=False)
    op.create_index(op.f("ix_jobs_type"), "jobs", ["type"], unique=False)
    op.create_table(
        "knowledge_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("domain_id", sa.String(length=64), nullable=False),
        sa.Column("knowledge_type", sa.String(length=40), nullable=False),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("predicate", sa.String(length=200), nullable=False),
        sa.Column("object", sa.Text(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("topic", sa.String(length=300), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("code", sa.Text(), nullable=True),
        sa.Column("product_version", sa.String(length=120), nullable=True),
        sa.Column("language", sa.String(length=12), nullable=False),
        sa.Column("publication_date", sa.String(length=40), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("verification_level", sa.Integer(), nullable=False),
        sa.Column("quality_factors", sa.JSON(), nullable=False),
        sa.Column("scoring_rule_version", sa.String(length=20), nullable=False),
        sa.Column("content_hash", sa.String(length=80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("previous_version_id", sa.Uuid(), nullable=True),
        sa.Column("superseded_by_id", sa.Uuid(), nullable=True),
        sa.Column("duplicate_of_id", sa.Uuid(), nullable=True),
        sa.Column("extraction", sa.JSON(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(768), nullable=True),
        sa.Column("embedding_model", sa.String(length=120), nullable=True),
        sa.Column("first_discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["domain_id"], ["domains.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["duplicate_of_id"],
            ["knowledge_items.id"],
        ),
        sa.ForeignKeyConstraint(
            ["previous_version_id"],
            ["knowledge_items.id"],
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"],
            ["knowledge_items.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ki_content_hash", "knowledge_items", ["content_hash"], unique=False)
    op.create_index("ix_ki_domain_status", "knowledge_items", ["domain_id", "status"], unique=False)
    op.create_index("ix_ki_subject", "knowledge_items", ["subject"], unique=False)
    op.create_index(op.f("ix_knowledge_items_domain_id"), "knowledge_items", ["domain_id"], unique=False)
    op.create_index(op.f("ix_knowledge_items_run_id"), "knowledge_items", ["run_id"], unique=False)
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("purpose", sa.String(length=60), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_llm_calls_created_at"), "llm_calls", ["created_at"], unique=False)
    op.create_index(op.f("ix_llm_calls_purpose"), "llm_calls", ["purpose"], unique=False)
    op.create_index(op.f("ix_llm_calls_run_id"), "llm_calls", ["run_id"], unique=False)
    op.create_table(
        "conflicts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("domain_id", sa.String(length=64), nullable=False),
        sa.Column("item_a_id", sa.Uuid(), nullable=False),
        sa.Column("item_b_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["domain_id"], ["domains.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_a_id"], ["knowledge_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_b_id"], ["knowledge_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_a_id", "item_b_id", name="uq_conflict_pair"),
    )
    op.create_index(op.f("ix_conflicts_domain_id"), "conflicts", ["domain_id"], unique=False)
    op.create_table(
        "evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_item_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("evidence_type", sa.String(length=30), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("locator", sa.JSON(), nullable=False),
        sa.Column("document_hash", sa.String(length=80), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["knowledge_item_id"], ["knowledge_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_evidence_document_id"), "evidence", ["document_id"], unique=False)
    op.create_index(op.f("ix_evidence_knowledge_item_id"), "evidence", ["knowledge_item_id"], unique=False)
    op.create_index(op.f("ix_evidence_source_id"), "evidence", ["source_id"], unique=False)
    op.create_table(
        "status_transitions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_item_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["knowledge_item_id"], ["knowledge_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_status_transitions_knowledge_item_id"), "status_transitions", ["knowledge_item_id"], unique=False
    )
    # Retrieval indexes (§22): lexical GIN on the same expression used by hybrid_search, HNSW for vectors.
    op.execute(f"CREATE INDEX ix_ki_tsv ON knowledge_items USING gin ({TSV_EXPR})")
    op.execute("CREATE INDEX ix_ki_embedding_hnsw ON knowledge_items USING hnsw (embedding vector_cosine_ops)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ki_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_ki_tsv")
    op.drop_table("status_transitions")
    op.drop_table("evidence")
    op.drop_table("conflicts")
    op.drop_table("llm_calls")
    op.drop_table("knowledge_items")
    op.drop_table("jobs")
    op.drop_table("documents")
    op.drop_table("sources")
    op.drop_table("runs")
    op.drop_table("domains")
