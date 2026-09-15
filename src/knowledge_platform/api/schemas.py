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
    source_class: str = "external"
    relevance: int = 50
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
    mirror_of_source_id: uuid.UUID | None = None
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
    source_class: str = Field(default="", pattern="^(|official|external|community|organization)$")
    relevance: int = Field(default=50, ge=0, le=100)
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
    source_class: str | None = Field(default=None, pattern="^(official|external|community|organization)$")
    relevance: int | None = Field(default=None, ge=0, le=100)
    crawl_frequency_hours: int | None = Field(default=None, ge=1)
    max_depth: int | None = Field(default=None, ge=0, le=6)
    max_pages: int | None = Field(default=None, ge=1, le=5000)
    notes: str | None = None
    mirror_of_source_id: uuid.UUID | None = None  # declare a shared primary source (audit P1.9)


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
    relation: str = "supports"
    retrieved_at: datetime | None = None
    source_version: int | None = None
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
    origin: str = "DIRECT"
    provenance: str = "EXTERNAL"
    polarity: str = "positive"
    status: str
    confidence: float
    verification_level: int
    version: int
    first_discovered_at: datetime
    last_verified_at: datetime | None
    last_source_checked_at: datetime | None = None
    last_content_changed_at: datetime | None = None
    updated_at: datetime
    needs_review: bool = False
    review_kind: str | None = None
    review_reason: str | None = None
    review_flagged_at: datetime | None = None
    evidence_count: int = 0
    source_count: int = 0


class KnowledgeDetail(KnowledgeOut):
    details: dict[str, Any] = Field(default_factory=dict)
    effective_date: str | None = None
    needs_revalidation: bool = False
    revalidation_reason: str | None = None
    validator_versions: dict[str, Any] = Field(default_factory=dict)
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
    relations: dict[str, list[dict[str, Any]]] = Field(default_factory=lambda: {"outgoing": [], "incoming": []})


class RelationCreate(BaseModel):
    to_item_id: uuid.UUID
    relation_type: str = Field(
        pattern="^(depends_on|example_of|derived_from|related_to|supersedes|contradicts|compatible_under)$"
    )


class KnowledgeCreate(BaseModel):
    """Human-authored knowledge (USER / ORGANIZATION provenance)."""

    domain: str
    statement: str = Field(min_length=10, max_length=2000)
    subject: str = Field(min_length=1, max_length=300)
    predicate: str = Field(min_length=1, max_length=200)
    object: str = Field(min_length=1, max_length=2000)
    knowledge_type: str = "fact"
    explanation: str = ""
    topic: str = ""
    tags: list[str] = Field(default_factory=list)
    code: str | None = None
    product_version: str | None = None
    provenance: str = Field(default="USER", pattern="^(USER|ORGANIZATION)$")
    polarity: str | None = Field(default=None, pattern="^(positive|negative)$")
    details: dict[str, str] = Field(default_factory=dict)
    evidence_text: str = ""
    evidence_url: str | None = None
    provided_by: str = "user"
    authority: int = Field(default=60, ge=0, le=100)
    # derived knowledge (audit P1.3): the premises and the reasoning; no quote is fabricated
    origin: str = Field(default="DIRECT", pattern="^(DIRECT|DERIVED|SYNTHESIZED)$")
    derived_from: list[uuid.UUID] = Field(default_factory=list)
    rationale: str = ""


class ReviewRequest(BaseModel):
    # dismiss = clear the review flag only; supersede = this item is replaced by `superseded_by` (P2.1)
    action: str = Field(pattern="^(approve|reject|stale|reopen|dismiss|supersede)$")
    reason: str = ""
    reviewer: str = "reviewer"
    superseded_by: uuid.UUID | None = None


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
    # ADR 0006: every ranking contribution and the human-readable reasons ("why did this rank above that?")
    signals: dict[str, float] = Field(default_factory=dict)
    explanation: list[str] = Field(default_factory=list)


class AskRequest(BaseModel):
    domain: str
    question: str = Field(min_length=3, max_length=2000)
    limit: int | None = Field(default=None, ge=1, le=20, description="context size; the mode's default when unset")
    mode: str = Field(default="p3", pattern="^(p2|p3-retrieval|p3)$")


class AskResponse(BaseModel):
    question: str
    answer: str
    citations: list[dict[str, Any]]
    retrieved: list[dict[str, Any]]
    insufficient: bool
    no_results: bool = False
    mode: str = "p3"
    plan: dict[str, Any] | None = None
    completeness: dict[str, Any] | None = None
    regeneration: dict[str, Any] | None = None
    retrieval: dict[str, Any] | None = None
    timings_ms: dict[str, int] = Field(default_factory=dict)
    llm_calls: int = 0


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


# ----------------------------------------------------------------------------- evaluation


class EvaluationResultOut(ORM):
    id: uuid.UUID
    question_id: str
    question: str
    expected_answer: str
    answer: str
    retrieved: list[Any]
    citations: list[Any]
    checks: dict[str, Any]
    judge: dict[str, Any]
    passed: bool
    failure_causes: list[Any]
    failure_class: str | None = None
    latency_ms: int


class EvaluationRunOut(ORM):
    id: uuid.UUID
    domain_id: str
    dataset_version: str
    status: str
    config: dict[str, Any]
    metrics: dict[str, Any]
    baseline_run_id: uuid.UUID | None
    regression: bool
    regression_details: dict[str, Any]
    findings: list[Any]
    triggered_by: str
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class EvaluationRunDetail(EvaluationRunOut):
    results: list[EvaluationResultOut]


class EvaluationCreate(BaseModel):
    domain: str
    question_ids: list[str] | None = None
    wait: bool = False  # run inline instead of queueing (CLI / tests)


class EvaluationCompare(BaseModel):
    a: EvaluationRunOut
    b: EvaluationRunOut
    metric_deltas: dict[str, Any]
    question_changes: list[dict[str, Any]]


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
    needs_revalidation: int = 0
    needs_review: int = 0
    jobs: dict[str, int]
    llm: dict[str, Any]
    topics: list[dict[str, Any]]
    recent_runs: list[RunOut]
    evaluation: dict[str, Any] | None = None  # latest run: metrics, regression, when
    ops: dict[str, Any] = Field(default_factory=dict)  # queue health, model latency, storage, schedule


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
