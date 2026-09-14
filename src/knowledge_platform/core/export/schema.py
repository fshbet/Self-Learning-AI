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

SCHEMA_VERSION = "1.3"  # see docs/export-schema/CHANGELOG.md; minor = additive, major = breaking

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
    last_verified_at: str | None = None  # verification event (reached VERIFIED)
    last_source_checked_at: str | None = None  # source re-fetched and still states the claim (schema 1.3)
    last_content_changed_at: str | None = None  # source content changed while the claim persisted
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
    schema_version: str = SCHEMA_VERSION  # kept for 1.0 consumers; same value as export_schema_version
    export_schema_version: str = SCHEMA_VERSION  # the contract this snapshot follows (schema/*.schema.json)
    database_schema_version: str | None = None  # alembic revision of the producing database (informational)
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


# ----------------------------------------------------------------------------- AI Knowledge Source record


class AICitation(BaseModel):
    kind: Literal["document", "human"]
    url: str | None = None
    title: str | None = None
    publisher: str | None = None
    excerpt: str | None = None
    section: str | None = None
    document_version: int | None = None
    publication_date: str | None = None
    retrieved_at: str | None = None
    provided_by: str | None = None
    provenance: str | None = None
    authority: int | None = None


class AIKnowledgeRecord(BaseModel):
    """One line of ``ai/knowledge.jsonl``: a self-contained record for an AI/RAG consumer (see render.ai_record)."""

    id: str
    domain: str
    type: str
    polarity: Literal["positive", "negative"]
    origin: Origin
    provenance: Provenance
    topic: str
    subject: str
    text: str
    statement: str
    code: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    status: str
    historical: bool
    superseded_by_id: str | None = None
    usage: Literal["cite", "caution", "historical"]
    caution_reasons: list[str] = Field(default_factory=list)
    needs_review: bool = False
    review_kind: str | None = None
    review_reason: str | None = None
    needs_revalidation: bool = False
    revalidation_reason: str | None = None
    evidence_status: dict[str, int] = Field(default_factory=dict)
    confidence: float
    verification_level: int
    validated_by: list[str] = Field(default_factory=list)
    product_version: str | None = None
    publication_date: str | None = None
    effective_date: str | None = None
    last_verified_at: str | None = None
    last_source_checked_at: str | None = None
    citations: list[AICitation] = Field(default_factory=list)
    dependencies: list[dict[str, str]] = Field(default_factory=list)
    related_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


# ----------------------------------------------------------------------------- machine-readable schema

# Versioning policy for the export contract (audit P1.1 / P1.2). EXPORT_SCHEMA_VERSION is independent of the
# application version, the database schema (alembic) and every plugin version:
#   * a MINOR bump (1.1 -> 1.2) only ADDS optional fields, files or vocabulary values - consumers of 1.x keep working
#   * a MAJOR bump (1.x -> 2.0) renames/removes fields or files, changes canonical serialisation, hashing or the
#     meaning of a vocabulary value - consumers must be updated
# Every change is recorded in docs/export-schema/CHANGELOG.md; the JSON Schema files shipped under schema/ in each
# snapshot (and mirrored in docs/export-schema/) are generated from the models above.
EXPORT_SCHEMA_VERSION = SCHEMA_VERSION

ITEM_STATUSES = ("EXTRACTED", "CANDIDATE", "SUPPORTED", "VERIFIED", "CONFLICTED", "STALE", "REJECTED", "SUPERSEDED")
SNAPSHOT_STATUSES = ("VERIFIED", "SUPPORTED", "CONFLICTED", "STALE", "SUPERSEDED")  # what a snapshot contains

SCHEMA_FILES: dict[str, type[BaseModel]] = {
    "manifest": Manifest,
    "knowledge": KnowledgeRecord,
    "negative": KnowledgeRecord,  # negative.jsonl holds knowledge records with polarity = negative
    "evidence": EvidenceRecord,
    "sources": SourceRecord,
    "relationships": RelationshipRecord,
    "examples": ExampleRecord,
    "conflicts": ConflictRecord,
    "changelog": ChangelogRecord,
    "ai_knowledge": AIKnowledgeRecord,
}

# which snapshot file each schema validates, and whether the file is one JSON document, a JSON array or JSON lines
SCHEMA_TARGETS: dict[str, tuple[str, str]] = {
    "manifest": ("manifest.json", "json"),
    "knowledge": ("knowledge.jsonl", "jsonl"),
    "negative": ("negative.jsonl", "jsonl"),
    "evidence": ("evidence.jsonl", "jsonl"),
    "sources": ("sources.jsonl", "jsonl"),
    "relationships": ("relationships.jsonl", "jsonl"),
    "examples": ("examples.jsonl", "jsonl"),
    "conflicts": ("conflicts.json", "json-array"),
    "changelog": ("changelog.jsonl", "jsonl"),
    "ai_knowledge": ("ai/knowledge.jsonl", "jsonl"),
}


def json_schema(name: str) -> dict[str, Any]:
    """JSON Schema (draft 2020-12, as emitted by pydantic) for one record family, stamped with the export version."""
    model = SCHEMA_FILES[name]
    schema = model.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"urn:knowledge-platform:export:{EXPORT_SCHEMA_VERSION}:{name}"
    schema["title"] = f"{model.__name__} (export schema {EXPORT_SCHEMA_VERSION})"
    target, layout = SCHEMA_TARGETS[name]
    schema["x-export-schema-version"] = EXPORT_SCHEMA_VERSION
    schema["x-snapshot-file"] = target
    schema["x-layout"] = layout
    return schema


def vocabulary() -> dict[str, Any]:
    """Every controlled vocabulary a consumer may meet, with its meaning (shipped as schema/vocabulary.json)."""
    return {
        "export_schema_version": EXPORT_SCHEMA_VERSION,
        "versioning_policy": {
            "minor": "adds optional fields, files or vocabulary values; consumers of the same major keep working",
            "major": "renames/removes fields or files, or changes serialisation, hashing or the meaning of a value",
            "independent_of": ["platform_version", "database_schema_version", "plugin_version"],
            "changelog": "docs/export-schema/CHANGELOG.md in the platform repository",
        },
        "status": {
            "VERIFIED": "verification level >= 2: authoritative source and/or independent agreement",
            "SUPPORTED": "at least one verified evidence record",
            "CONFLICTED": "an unresolved contradiction with another item exists",
            "STALE": "its evidence no longer appears in the current version of the source",
            "SUPERSEDED": "replaced by a newer item (historical: true); kept for provenance",
            "not_exported": ["EXTRACTED", "CANDIDATE", "REJECTED"],
        },
        "origin": {
            "DIRECT": "stated verbatim by a source (evidence quotes it) or entered by a person",
            "EXPERIMENTALLY_VALIDATED": "DIRECT and passed every applicable domain validator",
            "DERIVED": "concluded from other items (derived_from chain); no verbatim quote of its own",
            "SYNTHESIZED": "combined from several items (derived_from chain); no verbatim quote of its own",
        },
        "provenance": {
            "OFFICIAL": "the class of the most authoritative source is official (vendor / standards body)",
            "EXTERNAL": "reputable third-party source",
            "COMMUNITY": "community source (forums, blogs, Q&A)",
            "USER": "entered by a person as their own knowledge",
            "ORGANIZATION": "entered by a person as an internal standard or policy",
            "DERIVED": "no source of its own; follows the items it was derived from",
        },
        "polarity": {
            "positive": "what is / what works",
            "negative": "what does not work or must be avoided (limitation, warning, anti-pattern)",
        },
        "usage": {
            "cite": "an ordinary current item: safe to answer with, citing it",
            "caution": "flagged for review, awaiting revalidation, CONFLICTED/STALE or carrying contradicting "
            "evidence; surface only with a warning (caution_reasons says why)",
            "historical": "superseded; do not answer with it",
        },
        "review_kind": {
            "falsification": "counter-evidence found on the web; awaiting a reviewer",
            "manual": "a person asked for a review",
            "quality": "a quality rule flagged the item",
        },
        "evidence_type": {
            "extraction": "a verbatim quote located in a crawled document",
            "human": "provided or approved by a person",
            "validator": "result of a domain validator",
            "falsification": "a web passage found while trying to disprove the item (never verified)",
            "derivation": "the recorded reasoning of a DERIVED/SYNTHESIZED item (not a source quote; see derived_from)",
        },
        "evidence_relation": {
            "supports": "supports the claim",
            "contradicts": "contradicts the claim",
            "validates": "validator result",
            "approves": "human approval",
        },
        "relation_type": {
            "depends_on": "the item relies on the target; a change there flags it for revalidation",
            "example_of": "an example illustrating the target",
            "derived_from": "the item was concluded or synthesized from the target",
            "related_to": "informational link",
            "supersedes": "the item replaces the target",
            "contradicts": "the items disagree",
        },
        "propagating_relations": ["depends_on", "example_of", "derived_from"],
        "verification_level": {
            "0": "no source",
            "1": "one source",
            "2": "authoritative source (authority >= 80)",
            "3": "independent agreement (>= 2 sources)",
            "4": "passed a domain validator",
            "5": "approved by a person",
        },
        "files": {t: f"schema/{n}.schema.json" for n, (t, _) in SCHEMA_TARGETS.items()},
    }


def schema_files() -> dict[str, dict[str, Any]]:
    """path -> JSON document for everything shipped under ``schema/`` in a snapshot."""
    out = {f"schema/{name}.schema.json": json_schema(name) for name in SCHEMA_FILES}
    out["schema/vocabulary.json"] = vocabulary()
    return out
