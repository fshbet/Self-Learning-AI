"""Document → knowledge pipeline stage.

    extract → verify quotes → dedup (exact, near) → validators → score → lifecycle → conflicts → embed

Everything here is idempotent per (document, content_hash): re-running on an
unchanged document adds no new items, because exact/near duplicates fold into
existing items as additional evidence.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import Document, DocumentStatus, Evidence, ItemStatus, KnowledgeItem, Source, utcnow
from .extraction.extractor import ExtractedItem, extract_from_text
from .plugins.base import DomainPlugin
from .quality.dedup import find_exact, find_near
from .quality.scoring import SCORING_RULE_VERSION, score
from .retrieval.embeddings import embed_items
from .verification.conflicts import detect_conflicts
from .versioning.lifecycle import advance_to, status_for_level, transition, verification_level

log = logging.getLogger(__name__)


@dataclass
class IngestStats:
    items_created: int = 0
    items_merged: int = 0  # folded into existing items as extra evidence
    items_near_duplicate: int = 0
    items_stale: int = 0
    conflicts: int = 0
    validators_run: int = 0
    extraction: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


# ----------------------------------------------------------------------------- helpers


def item_as_dict(item: KnowledgeItem) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "knowledge_type": item.knowledge_type,
        "subject": item.subject,
        "predicate": item.predicate,
        "object": item.object,
        "statement": item.statement,
        "explanation": item.explanation,
        "topic": item.topic,
        "tags": item.tags,
        "code": item.code,
        "product_version": item.product_version,
    }


def _evidence_summary(session: Session, item: KnowledgeItem) -> tuple[int, int, bool, bool, list[dict[str, Any]]]:
    """distinct sources, max authority, validator passed, human approved, validator results."""
    source_ids = {e.source_id for e in item.evidence if e.source_id and e.evidence_type == "extraction" and e.verified}
    authorities = []
    if source_ids:
        authorities = list(session.execute(select(Source.authority).where(Source.id.in_(source_ids))).scalars())
    validator_results = [
        e.details | {"passed": e.details.get("passed", False)} for e in item.evidence if e.evidence_type == "validator"
    ]
    validator_passed = bool(validator_results) and all(v["passed"] for v in validator_results)
    human_approved = any(e.evidence_type == "human" and e.details.get("approved") for e in item.evidence)
    return len(source_ids), max(authorities, default=0), validator_passed, human_approved, validator_results


def rescore(session: Session, item: KnowledgeItem, plugin: DomainPlugin, *, actor: str = "system:score") -> None:
    distinct, max_auth, validator_passed, human_approved, validator_results = _evidence_summary(session, item)
    evidence_verified = any(e.verified for e in item.evidence if e.evidence_type == "extraction")
    conf, factors = score(
        source_authority=max_auth,
        evidence_verified=evidence_verified,
        distinct_sources=distinct,
        statement=item.statement,
        code=item.code,
        topic_matched=bool(item.topic) and item.topic in set(plugin.taxonomy_paths()),
        validator_results=validator_results,
    )
    item.confidence = conf
    item.quality_factors = factors
    item.scoring_rule_version = SCORING_RULE_VERSION
    item.verification_level = verification_level(
        distinct_sources=distinct,
        max_authority=max_auth,
        validator_passed=validator_passed,
        human_approved=human_approved,
    )
    if ItemStatus(item.status) in (ItemStatus.REJECTED, ItemStatus.SUPERSEDED, ItemStatus.CONFLICTED):
        return
    target = status_for_level(item.verification_level, has_conflict=False)
    if ItemStatus(item.status) == ItemStatus.STALE and evidence_verified:
        transition(session, item, target, reason="re-verified against current sources", actor=actor)
        return
    if ItemStatus(item.status) != ItemStatus.STALE:
        advance_to(session, item, target, reason=f"verification level {item.verification_level}", actor=actor)


def _add_evidence(item: KnowledgeItem, doc: Document, ex: ExtractedItem) -> Evidence:
    ev = Evidence(
        knowledge_item_id=item.id,
        document_id=doc.id,
        source_id=doc.source_id,
        evidence_type="extraction",
        excerpt=ex.evidence_quote,
        locator={
            "chunk_index": ex.chunk.index,
            "heading_path": ex.chunk.heading_path,
            "chunk_start": ex.chunk.start,
            "start": (ex.chunk.start + ex.quote_start) if ex.quote_start is not None else None,
            "end": (ex.chunk.start + ex.quote_end) if ex.quote_end is not None else None,
        },
        document_hash=doc.content_hash,
        url=doc.url,
        verified=ex.quote_verified,
    )
    item.evidence.append(ev)
    return ev


def _has_evidence_from_doc(item: KnowledgeItem, doc: Document) -> bool:
    return any(e.document_id == doc.id and e.document_hash == doc.content_hash for e in item.evidence)


def run_validators(session: Session, item: KnowledgeItem, plugin: DomainPlugin) -> int:
    ran = 0
    payload = item_as_dict(item)
    for v in plugin.validators():
        try:
            if not v.applies_to(payload):
                continue
            result = v.validate(payload)
        except Exception as exc:  # a broken validator must not break ingestion
            log.exception("validator %s failed on %s: %s", v.name, item.id, exc)
            continue
        ran += 1
        # replace an older result from the same validator version
        item.evidence[:] = [
            e for e in item.evidence if not (e.evidence_type == "validator" and e.details.get("validator") == v.name)
        ]
        item.evidence.append(
            Evidence(
                knowledge_item_id=item.id,
                evidence_type="validator",
                excerpt=result.message or f"{v.name} {'passed' if result.passed else 'failed'}",
                verified=True,
                details={"validator": v.name, "version": v.version, "passed": result.passed, **result.details},
            )
        )
    return ran


# ----------------------------------------------------------------------------- stale detection


def mark_stale_items(session: Session, doc: Document, *, actor: str = "system:change-detection") -> int:
    """After a document changed, items whose quotes vanished from it lose that evidence (§24)."""
    stale = 0
    items = (
        session.execute(
            select(KnowledgeItem)
            .where(
                KnowledgeItem.id.in_(
                    select(Evidence.knowledge_item_id).where(
                        Evidence.document_id == doc.id, Evidence.document_hash != doc.content_hash
                    )
                )
            )
            .options(selectinload(KnowledgeItem.evidence))
        )
        .scalars()
        .all()
    )
    for item in items:
        for ev in item.evidence:
            if ev.document_id == doc.id and ev.document_hash != doc.content_hash and ev.evidence_type == "extraction":
                still_there = ev.excerpt and ev.excerpt in doc.text
                ev.verified = bool(still_there)
                if still_there:
                    ev.document_hash = doc.content_hash
        if not any(e.verified for e in item.evidence if e.evidence_type == "extraction"):
            if ItemStatus(item.status) in (ItemStatus.SUPPORTED, ItemStatus.VERIFIED):
                transition(
                    session, item, ItemStatus.STALE, reason=f"evidence no longer found in {doc.url}", actor=actor
                )
                stale += 1
    session.flush()
    return stale


# ----------------------------------------------------------------------------- main stage


def ingest_document(
    session: Session,
    doc: Document,
    plugin: DomainPlugin,
    *,
    run_id: uuid.UUID | None = None,
    max_chunks: int | None = None,
) -> IngestStats:
    stats = IngestStats()
    stats.items_stale = mark_stale_items(session, doc)

    extracted, ext_stats = extract_from_text(
        plugin, text=doc.text, title=doc.title, url=doc.url, session=session, run_id=run_id, max_chunks=max_chunks
    )
    stats.extraction = ext_stats

    new_items: list[KnowledgeItem] = []
    touched: dict[uuid.UUID, KnowledgeItem] = {}

    for ex in extracted:
        existing = find_exact(session, doc.domain_id, ex.content_hash)
        if existing is not None:
            if not _has_evidence_from_doc(existing, doc):
                _add_evidence(existing, doc, ex)
                stats.items_merged += 1
            touched[existing.id] = existing
            continue
        item = KnowledgeItem(
            domain_id=doc.domain_id,
            knowledge_type=ex.knowledge_type,
            subject=ex.subject,
            predicate=ex.predicate,
            object=ex.object,
            statement=ex.statement,
            explanation=ex.explanation,
            topic=ex.topic,
            tags=ex.tags,
            code=ex.code,
            product_version=ex.product_version,
            language=doc.language,
            publication_date=doc.published_at,
            status=ItemStatus.EXTRACTED,
            content_hash=ex.content_hash,
            extraction=ex.extraction,
            run_id=run_id,
        )
        session.add(item)
        session.flush()
        _add_evidence(item, doc, ex)
        new_items.append(item)

    # Embed first so that near-duplicate detection can use vectors.
    embed_items(session, new_items)

    survivors: list[KnowledgeItem] = []
    for item in new_items:
        near = find_near(session, doc.domain_id, item)
        if near is not None:
            other, dist = near
            item.duplicate_of_id = other.id
            transition(
                session,
                item,
                ItemStatus.REJECTED,
                reason=f"near-duplicate of {other.id} (cosine distance {dist:.3f})",
                actor="system:dedup",
            )
            stats.items_near_duplicate += 1
            if not _has_evidence_from_doc(other, doc):
                ev = item.evidence[0]
                other.evidence.append(
                    Evidence(
                        knowledge_item_id=other.id,
                        document_id=doc.id,
                        source_id=doc.source_id,
                        evidence_type="extraction",
                        excerpt=ev.excerpt,
                        locator=ev.locator,
                        document_hash=doc.content_hash,
                        url=doc.url,
                        verified=ev.verified,
                        details={"via_duplicate": str(item.id)},
                    )
                )
                stats.items_merged += 1
            touched[other.id] = other
            continue
        survivors.append(item)

    for item in survivors:
        stats.validators_run += run_validators(session, item, plugin)
        rescore(session, item, plugin)
        stats.conflicts += len(detect_conflicts(session, item, domain_name=plugin.name, run_id=run_id))
        stats.items_created += 1

    for item in touched.values():
        rescore(session, item, plugin)

    doc.status = DocumentStatus.EXTRACTED
    doc.extracted_at = utcnow()
    doc.error = None
    session.flush()
    return stats
