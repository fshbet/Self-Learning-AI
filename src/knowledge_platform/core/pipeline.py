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

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from ..models import Conflict, Document, DocumentStatus, Evidence, ItemStatus, KnowledgeItem, Source, utcnow
from .extraction.extractor import ExtractedItem, extract_from_text
from .extraction.prompts import PROMPT_VERSION
from .plugins.base import DomainPlugin
from .quality.dedup import find_exact, find_near
from .quality.provenance import derive_provenance
from .quality.scoring import SCORING_RULE_VERSION, score
from .retrieval.embeddings import embed_items
from .verification.conflicts import detect_conflicts
from .versioning.dependencies import derive_relations
from .versioning.lifecycle import advance_to, status_for_level, transition, verification_level
from .versioning.supersede import link_superseded

log = logging.getLogger(__name__)


@dataclass
class IngestStats:
    items_created: int = 0
    items_merged: int = 0  # folded into existing items as extra evidence
    items_near_duplicate: int = 0
    items_stale: int = 0
    items_superseded: int = 0  # stale items linked to the new version that replaced them (P2.1)
    conflicts: int = 0
    relations: int = 0
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
        source_ids = independent_source_ids(session, item, source_ids)
    # user/organization-provided knowledge (req. 9): the human evidence carries its own declared authority
    provided = [e for e in item.evidence if e.evidence_type == "human" and e.details.get("provided")]
    distinct_keys = {str(s) for s in source_ids} | {f"human:{e.details.get('provided_by', '?')}" for e in provided}
    authorities += [int(e.details.get("authority", 60)) for e in provided]
    validator_results = [
        e.details | {"passed": e.details.get("passed", False)} for e in item.evidence if e.evidence_type == "validator"
    ]
    validator_passed = bool(validator_results) and all(v["passed"] for v in validator_results)
    human_approved = any(e.evidence_type == "human" and e.details.get("approved") for e in item.evidence)
    return len(distinct_keys), max(authorities, default=0), validator_passed, human_approved, validator_results


def _primary_source(session: Session, source_id: uuid.UUID, cache: dict[uuid.UUID, uuid.UUID]) -> uuid.UUID:
    """Follow declared mirror_of chains to the primary source (cycle-safe)."""
    seen: list[uuid.UUID] = []
    current = source_id
    while current not in cache and current not in seen:
        seen.append(current)
        parent = session.execute(select(Source.mirror_of_source_id).where(Source.id == current)).scalar_one_or_none()
        if not parent:
            break
        current = parent
    root = cache.get(current, current)
    for s in seen:
        cache[s] = root
    return root


def independent_source_ids(session: Session, item: KnowledgeItem, source_ids: set[uuid.UUID]) -> set[uuid.UUID]:
    """Sources that independently confirm the item (audit P1.9). A copy is not a second confirmation:

    * a document that mirrors another document (same text / fingerprint / declared canonical URL) counts for the
      canonical document's source;
    * a source declared as ``mirror_of`` another source counts as that primary (chains followed);
    * a source that only contributes verbatim excerpts another source already contributed (syndicated fragments)
      is not independent either.
    """
    evidence = [e for e in item.evidence if e.evidence_type == "extraction" and e.verified and e.source_id]
    if not evidence:
        return source_ids
    doc_ids = {e.document_id for e in evidence if e.document_id}
    canonical_of: dict[uuid.UUID, uuid.UUID] = {}
    if doc_ids:
        mirrors = session.execute(
            select(Document.id, Document.canonical_document_id).where(
                Document.id.in_(doc_ids), Document.canonical_document_id.is_not(None)
            )
        ).all()
        if mirrors:
            canonical_sources = dict(
                session.execute(
                    select(Document.id, Document.source_id).where(Document.id.in_([c for _, c in mirrors]))
                ).all()
            )
            canonical_of = {d: canonical_sources[c] for d, c in mirrors if c in canonical_sources}
    cache: dict[uuid.UUID, uuid.UUID] = {}
    seen_excerpts: dict[str, uuid.UUID] = {}
    out: set[uuid.UUID] = set()
    # oldest evidence first: the source that stated it first owns the excerpt
    for e in sorted(evidence, key=lambda x: (x.retrieved_at or x.created_at or utcnow(), str(x.id))):
        src = canonical_of.get(e.document_id, e.source_id) if e.document_id else e.source_id
        src = _primary_source(session, src, cache)
        key = " ".join((e.excerpt or "").lower().split())
        owner = seen_excerpts.setdefault(key, src)
        if owner != src:
            continue  # the same words, said first by another source: not independent
        out.add(src)
    return out or source_ids


def has_open_conflict(session: Session, item: KnowledgeItem) -> bool:
    return (
        session.execute(
            select(Conflict.id)
            .where(or_(Conflict.item_a_id == item.id, Conflict.item_b_id == item.id), Conflict.status == "OPEN")
            .limit(1)
        ).scalar_one_or_none()
        is not None
    )


def _rescore_derived(session: Session, item: KnowledgeItem, *, actor: str) -> None:
    """DERIVED / SYNTHESIZED items (audit P1.3) have no source of their own: confidence and level follow their
    premises (derived_from relations) — never more certain than the weakest one, SUPPORTED at most until a person
    approves, and demoted to CANDIDATE while any premise is not live."""
    from .versioning.dependencies import dependencies_of

    premises = [k for r, k in dependencies_of(session, item.id) if r.relation_type == "derived_from"]
    # a premise that was superseded and whose successor was carried over is represented by the successor
    premise_ids = {p.id for p in premises}
    premises = [p for p in premises if not (p.superseded_by_id and p.superseded_by_id in premise_ids)]
    live = [p for p in premises if ItemStatus(p.status) in (ItemStatus.SUPPORTED, ItemStatus.VERIFIED)]
    all_live = bool(premises) and len(live) == len(premises)
    human_approved = any(e.evidence_type == "human" and e.details.get("approved") for e in item.evidence)
    weakest = min((float(p.confidence) for p in premises), default=0.0)
    item.confidence = round(weakest * DERIVATION_DISCOUNT, 4) if all_live else round(weakest * 0.5, 4)
    item.quality_factors = {
        "derivation": {
            "origin": item.origin,
            "premises": len(premises),
            "premises_live": len(live),
            "weakest_premise_confidence": weakest,
            "discount": DERIVATION_DISCOUNT,
        },
        "rule_version": SCORING_RULE_VERSION,
    }
    item.scoring_rule_version = SCORING_RULE_VERSION
    level = min((int(p.verification_level) for p in premises), default=0) if all_live else 0
    item.verification_level = 5 if human_approved else min(level, 3)
    if ItemStatus(item.status) in (ItemStatus.REJECTED, ItemStatus.SUPERSEDED, ItemStatus.CONFLICTED):
        return
    if human_approved:
        target = ItemStatus.VERIFIED
    elif all_live and item.verification_level >= 1:
        target = ItemStatus.SUPPORTED
    else:
        target = ItemStatus.CANDIDATE
    current = ItemStatus(item.status)
    if current == ItemStatus.STALE:
        if all_live and target != ItemStatus.CANDIDATE:
            transition(session, item, target, reason="premises live again", actor=actor)
        return
    if target == ItemStatus.CANDIDATE and current in (ItemStatus.SUPPORTED, ItemStatus.VERIFIED):
        transition(session, item, ItemStatus.STALE, reason="a premise is no longer live", actor=actor)
        return
    advance_to(session, item, target, reason=f"derived: premises live={all_live}, level {level}", actor=actor)


DERIVATION_DISCOUNT = 0.9


def rescore(session: Session, item: KnowledgeItem, plugin: DomainPlugin, *, actor: str = "system:score") -> None:
    if item.origin in ("DERIVED", "SYNTHESIZED"):
        _rescore_derived(session, item, actor=actor)
        return
    distinct, max_auth, validator_passed, human_approved, validator_results = _evidence_summary(session, item)
    evidence_verified = any(
        e.verified for e in item.evidence if e.evidence_type == "extraction" or e.details.get("provided")
    )
    conf, factors = score(
        source_authority=max_auth,
        evidence_verified=evidence_verified,
        distinct_sources=distinct,
        statement=item.statement,
        code=item.code,
        topic_matched=bool(item.topic) and item.topic in set(plugin.taxonomy_paths()),
        validator_results=validator_results,
        # freshness: the source was last seen to still state it; then the verification event; then discovery
        last_verified_at=item.last_source_checked_at or item.last_verified_at or item.first_discovered_at,
        is_stale=ItemStatus(item.status) == ItemStatus.STALE,
        has_open_conflict=has_open_conflict(session, item),
        version_known=bool(item.product_version),
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
        relation="supports",
        retrieved_at=doc.fetched_at,
        source_version=doc.version,
    )
    item.evidence.append(ev)
    return ev


def _has_evidence_from_doc(item: KnowledgeItem, doc: Document) -> bool:
    return any(e.document_id == doc.id and e.document_hash == doc.content_hash for e in item.evidence)


def run_validators(session: Session, item: KnowledgeItem, plugin: DomainPlugin) -> int:
    ran = 0
    payload = item_as_dict(item)
    versions = dict(item.validator_versions or {})
    for v in plugin.validators():
        if getattr(v, "kind", "static") != "static":
            # ADR 0003: content-executing validators never run inside the worker process. Until the isolated
            # runner exists they are skipped — and the skip is recorded so "not run" can never read as "passed".
            log.warning("validator %s is %r: skipped (no isolated runner configured)", v.name, v.kind)
            versions[v.name] = {"version": v.version, "skipped": f"{v.kind} validators need the isolated runner"}
            continue
        try:
            if not v.applies_to(payload):
                continue
            result = v.validate(payload)
        except Exception as exc:  # a broken validator must not break ingestion
            log.exception("validator %s failed on %s: %s", v.name, item.id, exc)
            continue
        ran += 1
        versions[v.name] = v.version
        # replace an older result from the same validator version
        item.evidence[:] = [
            e for e in item.evidence if not (e.evidence_type == "validator" and e.details.get("validator") == v.name)
        ]
        item.evidence.append(
            Evidence(
                knowledge_item_id=item.id,
                evidence_type="validator",
                relation="validates",
                excerpt=result.message or f"{v.name} {'passed' if result.passed else 'failed'}",
                verified=True,
                details={"validator": v.name, "version": v.version, "passed": result.passed, **result.details},
            )
        )
    item.validator_versions = versions
    validator_evidence = [e for e in item.evidence if e.evidence_type == "validator"]
    if validator_evidence and all(e.details.get("passed") for e in validator_evidence):
        item.origin = "EXPERIMENTALLY_VALIDATED"  # req. 8: verified through a domain-specific validation process
    return ran


# ----------------------------------------------------------------------------- stale detection


def confirm_unchanged_document(session: Session, doc: Document, plugin: DomainPlugin | None = None) -> int:
    """An unchanged re-crawl (304 or same content hash) confirms that the source still states every claim it
    evidences (audit P1.4): stamp ``last_source_checked_at`` and refresh the freshness factor. It is *not* a new
    verification — ``last_verified_at`` and the verification level are untouched."""
    items = (
        session.execute(
            select(KnowledgeItem)
            .where(
                KnowledgeItem.id.in_(
                    select(Evidence.knowledge_item_id).where(
                        Evidence.document_id == doc.id,
                        Evidence.evidence_type == "extraction",
                        Evidence.verified.is_(True),
                        Evidence.document_hash == doc.content_hash,
                    )
                ),
                KnowledgeItem.status.in_([ItemStatus.SUPPORTED, ItemStatus.VERIFIED, ItemStatus.CONFLICTED]),
            )
            .options(selectinload(KnowledgeItem.evidence))
        )
        .scalars()
        .all()
    )
    now = utcnow()
    for item in items:
        item.last_source_checked_at = now
        if plugin is not None:
            rescore(session, item, plugin, actor="system:freshness")
    session.flush()
    return len(items)


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
    now = utcnow()
    for item in items:
        confirmed = False
        for ev in item.evidence:
            if ev.document_id == doc.id and ev.document_hash != doc.content_hash and ev.evidence_type == "extraction":
                still_there = ev.excerpt and ev.excerpt in doc.text
                ev.verified = bool(still_there)
                if still_there:
                    ev.document_hash = doc.content_hash
                    ev.source_version = doc.version
                    confirmed = True
        if confirmed:
            # the page changed but still states the claim: checked now, content changed now (no new verification)
            item.last_source_checked_at = now
            item.last_content_changed_at = now
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

    # section-level delta (req. 25): chunks whose text and prompt version are unchanged since the last extraction
    # are not sent to the model again — their items already exist and keep their (re-verified) evidence.
    previous = {
        h["sha256"]
        for h in (doc.chunk_hashes or [])
        if h.get("prompt") == PROMPT_VERSION and h.get("sha256") and not h.get("failed")
    }
    extracted, ext_stats = extract_from_text(
        plugin,
        text=doc.text,
        title=doc.title,
        url=doc.url,
        session=session,
        run_id=run_id,
        max_chunks=max_chunks,
        skip_hashes=previous if doc.version > 1 or previous else None,
    )
    stats.extraction = ext_stats
    doc.chunk_hashes = ext_stats.pop("chunk_hashes", [])

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
            origin="DIRECT",
            provenance=derive_provenance([doc.source] if doc.source else []),
            polarity=ex.polarity,
            details=ex.details,
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
                        relation="supports",
                        retrieved_at=doc.fetched_at,
                        source_version=doc.version,
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
        stats.relations += len(derive_relations(session, item, plugin))
        stats.items_created += 1

    # a changed claim is the next version of the stale item it replaced, not an unrelated new fact (P2.1)
    stats.items_superseded = len(link_superseded(session, doc, survivors))

    for item in touched.values():
        rescore(session, item, plugin)

    failed = int(ext_stats.get("chunks_failed") or 0)
    if failed:
        # partial extraction: what succeeded is kept, the document is not marked done, and the failed sections are
        # what the next extraction pass (retry job or next crawl) sends to the model
        doc.status = DocumentStatus.FETCHED
        doc.error = f"extraction incomplete: {failed} section(s) failed"
    else:
        doc.status = DocumentStatus.EXTRACTED
        doc.extracted_at = utcnow()
        doc.error = None
    session.flush()
    return stats
