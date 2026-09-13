"""Canonical Knowledge Snapshot builder (req. 3–6, 26–28, 39).

    gather → gate (schema, provenance, consistency, integrity) → write files → hash → manifest → Snapshot row

A failed gate produces a ``failed`` snapshot row with reasons and writes nothing.
"""

from __future__ import annotations

import io
import json
import logging
import uuid
import zipfile
from collections import defaultdict
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ... import PLUGIN_API_VERSION, __version__
from ...adapters import get_object_store
from ...models import (
    Conflict,
    Document,
    ItemStatus,
    KnowledgeItem,
    Snapshot,
    Source,
    StatusTransition,
    utcnow,
)
from ..extraction.chunker import CHUNKER_VERSION
from ..extraction.prompts import JUDGE_VERSION, PROMPT_VERSION
from ..plugins.base import DomainPlugin
from ..quality.provenance import derive_origin, derive_polarity, derive_provenance
from ..quality.scoring import SCORING_RULE_VERSION
from ..runtime_config import effective_config
from .canonical import dumps_canonical, integrity_hash, iso, jsonl, sha256_bytes
from .render import RENDER_VERSION, ai_index, ai_markdown, ai_record, markdown_to_html, readme
from .schema import (
    SCHEMA_VERSION,
    ChangelogRecord,
    ConflictRecord,
    EvidenceRecord,
    ExampleRecord,
    KnowledgeRecord,
    Manifest,
    RelationshipRecord,
    SourceRecord,
)

log = logging.getLogger(__name__)

CURRENT_STATES = (ItemStatus.VERIFIED, ItemStatus.SUPPORTED, ItemStatus.CONFLICTED, ItemStatus.STALE)
HISTORICAL_STATES = (ItemStatus.SUPERSEDED,)
CHANGELOG_LIMIT = 20000


class ExportGateError(RuntimeError):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


# ----------------------------------------------------------------------------- gather


def _relations_for(session: Session, domain_id: str) -> list[RelationshipRecord]:
    """Dependency graph edges. Empty until P3 introduces the relations table; kept here so the file exists."""
    try:
        from ...models import KnowledgeRelation  # type: ignore[attr-defined]
    except ImportError:
        return []
    rows = session.execute(select(KnowledgeRelation).where(KnowledgeRelation.domain_id == domain_id)).scalars()
    return [
        RelationshipRecord(
            id=str(r.id),
            from_item_id=str(r.from_item_id),
            to_item_id=str(r.to_item_id),
            relation_type=r.relation_type,
            origin=r.origin,
            details=r.details or {},
        )
        for r in rows
    ]


def gather(session: Session, plugin: DomainPlugin) -> dict[str, Any]:
    domain_id = plugin.id
    items = (
        session.execute(
            select(KnowledgeItem)
            .where(KnowledgeItem.domain_id == domain_id, KnowledgeItem.status.in_(CURRENT_STATES + HISTORICAL_STATES))
            .options(selectinload(KnowledgeItem.evidence))
            .order_by(KnowledgeItem.id)
        )
        .scalars()
        .all()
    )
    sources = {s.id: s for s in session.execute(select(Source).where(Source.domain_id == domain_id)).scalars()}
    doc_ids = {e.document_id for it in items for e in it.evidence if e.document_id}
    documents = (
        {d.id: d for d in session.execute(select(Document).where(Document.id.in_(doc_ids))).scalars()}
        if doc_ids
        else {}
    )
    doc_counts = dict(
        session.execute(
            select(Document.source_id, func.count()).where(Document.domain_id == domain_id).group_by(Document.source_id)
        ).all()
    )
    relations = _relations_for(session, domain_id)
    deps_by_item: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in relations:
        deps_by_item[r.from_item_id].append({"relation": r.relation_type, "item_id": r.to_item_id})

    knowledge: list[KnowledgeRecord] = []
    evidence: list[EvidenceRecord] = []
    examples: list[ExampleRecord] = []
    by_subject: dict[str, list[str]] = defaultdict(list)
    for it in items:
        by_subject[(it.subject or "").strip().lower()].append(str(it.id))

    for it in items:
        item_sources = [sources[e.source_id] for e in it.evidence if e.source_id in sources]
        ev_records: list[EvidenceRecord] = []
        for e in sorted(it.evidence, key=lambda x: str(x.id)):
            src = sources.get(e.source_id) if e.source_id else None
            doc = documents.get(e.document_id) if e.document_id else None
            ev_records.append(
                EvidenceRecord(
                    id=str(e.id),
                    knowledge_item_id=str(it.id),
                    evidence_type=e.evidence_type,
                    relation=e.relation or "supports",
                    source_id=str(src.id) if src else None,
                    source_url=src.url if src else None,
                    source_name=src.name if src else None,
                    publisher=src.publisher if src else None,
                    source_authority=src.authority if src else None,
                    source_origin=src.origin if src else None,
                    document_id=str(doc.id) if doc else None,
                    document_url=e.url or (doc.url if doc else None),
                    document_title=doc.title if doc else None,
                    document_version=doc.version if doc else None,
                    document_hash=e.document_hash,
                    publication_date=doc.published_at if doc else None,
                    retrieved_at=iso(e.retrieved_at or (doc.fetched_at if doc else None)),
                    section=list((e.locator or {}).get("heading_path") or []),
                    locator={k: v for k, v in (e.locator or {}).items() if k != "heading_path"},
                    excerpt=e.excerpt,
                    verified=bool(e.verified),
                    details=e.details or {},
                )
            )
        evidence.extend(ev_records)
        rec = KnowledgeRecord(
            id=str(it.id),
            domain=domain_id,
            knowledge_type=it.knowledge_type,
            origin=it.origin or derive_origin(it),  # type: ignore[arg-type]
            provenance=it.provenance or derive_provenance(item_sources),  # type: ignore[arg-type]
            polarity=it.polarity or derive_polarity(it.knowledge_type),  # type: ignore[arg-type]
            subject=it.subject,
            predicate=it.predicate,
            object=it.object,
            statement=it.statement,
            explanation=it.explanation or "",
            topic=it.topic or "",
            tags=[str(t) for t in (it.tags or [])],
            code=it.code,
            details=it.details or {},
            product_version=it.product_version,
            language=it.language,
            publication_date=it.publication_date,
            effective_date=it.effective_date,
            valid_until=iso(it.valid_until),
            status=it.status,
            historical=it.status in HISTORICAL_STATES,
            confidence=float(it.confidence),
            verification_level=int(it.verification_level),
            quality_factors=it.quality_factors or {},
            scoring_rule_version=it.scoring_rule_version or "",
            version=it.version,
            previous_version_id=str(it.previous_version_id) if it.previous_version_id else None,
            superseded_by_id=str(it.superseded_by_id) if it.superseded_by_id else None,
            first_discovered_at=iso(it.first_discovered_at) or "",
            last_verified_at=iso(it.last_verified_at),
            extraction=it.extraction or {},
            evidence_ids=[r.id for r in ev_records],
            source_ids=sorted({r.source_id for r in ev_records if r.source_id}),
            dependencies=deps_by_item.get(str(it.id), []),
            content_hash=it.content_hash,
        )
        knowledge.append(rec)
        if it.knowledge_type == "example" and not rec.historical:
            details = it.details or {}
            examples.append(
                ExampleRecord(
                    id=str(it.id),
                    subject=it.subject,
                    topic=it.topic or "",
                    statement=it.statement,
                    explanation=it.explanation or "",
                    code=it.code,
                    expected_behavior=details.get("expected_behavior"),
                    expected_result=details.get("expected_result"),
                    common_mistake=details.get("common_mistake"),
                    validation_method=details.get("validation_method"),
                    validation_results=[r.details for r in ev_records if r.evidence_type == "validator"],
                    supporting_item_ids=[i for i in by_subject[(it.subject or "").strip().lower()] if i != str(it.id)],
                    status=it.status,
                    confidence=float(it.confidence),
                    source_ids=rec.source_ids,
                )
            )

    source_records = [
        SourceRecord(
            id=str(s.id),
            key=s.key,
            name=s.name,
            url=s.url,
            publisher=s.publisher,
            source_type=s.source_type,
            origin=s.origin,
            authority=s.authority,
            access_type=s.access_type,
            license=s.license,
            permissions=s.permissions or {},
            crawl_frequency_hours=s.crawl_frequency_hours,
            status=s.status,
            enabled=s.enabled,
            last_checked_at=iso(s.last_checked_at),
            last_changed_at=iso(s.last_changed_at),
            document_count=int(doc_counts.get(s.id, 0)),
        )
        for s in sorted(sources.values(), key=lambda s: str(s.id))
    ]
    conflicts = [
        ConflictRecord(
            id=str(c.id),
            item_a_id=str(c.item_a_id),
            item_b_id=str(c.item_b_id),
            status=c.status,
            reason=c.reason,
            resolution=c.resolution,
            resolved_by=c.resolved_by,
            created_at=iso(c.created_at) or "",
            resolved_at=iso(c.resolved_at),
        )
        for c in session.execute(
            select(Conflict).where(Conflict.domain_id == domain_id).order_by(Conflict.id)
        ).scalars()
    ]
    item_ids = [it.id for it in items]
    changelog = (
        [
            ChangelogRecord(
                id=str(t.id),
                knowledge_item_id=str(t.knowledge_item_id),
                from_status=t.from_status,
                to_status=t.to_status,
                reason=t.reason,
                actor=t.actor,
                at=iso(t.created_at) or "",
            )
            for t in session.execute(
                select(StatusTransition)
                .where(StatusTransition.knowledge_item_id.in_(item_ids))
                .order_by(StatusTransition.created_at, StatusTransition.id)
                .limit(CHANGELOG_LIMIT)
            ).scalars()
        ]
        if item_ids
        else []
    )
    glossary = {
        "terminology": plugin.terminology(),
        "taxonomy": [n.model_dump() for n in plugin.taxonomy()],
        "knowledge_types": plugin.knowledge_types(),
        "risk_classes": {k: v.model_dump() for k, v in plugin.risk_classes().items()},
    }
    return {
        "knowledge": knowledge,
        "evidence": evidence,
        "sources": source_records,
        "relationships": relations,
        "examples": examples,
        "negative": [k for k in knowledge if k.polarity == "negative" and not k.historical],
        "conflicts": conflicts,
        "changelog": changelog,
        "glossary": glossary,
    }


# ----------------------------------------------------------------------------- gate


def run_gate(data: dict[str, Any]) -> dict[str, Any]:
    """Export quality gate (req. 39). Returns a report; raises ExportGateError on hard failures."""
    reasons: list[str] = []
    knowledge: list[KnowledgeRecord] = data["knowledge"]
    evidence: list[EvidenceRecord] = data["evidence"]
    ev_ids = {e.id for e in evidence}
    item_ids = {k.id for k in knowledge}
    # schema validation: re-validate every record through pydantic
    for name in ("knowledge", "evidence", "sources", "relationships", "examples", "conflicts", "changelog"):
        for rec in data[name]:
            try:
                type(rec).model_validate(rec.model_dump())
            except ValidationError as exc:
                reasons.append(f"schema: {name} {getattr(rec, 'id', '?')}: {exc.errors()[0]['msg']}")
                break
    # provenance: every current, non-derived item needs verified extraction evidence or a derived_from relation
    verified_by_item = defaultdict(bool)
    for e in evidence:
        if e.verified and (
            e.evidence_type == "extraction" or (e.evidence_type == "human" and e.details.get("provided"))
        ):
            verified_by_item[e.knowledge_item_id] = True
    missing = [
        k.id
        for k in knowledge
        if not k.historical
        and k.status != ItemStatus.STALE
        and k.origin in ("DIRECT", "EXPERIMENTALLY_VALIDATED")
        and not verified_by_item[k.id]
    ]
    if missing:
        reasons.append(f"provenance: {len(missing)} current items lack verified evidence (e.g. {missing[0]})")
    derived_without_chain = [
        k.id
        for k in knowledge
        if k.origin in ("DERIVED", "SYNTHESIZED") and not any(d["relation"] == "derived_from" for d in k.dependencies)
    ]
    if derived_without_chain:
        reasons.append(f"provenance: {len(derived_without_chain)} derived items lack a derived_from chain")
    # consistency: dangling references
    dangling_ev = [k.id for k in knowledge if any(i not in ev_ids for i in k.evidence_ids)]
    if dangling_ev:
        reasons.append(f"consistency: {len(dangling_ev)} items reference unknown evidence")
    dangling_rel = [
        r.id for r in data["relationships"] if r.from_item_id not in item_ids or r.to_item_id not in item_ids
    ]
    dangling_conf = [c.id for c in data["conflicts"] if c.item_a_id not in item_ids or c.item_b_id not in item_ids]
    report = {
        "schema_ok": not any(r.startswith("schema") for r in reasons),
        "provenance_ok": not any(r.startswith("provenance") for r in reasons),
        "consistency_ok": not dangling_ev,
        "items_without_verified_evidence": len(missing),
        "relationships_dangling": len(dangling_rel),  # informational: edges to items outside the snapshot are dropped
        "conflicts_referencing_excluded_items": len(dangling_conf),
        "open_conflicts": sum(1 for c in data["conflicts"] if c.status == "OPEN"),
        "stale_items": sum(1 for k in knowledge if k.status == ItemStatus.STALE),
        "conflicted_items": sum(1 for k in knowledge if k.status == ItemStatus.CONFLICTED),
    }
    if dangling_rel:
        data["relationships"] = [r for r in data["relationships"] if r.id not in set(dangling_rel)]
    if reasons:
        raise ExportGateError(reasons)
    return report


# ----------------------------------------------------------------------------- build


def _generation_metadata() -> dict[str, Any]:
    cfg = effective_config()
    return {
        "extractor_version": PROMPT_VERSION,
        "chunker_version": CHUNKER_VERSION,
        "scoring_rule_version": SCORING_RULE_VERSION,
        "judge_version": JUDGE_VERSION,
        "render_version": RENDER_VERSION,
        "schema_version": SCHEMA_VERSION,
        "embedding": f"{cfg.embedding.provider}:{cfg.embedding_model}:{cfg.embedding_dimension}"
        if cfg.embedding
        else None,
        "models": dict(cfg.models),
        "llm_provider": cfg.llm.provider,
    }


def build_snapshot(session: Session, plugin: DomainPlugin, *, created_by: str = "api") -> Snapshot:
    """Create a full Canonical Knowledge Snapshot for ``plugin``. Returns the stored Snapshot row (ready or failed)."""
    version = (
        session.execute(
            select(func.coalesce(func.max(Snapshot.version), 0)).where(Snapshot.domain_id == plugin.id)
        ).scalar_one()
        + 1
    )
    snap = Snapshot(domain_id=plugin.id, version=version, kind="full", status="building", created_by=created_by)
    session.add(snap)
    session.flush()
    prefix = f"snapshots/{plugin.id}/{snap.id}"
    try:
        data = gather(session, plugin)
        gate = run_gate(data)
        files = _render_files(snap, plugin, data, gate)
        store = get_object_store()
        digests: dict[str, str] = {}
        sizes: dict[str, int] = {}
        for path, content in files.items():
            store.put(
                f"{prefix}/{path}", content, "application/json" if path.endswith((".json", ".jsonl")) else "text/plain"
            )
            digests[path] = sha256_bytes(content)
            sizes[path] = len(content)
        manifest = _manifest(snap, plugin, data, gate, digests, sizes, files)
        manifest_bytes = dumps_canonical(manifest).encode("utf-8")
        store.put(f"{prefix}/manifest.json", manifest_bytes, "application/json")
        snap.manifest = json.loads(manifest_bytes)
        snap.integrity_hash = manifest.integrity_hash
        snap.object_prefix = prefix
        snap.size_bytes = sum(sizes.values()) + len(manifest_bytes)
        snap.status = "ready"
    except ExportGateError as exc:
        snap.status = "failed"
        snap.error = "export gate: " + "; ".join(exc.reasons)
        snap.manifest = {"gate_failures": exc.reasons}
    except Exception as exc:
        log.exception("snapshot build failed")
        snap.status = "failed"
        snap.error = str(exc)[:2000]
    snap.finished_at = utcnow()
    session.flush()
    return snap


def _render_files(snap: Snapshot, plugin: DomainPlugin, data: dict[str, Any], gate: dict[str, Any]) -> dict[str, bytes]:
    ev_by_item: dict[str, list[EvidenceRecord]] = defaultdict(list)
    for e in data["evidence"]:
        ev_by_item[e.knowledge_item_id].append(e)
    rel_by_item: dict[str, list[str]] = defaultdict(list)
    for r in data["relationships"]:
        rel_by_item[r.from_item_id].append(r.to_item_id)
        rel_by_item[r.to_item_id].append(r.from_item_id)
    # a provisional manifest (no hashes yet) for the renderers' headers
    provisional = Manifest(
        snapshot_id=str(snap.id),
        snapshot_version=snap.version,
        domain=plugin.id,
        plugin_name=plugin.name,
        plugin_version=plugin.manifest.version,
        plugin_api_version=PLUGIN_API_VERSION,
        created_at=iso(snap.created_at) or "",
        platform_version=__version__,
        integrity_hash="(see manifest.json)",
    )
    files: dict[str, bytes] = {
        "knowledge.jsonl": jsonl(data["knowledge"]),
        "evidence.jsonl": jsonl(data["evidence"]),
        "sources.jsonl": jsonl(data["sources"]),
        "relationships.jsonl": jsonl(data["relationships"]),
        "examples.jsonl": jsonl(data["examples"]),
        "negative.jsonl": jsonl(data["negative"]),
        "glossary.json": dumps_canonical(data["glossary"]).encode("utf-8"),
        "conflicts.json": dumps_canonical([c.model_dump() for c in data["conflicts"]]).encode("utf-8"),
        "changelog.jsonl": jsonl(data["changelog"]),
        "ai/knowledge.jsonl": jsonl(
            ai_record(k, ev_by_item.get(k.id, []), sorted(set(rel_by_item.get(k.id, [])))) for k in data["knowledge"]
        ),
        "ai/index.json": dumps_canonical(ai_index(data["knowledge"], data["glossary"])).encode("utf-8"),
    }
    md = ai_markdown(provisional, data["knowledge"], ev_by_item, data["conflicts"], data["glossary"])
    files["ai/knowledge.md"] = md.encode("utf-8")
    files["knowledge.html"] = markdown_to_html(md, f"{plugin.name} — Canonical Knowledge Snapshot").encode("utf-8")
    # plugin extension hook (req. 26): domain-specific files without core knowledge of the domain
    for path, content in (
        plugin.export_extensions({"snapshot_id": str(snap.id), "version": snap.version, "data": data}) or {}
    ).items():
        safe = path.strip("/").replace("..", "")
        files[f"ext/{safe}"] = content if isinstance(content, bytes) else str(content).encode("utf-8")
    # README needs counts → render after the rest is known (no hashes needed)
    counts, status_counts = _counts(data)
    provisional.counts, provisional.status_counts = counts, status_counts
    files["README.md"] = readme(provisional).encode("utf-8")
    return files


def _counts(data: dict[str, Any]) -> tuple[dict[str, int], dict[str, int]]:
    knowledge: list[KnowledgeRecord] = data["knowledge"]
    status_counts: dict[str, int] = defaultdict(int)
    for k in knowledge:
        status_counts[k.status] += 1
    counts = {
        "knowledge": len(knowledge),
        "knowledge_current": sum(1 for k in knowledge if not k.historical),
        "evidence": len(data["evidence"]),
        "sources": len(data["sources"]),
        "relationships": len(data["relationships"]),
        "examples": len(data["examples"]),
        "negative": len(data["negative"]),
        "conflicts": len(data["conflicts"]),
        "changelog": len(data["changelog"]),
        "verified": status_counts.get("VERIFIED", 0),
        "supported": status_counts.get("SUPPORTED", 0),
        "conflicted": status_counts.get("CONFLICTED", 0),
        "stale": status_counts.get("STALE", 0),
        "superseded": status_counts.get("SUPERSEDED", 0),
    }
    return counts, dict(status_counts)


def _manifest(
    snap: Snapshot,
    plugin: DomainPlugin,
    data: dict[str, Any],
    gate: dict[str, Any],
    digests: dict[str, str],
    sizes: dict[str, int],
    files: dict[str, bytes],
) -> Manifest:
    counts, status_counts = _counts(data)
    record_counts = {
        "knowledge.jsonl": counts["knowledge"],
        "evidence.jsonl": counts["evidence"],
        "sources.jsonl": counts["sources"],
        "relationships.jsonl": counts["relationships"],
        "examples.jsonl": counts["examples"],
        "negative.jsonl": counts["negative"],
        "changelog.jsonl": counts["changelog"],
        "ai/knowledge.jsonl": counts["knowledge"],
    }
    validators = sorted({f"{v.name}@{v.version}" for v in plugin.validators()})
    return Manifest(
        schema_version=SCHEMA_VERSION,
        snapshot_id=str(snap.id),
        snapshot_version=snap.version,
        kind="full",
        domain=plugin.id,
        plugin_name=plugin.name,
        plugin_version=plugin.manifest.version,
        plugin_api_version=PLUGIN_API_VERSION,
        created_at=iso(snap.created_at) or "",
        platform_version=__version__,
        generation=_generation_metadata() | {"validators": validators, "dataset_version": plugin.evaluation_version()},
        counts=counts,
        status_counts=status_counts,
        files={p: {"sha256": digests[p], "bytes": sizes[p], "records": record_counts.get(p)} for p in sorted(files)},
        integrity_hash=integrity_hash(digests),
        gate=gate,
    )


# ----------------------------------------------------------------------------- read back / verify / zip


def read_file(snap: Snapshot, path: str) -> bytes:
    return get_object_store().get(f"{snap.object_prefix}/{path}")


def verify_snapshot(snap: Snapshot) -> dict[str, Any]:
    """Recompute every file hash and the integrity hash; report mismatches."""
    store = get_object_store()
    manifest = snap.manifest or {}
    mismatched: list[str] = []
    digests: dict[str, str] = {}
    for path, meta in (manifest.get("files") or {}).items():
        try:
            data = store.get(f"{snap.object_prefix}/{path}")
        except Exception:
            mismatched.append(f"{path}: missing")
            continue
        digest = sha256_bytes(data)
        digests[path] = digest
        if digest != meta.get("sha256"):
            mismatched.append(path)
    recomputed = integrity_hash(digests)
    ok = not mismatched and recomputed == manifest.get("integrity_hash")
    return {"ok": ok, "mismatched": mismatched, "recomputed": recomputed, "expected": manifest.get("integrity_hash")}


def zip_snapshot(snap: Snapshot) -> bytes:
    store = get_object_store()
    root = f"{snap.domain_id}-knowledge-v{snap.version}"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted((snap.manifest or {}).get("files") or {}):
            zf.writestr(f"{root}/{path}", store.get(f"{snap.object_prefix}/{path}"))
        zf.writestr(f"{root}/manifest.json", store.get(f"{snap.object_prefix}/manifest.json"))
    return buf.getvalue()


def latest_ready(session: Session, domain_id: str, kind: str = "full") -> Snapshot | None:
    return session.execute(
        select(Snapshot)
        .where(Snapshot.domain_id == domain_id, Snapshot.status == "ready", Snapshot.kind == kind)
        .order_by(Snapshot.version.desc())
        .limit(1)
    ).scalar_one_or_none()


__all__ = [
    "ExportGateError",
    "build_snapshot",
    "gather",
    "latest_ready",
    "read_file",
    "run_gate",
    "verify_snapshot",
    "zip_snapshot",
    "uuid",
]
