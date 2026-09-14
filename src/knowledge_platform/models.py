"""SQLAlchemy ORM model.

Design notes (see docs: modular_self_updating_knowledge_platform.md):

* ``Source``      – registry row; the control system for collection (§8).
* ``Document``    – one collected artefact, identified by content hash; raw bytes
                    live in the object store, normalized text lives here (§13).
* ``KnowledgeItem`` – versioned, structured knowledge with status lifecycle (§14).
* ``Evidence``    – verifiable pointer from an item into a document (§15).
* ``StatusTransition`` – audit trail of lifecycle changes.
* ``Conflict``    – two items that disagree (§18).
* ``Job`` / ``Run`` – PostgreSQL-backed queue and run identity (§28).
* ``LLMCall``     – cost/latency accounting for every model call (§39/§40).
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .config import get_settings

EMBEDDING_DIM = get_settings().embedding_dimension


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> uuid.UUID:
    return uuid.uuid4()


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


# --------------------------------------------------------------------------- enums


class ItemStatus(enum.StrEnum):
    EXTRACTED = "EXTRACTED"
    CANDIDATE = "CANDIDATE"
    SUPPORTED = "SUPPORTED"
    VERIFIED = "VERIFIED"
    CONFLICTED = "CONFLICTED"
    STALE = "STALE"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


# Allowed transitions (§14). "*" means any non-terminal state.
ALLOWED_TRANSITIONS: dict[ItemStatus, set[ItemStatus]] = {
    ItemStatus.EXTRACTED: {ItemStatus.CANDIDATE, ItemStatus.REJECTED},
    ItemStatus.CANDIDATE: {ItemStatus.SUPPORTED, ItemStatus.REJECTED},
    ItemStatus.SUPPORTED: {ItemStatus.VERIFIED, ItemStatus.CONFLICTED, ItemStatus.REJECTED, ItemStatus.STALE},
    ItemStatus.VERIFIED: {ItemStatus.CONFLICTED, ItemStatus.STALE, ItemStatus.SUPERSEDED, ItemStatus.REJECTED},
    ItemStatus.CONFLICTED: {ItemStatus.VERIFIED, ItemStatus.SUPERSEDED, ItemStatus.REJECTED, ItemStatus.SUPPORTED},
    ItemStatus.STALE: {ItemStatus.SUPPORTED, ItemStatus.VERIFIED, ItemStatus.SUPERSEDED, ItemStatus.REJECTED},
    ItemStatus.REJECTED: set(),
    ItemStatus.SUPERSEDED: set(),
}


class SourceStatus(enum.StrEnum):
    CANDIDATE = "CANDIDATE"  # discovered by search, awaiting approval
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    BLOCKED = "BLOCKED"  # robots.txt / terms forbid collection


class DocumentStatus(enum.StrEnum):
    FETCHED = "FETCHED"
    EXTRACTED = "EXTRACTED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class JobStatus(enum.StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"  # will retry
    DEAD = "DEAD"  # retries exhausted (dead-letter)


class RunStatus(enum.StrEnum):
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


# --------------------------------------------------------------------------- tables


class Domain(Base):
    __tablename__ = "domains"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # slug, e.g. "powerbi"
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[str] = mapped_column(String(32), default="0.0.0")
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    sources: Mapped[list[Source]] = relationship(back_populates="domain", passive_deletes=True)


class DomainKeyword(Base):
    """User-defined discovery keywords (§9): merged with the plugin's discovery_queries when discovering sources."""

    __tablename__ = "domain_keywords"
    __table_args__ = (UniqueConstraint("domain_id", "keyword", name="uq_domain_keyword"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    domain_id: Mapped[str] = mapped_column(ForeignKey("domains.id", ondelete="CASCADE"), index=True)
    keyword: Mapped[str] = mapped_column(String(300))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(120), default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("domain_id", "url", name="uq_sources_domain_url"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    domain_id: Mapped[str] = mapped_column(ForeignKey("domains.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(120))  # stable key from sources.yaml / user:<slug> / discovered:<host>
    origin: Mapped[str] = mapped_column(String(20), default="plugin")  # plugin | user | discovered
    source_class: Mapped[str] = mapped_column(
        String(20), default="external"
    )  # official|external|community|organization
    relevance: Mapped[int] = mapped_column(Integer, default=50)
    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(Text)
    publisher: Mapped[str] = mapped_column(String(200), default="")
    source_type: Mapped[str] = mapped_column(String(60), default="web")  # web|pdf|api|git|video
    authority: Mapped[int] = mapped_column(Integer, default=50)  # 0-100
    access_type: Mapped[str] = mapped_column(String(40), default="public")
    license: Mapped[str] = mapped_column(String(200), default="")
    permissions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # read/store/process/train/redistribute
    crawl_frequency_hours: Mapped[int] = mapped_column(Integer, default=168)
    max_depth: Mapped[int] = mapped_column(Integer, default=2)
    max_pages: Mapped[int] = mapped_column(Integer, default=50)
    allow_patterns: Mapped[list[Any]] = mapped_column(JSON, default=list)  # regexes a URL must match
    deny_patterns: Mapped[list[Any]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default=SourceStatus.ACTIVE)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    reliability_score: Mapped[float] = mapped_column(Float, default=1.0)
    robots_info: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")
    # declared shared primary (audit P1.9): this source republishes another one; its documents count as the
    # primary's for independence
    mirror_of_source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    domain: Mapped[Domain] = relationship(back_populates="sources")
    documents: Mapped[list[Document]] = relationship(back_populates="source", passive_deletes=True)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("domain_id", "url", name="uq_documents_domain_url"),
        Index("ix_documents_content_hash", "content_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    domain_id: Mapped[str] = mapped_column(ForeignKey("domains.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(80))  # sha256 of normalized text
    content_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # last content change
    # source independence (audit P1.9): loose fingerprint (letters/digits only) and the page's declared canonical
    text_fingerprint: Mapped[str | None] = mapped_column(String(80))
    canonical_url: Mapped[str | None] = mapped_column(Text)
    previous_content_hash: Mapped[str | None] = mapped_column(String(80))
    raw_object_key: Mapped[str] = mapped_column(Text)  # object store key of raw bytes
    text: Mapped[str] = mapped_column(Text, default="")  # normalized markdown-ish text
    language: Mapped[str] = mapped_column(String(12), default="en")
    http_etag: Mapped[str | None] = mapped_column(String(300))
    http_last_modified: Mapped[str | None] = mapped_column(String(100))
    published_at: Mapped[str | None] = mapped_column(String(40))  # as reported by page metadata
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    byte_size: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default=DocumentStatus.FETCHED)
    error: Mapped[str | None] = mapped_column(Text)
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # section-level change detection: [{index, heading, sha256, prompt}] of the last extraction (req. 25)
    chunk_hashes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # same text fetched from another source: this document mirrors the earliest one (source independence)
    canonical_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), index=True
    )

    source: Mapped[Source] = relationship(back_populates="documents")
    evidence: Mapped[list[Evidence]] = relationship(back_populates="document", passive_deletes=True)


class KnowledgeItem(Base):
    __tablename__ = "knowledge_items"
    __table_args__ = (
        Index("ix_ki_domain_status", "domain_id", "status"),
        Index("ix_ki_content_hash", "content_hash"),
        Index("ix_ki_subject", "subject"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    domain_id: Mapped[str] = mapped_column(ForeignKey("domains.id", ondelete="CASCADE"), index=True)
    knowledge_type: Mapped[str] = mapped_column(String(40), default="fact")
    subject: Mapped[str] = mapped_column(String(300))
    predicate: Mapped[str] = mapped_column(String(200))
    object: Mapped[str] = mapped_column(Text)
    statement: Mapped[str] = mapped_column(Text)  # one-sentence normalized claim
    explanation: Mapped[str] = mapped_column(Text, default="")
    topic: Mapped[str] = mapped_column(String(300), default="")  # taxonomy path "DAX/Filter context"
    tags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    code: Mapped[str | None] = mapped_column(Text)  # code sample if the item is an example
    product_version: Mapped[str | None] = mapped_column(String(120))
    language: Mapped[str] = mapped_column(String(12), default="en")
    publication_date: Mapped[str | None] = mapped_column(String(40))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # knowledge origin / provenance / polarity (req. 8, 9, 19)
    origin: Mapped[str] = mapped_column(
        String(32), default="DIRECT"
    )  # DIRECT|DERIVED|SYNTHESIZED|EXPERIMENTALLY_VALIDATED
    provenance: Mapped[str] = mapped_column(
        String(32), default="EXTERNAL"
    )  # OFFICIAL|EXTERNAL|COMMUNITY|USER|ORGANIZATION|DERIVED
    polarity: Mapped[str] = mapped_column(String(16), default="positive")  # positive | negative (what does NOT work)
    effective_date: Mapped[str | None] = mapped_column(String(40))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # structured example / condition fields
    # dependency state (cleared by revalidation once every dependency is live again)
    needs_revalidation: Mapped[bool] = mapped_column(Boolean, default=False)
    revalidation_reason: Mapped[str | None] = mapped_column(Text)
    # review flag (falsification | manual | quality): only a reviewer clears it, see verification/review_flags
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    review_kind: Mapped[str | None] = mapped_column(String(30))
    review_reason: Mapped[str | None] = mapped_column(Text)
    review_flagged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validator_versions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    status: Mapped[str] = mapped_column(String(20), default=ItemStatus.EXTRACTED)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    verification_level: Mapped[int] = mapped_column(Integer, default=0)
    quality_factors: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    scoring_rule_version: Mapped[str] = mapped_column(String(20), default="")

    content_hash: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer, default=1)
    previous_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("knowledge_items.id"))
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("knowledge_items.id"))
    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("knowledge_items.id"))

    extraction: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # method, extractor_version, model
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(String(120))

    first_discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # verification event: the item reached VERIFIED (scoring or a reviewer). Not touched by crawls.
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # freshness (audit P1.4): a source holding this item's verified quote was re-fetched and still contains
    # it / that source's content changed while the quote persisted. Neither claims a new verification.
    last_source_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_content_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id"), index=True)

    evidence: Mapped[list[Evidence]] = relationship(
        back_populates="item", cascade="all, delete-orphan", order_by="Evidence.created_at"
    )
    transitions: Mapped[list[StatusTransition]] = relationship(
        back_populates="item", cascade="all, delete-orphan", order_by="StatusTransition.created_at"
    )


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    knowledge_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), index=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"), index=True)
    evidence_type: Mapped[str] = mapped_column(String(30), default="extraction")  # extraction|validator|human
    relation: Mapped[str] = mapped_column(String(20), default="supports")  # supports|contradicts|validates|approves
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_version: Mapped[int | None] = mapped_column(Integer)  # document version at collection time
    excerpt: Mapped[str] = mapped_column(Text)  # verbatim quote from the document
    locator: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # chunk index, offsets, heading path
    document_hash: Mapped[str | None] = mapped_column(String(80))
    url: Mapped[str | None] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)  # excerpt located verbatim in document
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    item: Mapped[KnowledgeItem] = relationship(back_populates="evidence")
    document: Mapped[Document | None] = relationship(back_populates="evidence")


class KnowledgeRelation(Base):
    """Dependency graph edge (req. 17): ``from_item`` ─relation_type─▶ ``to_item``."""

    __tablename__ = "knowledge_relations"
    __table_args__ = (
        UniqueConstraint("from_item_id", "to_item_id", "relation_type", name="uq_relation"),
        Index("ix_relations_to", "to_item_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    domain_id: Mapped[str] = mapped_column(ForeignKey("domains.id", ondelete="CASCADE"), index=True)
    from_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_items.id", ondelete="CASCADE"), index=True)
    to_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_items.id", ondelete="CASCADE"))
    relation_type: Mapped[str] = mapped_column(
        String(30)
    )  # depends_on|example_of|derived_from|related_to|supersedes|contradicts
    origin: Mapped[str] = mapped_column(String(20), default="system")  # system | extractor | user
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StatusTransition(Base):
    __tablename__ = "status_transitions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    knowledge_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="CASCADE"), index=True
    )
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(120), default="system")  # system:<stage> | human:<name>
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    item: Mapped[KnowledgeItem] = relationship(back_populates="transitions")


class Conflict(Base):
    __tablename__ = "conflicts"
    __table_args__ = (UniqueConstraint("item_a_id", "item_b_id", name="uq_conflict_pair"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    domain_id: Mapped[str] = mapped_column(ForeignKey("domains.id", ondelete="CASCADE"), index=True)
    item_a_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_items.id", ondelete="CASCADE"))
    item_b_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_items.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), default="OPEN")  # OPEN|RESOLVED
    reason: Mapped[str] = mapped_column(Text, default="")
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    domain_id: Mapped[str | None] = mapped_column(ForeignKey("domains.id", ondelete="SET NULL"), index=True)
    kind: Mapped[str] = mapped_column(String(40))  # pipeline|crawl|extract|embed|discover
    status: Mapped[str] = mapped_column(String(20), default=RunStatus.RUNNING)
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    triggered_by: Mapped[str] = mapped_column(String(120), default="cli")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_pick", "status", "run_at", "priority"),
        UniqueConstraint("idempotency_key", name="uq_jobs_idempotency"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"), index=True)
    type: Mapped[str] = mapped_column(String(60), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.QUEUED)
    priority: Mapped[int] = mapped_column(Integer, default=100)  # lower runs first
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    last_error: Mapped[str | None] = mapped_column(Text)
    locked_by: Mapped[str | None] = mapped_column(String(120))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LLMCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(120))
    purpose: Mapped[str] = mapped_column(String(60), index=True)  # extract|triage|answer|embed
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Setting(Base):
    """Runtime overrides made in the UI (model providers, schedules). Layered over environment settings."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# --------------------------------------------------------------------------- evaluation (§34/§35, req. 15/38)


class EvaluationRun(Base):
    """One execution of a domain's golden question set against the current knowledge base."""

    __tablename__ = "evaluation_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    domain_id: Mapped[str] = mapped_column(ForeignKey("domains.id", ondelete="CASCADE"), index=True)
    dataset_version: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(20), default=RunStatus.RUNNING)  # RUNNING|DONE|FAILED
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # models, prompt/scoring versions, counts
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    baseline_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("evaluation_runs.id", ondelete="SET NULL"))
    regression: Mapped[bool] = mapped_column(Boolean, default=False)
    regression_details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    findings: Mapped[list[Any]] = mapped_column(JSON, default=list)  # failure analysis (req. 16)
    triggered_by: Mapped[str] = mapped_column(String(120), default="cli")
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))

    results: Mapped[list[EvaluationResult]] = relationship(
        back_populates="evaluation_run", cascade="all, delete-orphan", order_by="EvaluationResult.question_id"
    )


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    evaluation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[str] = mapped_column(String(120))
    question: Mapped[str] = mapped_column(Text)
    expected_answer: Mapped[str] = mapped_column(Text, default="")
    answer: Mapped[str] = mapped_column(Text, default="")
    retrieved: Mapped[list[Any]] = mapped_column(JSON, default=list)
    citations: Mapped[list[Any]] = mapped_column(JSON, default=list)
    checks: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # mechanical checks, each {ok, detail}
    judge: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # LLM judge verdict + rationale
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    failure_causes: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # primary class (audit P0.5/P0.6): expected_abstention | coverage_failure | retrieval_failure | uncited_answer
    # | answer_generation_failure | citation_failure | validation_failure; NULL for an ordinary pass
    failure_class: Mapped[str | None] = mapped_column(String(40))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    evaluation_run: Mapped[EvaluationRun] = relationship(back_populates="results")


# --------------------------------------------------------------------------- canonical knowledge snapshots (req. 3–6)


class Snapshot(Base):
    """A Canonical Knowledge Snapshot: reproducible, versioned export of a domain's curated knowledge."""

    __tablename__ = "snapshots"
    __table_args__ = (UniqueConstraint("domain_id", "version", name="uq_snapshot_domain_version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    domain_id: Mapped[str] = mapped_column(ForeignKey("domains.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(10), default="full")  # full | delta
    base_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("snapshots.id", ondelete="SET NULL"))
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    integrity_hash: Mapped[str | None] = mapped_column(String(80))
    object_prefix: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="building")  # building | ready | failed
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(120), default="api")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
