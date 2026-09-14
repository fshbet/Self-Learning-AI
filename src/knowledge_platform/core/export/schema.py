"""Canonical Knowledge Snapshot record schema (req. 3–10, 26–28, 40).

Terminology (req. 40):
* Source of Truth        – the original authoritative source (a URL / document).
* Evidence               – material collected from the source that supports or contradicts a claim.
* Curated Knowledge      – the structured conclusion the platform produced from evidence.
* Canonical Knowledge Snapshot – a reproducible, versioned export of curated knowledge (this schema).
* AI Knowledge Source    – the representation of a snapshot optimised for AI/RAG consumption (ai/ folder).

The snapshot is *not* the source of truth; every record keeps enough provenance to point back to it.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.1"  # 1.1: trust state (review / revalidation flags, evidence_status) on knowledge records

Origin = Literal["DIRECT", "DERIVED", "SYNTHESIZED", "EXPERIMENTALLY_VALIDATED"]
Provenance = Literal["OFFICIAL", "EXTERNAL", "COMMUNITY", "USER", "ORGANIZATION", "DERIVED"]


class KnowledgeRecord(BaseModel):
    id: str
    domain: str
    knowledge_type: str
    origin: Origin = "DIRECT"
    provenance: Provenance = "EXTERNAL"
    polarity: Literal["positive", "negative"] = "positive"
    subject: str
    predicate: str
    object: str
    statement: str
    explanation: str = ""
    topic: str = ""
    tags: list[str] = Field(default_factory=list)
    code: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    product_version: str | None = None
    language: str = "en"
    publication_date: str | None = None
    effective_date: str | None = None
    valid_until: str | None = None
    status: str
    historical: bool = False  # SUPERSEDED items kept for provenance
    confidence: float
    verification_level: int
    quality_factors: dict[str, Any] = Field(default_factory=dict)
    scoring_rule_version: str = ""
    version: int = 1
    previous_version_id: str | None = None
    superseded_by_id: str | None = None
    first_discovered_at: str
    last_verified_at: str | None = None
    extraction: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    dependencies: list[dict[str, str]] = Field(default_factory=list)  # {"relation": ..., "item_id": ...}
    # trust state (schema 1.1, audit P0.3): a consumer must be able to see that an item needs caution
    needs_revalidation: bool = False  # a dependency changed; revalidation pending
    revalidation_reason: str | None = None
    needs_review: bool = False  # falsification / manual / quality concern awaiting a reviewer
    review_kind: str | None = None
    review_reason: str | None = None
    review_flagged_at: str | None = None
    evidence_status: dict[str, int] = Field(default_factory=dict)  # verified / unverified / contradicting counts
    content_hash: str


class EvidenceRecord(BaseModel):
    id: str
    knowledge_item_id: str
    evidence_type: str  # extraction | validator | human
    relation: str = "supports"  # supports | contradicts | validates | approves
    source_id: str | None = None
    source_url: str | None = None
    source_name: str | None = None
    publisher: str | None = None
    source_authority: int | None = None
    source_origin: str | None = None
    document_id: str | None = None
    document_url: str | None = None
    document_title: str | None = None
    document_version: int | None = None
    document_hash: str | None = None
    publication_date: str | None = None
    retrieved_at: str | None = None
    section: list[str] = Field(default_factory=list)
    locator: dict[str, Any] = Field(default_factory=dict)
    excerpt: str
    verified: bool
    details: dict[str, Any] = Field(default_factory=dict)


class SourceRecord(BaseModel):
    id: str
    key: str
    name: str
    url: str
    publisher: str = ""
    source_type: str = "web"
    origin: str = "plugin"
    authority: int = 50
    access_type: str = "public"
    license: str = ""
    permissions: dict[str, Any] = Field(default_factory=dict)
    crawl_frequency_hours: int = 168
    status: str = "ACTIVE"
    enabled: bool = True
    last_checked_at: str | None = None
    last_changed_at: str | None = None
    document_count: int = 0


class RelationshipRecord(BaseModel):
    id: str
    from_item_id: str
    to_item_id: str
    relation_type: str
    origin: str = "system"
    details: dict[str, Any] = Field(default_factory=dict)


class ExampleRecord(BaseModel):
    id: str
    subject: str
    topic: str = ""
    statement: str
    explanation: str = ""
    code: str | None = None
    expected_behavior: str | None = None
    expected_result: str | None = None
    common_mistake: str | None = None
    validation_method: str | None = None
    validation_results: list[dict[str, Any]] = Field(default_factory=list)
    supporting_item_ids: list[str] = Field(default_factory=list)
    status: str
    confidence: float
    source_ids: list[str] = Field(default_factory=list)


class ConflictRecord(BaseModel):
    id: str
    item_a_id: str
    item_b_id: str
    status: str
    reason: str
    resolution: str | None = None
    resolved_by: str | None = None
    created_at: str
    resolved_at: str | None = None


class ChangelogRecord(BaseModel):
    id: str
    knowledge_item_id: str
    from_status: str | None
    to_status: str
    reason: str = ""
    actor: str = "system"
    at: str


class Manifest(BaseModel):
    schema_version: str = SCHEMA_VERSION
    snapshot_id: str
    snapshot_version: int
    kind: Literal["full", "delta"] = "full"
    base_snapshot_id: str | None = None
    base_integrity_hash: str | None = None
    domain: str
    plugin_name: str
    plugin_version: str
    plugin_api_version: str
    created_at: str
    platform_version: str
    generation: dict[str, Any] = Field(
        default_factory=dict
    )  # extractor/judge/chunker/scoring/embedding versions, models
    counts: dict[str, int] = Field(default_factory=dict)
    status_counts: dict[str, int] = Field(default_factory=dict)
    files: dict[str, dict[str, Any]] = Field(default_factory=dict)  # path -> {sha256, bytes, records}
    integrity_hash: str = ""  # sha256 over sorted "path:sha256" lines; the manifest itself is excluded
    gate: dict[str, Any] = Field(default_factory=dict)  # export quality gate results
    terminology: dict[str, str] = Field(
        default_factory=lambda: {
            "source_of_truth": "the original authoritative source referenced by evidence records",
            "evidence": "material collected from a source that supports or contradicts a claim",
            "curated_knowledge": "the structured conclusion produced by the platform from evidence",
            "canonical_knowledge_snapshot": "this reproducible, versioned export of curated knowledge",
            "ai_knowledge_source": "the ai/ representation of this snapshot optimised for AI/RAG consumption",
        }
    )
