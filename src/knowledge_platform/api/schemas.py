"""Pydantic response/request models for the HTTP API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


# ----------------------------------------------------------------------------- domains


class DomainOut(ORM):
    id: str
    name: str
    description: str
    version: str
    enabled: bool
    synced_at: datetime | None = None
    manifest: dict[str, Any] = Field(default_factory=dict)
    loaded: bool = True
    load_error: str | None = None
    keywords: list[KeywordOut] = Field(default_factory=list)
    user_sources: int = 0


# ----------------------------------------------------------------------------- sources


class SourceOut(ORM):
    id: uuid.UUID
    domain_id: str
    key: str
    origin: str = "plugin"
    name: str
    url: str
    publisher: str
    source_type: str
    authority: int
    access_type: str
    license: str
    permissions: dict[str, Any]
    crawl_frequency_hours: int
    max_depth: int
    max_pages: int
    status: str
    enabled: bool
    last_checked_at: datetime | None
    last_changed_at: datetime | None
    last_error: str | None
    robots_info: dict[str, Any]
    notes: str
    document_count: int = 0


class SourceCreate(BaseModel):
    """A user-defined source: any URL you want the platform to collect from."""

    domain: str
    url: str = Field(pattern=r"^https?://", max_length=2000)
    name: str = Field(default="", max_length=200)
    publisher: str = Field(default="", max_length=200)
    authority: int = Field(default=60, ge=0, le=100)
    source_type: str = Field(default="web", pattern="^(web|pdf|api|git|video)$")
    license: str = Field(default="", max_length=200)
    permissions: dict[str, bool] = Field(
        default_factory=lambda: {"read": True, "store": True, "process": True, "train": False, "redistribute": False}
    )
    crawl_frequency_hours: int = Field(default=168, ge=1)
    max_depth: int = Field(default=1, ge=0, le=6)
    max_pages: int = Field(default=20, ge=1, le=5000)
    allow_patterns: list[str] = Field(default_factory=list)
    deny_patterns: list[str] = Field(default_factory=list)
    notes: str = ""
    crawl_now: bool = False


class KeywordOut(ORM):
    id: uuid.UUID
    domain_id: str
    keyword: str
    enabled: bool
    created_by: str
    created_at: datetime


class KeywordCreate(BaseModel):
    keyword: str = Field(min_length=2, max_length=300)


class SourcePatch(BaseModel):
    enabled: bool | None = None
    status: str | None = None
    authority: int | None = Field(default=None, ge=0, le=100)
    crawl_frequency_hours: int | None = Field(default=None, ge=1)
    max_depth: int | None = Field(default=None, ge=0, le=6)
    max_pages: int | None = Field(default=None, ge=1, le=5000)
    notes: str | None = None


# ----------------------------------------------------------------------------- documents


class DocumentOut(ORM):
    id: uuid.UUID
    domain_id: str
    source_id: uuid.UUID
    url: str
    title: str
    content_hash: str
    language: str
    published_at: str | None
    fetched_at: datetime
    extracted_at: datetime | None
    version: int
    depth: int
    byte_size: int
    status: str
    error: str | None
    item_count: int = 0
    source_name: str | None = None


class DocumentDetail(DocumentOut):
    text: str
    raw_object_key: str
    http_etag: str | None
    http_last_modified: str | None
    meta: dict[str, Any]


# ----------------------------------------------------------------------------- knowledge


class EvidenceOut(ORM):
    id: uuid.UUID
    document_id: uuid.UUID | None
    source_id: uuid.UUID | None
    evidence_type: str
    excerpt: str
    locator: dict[str, Any]
    document_hash: str | None
    url: str | None
    verified: bool
    weight: float
    details: dict[str, Any]
    created_at: datetime
    source_name: str | None = None
    document_title: str | None = None


class TransitionOut(ORM):
    id: uuid.UUID
    from_status: str | None
    to_status: str
    reason: str
    actor: str
    created_at: datetime


class KnowledgeOut(ORM):
    id: uuid.UUID
    domain_id: str
    knowledge_type: str
    subject: str
    predicate: str
    object: str
    statement: str
    explanation: str
    topic: str
    tags: list[Any]
    code: str | None
    product_version: str | None
    language: str
    publication_date: str | None
    status: str
    confidence: float
    verification_level: int
    version: int
    first_discovered_at: datetime
    last_verified_at: datetime | None
    updated_at: datetime
    evidence_count: int = 0
    source_count: int = 0


class KnowledgeDetail(KnowledgeOut):
    quality_factors: dict[str, Any]
    scoring_rule_version: str
    content_hash: str
    extraction: dict[str, Any]
    embedding_model: str | None
    previous_version_id: uuid.UUID | None
    superseded_by_id: uuid.UUID | None
    duplicate_of_id: uuid.UUID | None
    run_id: uuid.UUID | None
    evidence: list[EvidenceOut]
    transitions: list[TransitionOut]
    conflicts: list[ConflictOut] = Field(default_factory=list)
    duplicates: list[KnowledgeOut] = Field(default_factory=list)


class ReviewRequest(BaseModel):
    action: str = Field(pattern="^(approve|reject|stale|reopen)$")
    reason: str = ""
    reviewer: str = "reviewer"


# ----------------------------------------------------------------------------- conflicts


class ConflictOut(ORM):
    id: uuid.UUID
    domain_id: str
    item_a_id: uuid.UUID
    item_b_id: uuid.UUID
    status: str
    reason: str
    resolution: str | None
    resolved_by: str | None
    created_at: datetime
    resolved_at: datetime | None
    item_a: KnowledgeOut | None = None
    item_b: KnowledgeOut | None = None


class ConflictResolve(BaseModel):
    keep: str = Field(pattern="^(a|b|both|neither)$")
    resolution: str = ""
    reviewer: str = "reviewer"


# ----------------------------------------------------------------------------- search / ask


class SearchHitOut(BaseModel):
    item: KnowledgeOut
    score: float
    vec_rank: int | None
    lex_rank: int | None
    similarity: float | None
    evidence: list[EvidenceOut] = Field(default_factory=list)


class AskRequest(BaseModel):
    domain: str
    question: str = Field(min_length=3, max_length=2000)
    limit: int = Field(default=8, ge=1, le=20)


class AskResponse(BaseModel):
    question: str
    answer: str
    citations: list[dict[str, Any]]
    retrieved: list[dict[str, Any]]
    insufficient: bool


# ----------------------------------------------------------------------------- runs / jobs


class RunOut(ORM):
    id: uuid.UUID
    domain_id: str | None
    kind: str
    status: str
    stats: dict[str, Any]
    started_at: datetime
    finished_at: datetime | None
    triggered_by: str
    jobs_total: int = 0
    jobs_done: int = 0
    jobs_failed: int = 0
    jobs_running: int = 0
    jobs_queued: int = 0


class RunCreate(BaseModel):
    domain: str
    kind: str = Field(default="pipeline", pattern="^(pipeline|extract|discover)$")
    source_keys: list[str] | None = None
    max_pages: int | None = Field(default=None, ge=1, le=5000)


class JobOut(ORM):
    id: uuid.UUID
    run_id: uuid.UUID | None
    type: str
    payload: dict[str, Any]
    status: str
    priority: int
    attempts: int
    max_attempts: int
    last_error: str | None
    result: dict[str, Any]
    created_at: datetime
    locked_at: datetime | None
    finished_at: datetime | None


# ----------------------------------------------------------------------------- stats


class StatsOut(BaseModel):
    domain: str | None
    sources: dict[str, int]
    documents: dict[str, int]
    knowledge: dict[str, int]
    knowledge_total: int
    verified_ratio: float
    avg_confidence: float
    conflicts_open: int
    jobs: dict[str, int]
    llm: dict[str, Any]
    topics: list[dict[str, Any]]
    recent_runs: list[RunOut]


class HealthOut(BaseModel):
    ok: bool
    database: bool
    llm_provider: str
    llm_models_configured: dict[str, str]
    llm_models_available: list[str]
    embedding: str
    object_store: str
    search: str | None
    domains: list[str]
    plugin_errors: dict[str, str]
    version: str


DomainOut.model_rebuild()
