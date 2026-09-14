"""Job handlers and run management. Handlers are plain functions: (session, job) -> result dict."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...adapters import get_search
from ...models import (
    Document,
    EvaluationRun,
    Job,
    JobStatus,
    KnowledgeItem,
    Run,
    RunStatus,
    Snapshot,
    Source,
    SourceStatus,
    utcnow,
)
from ..collection.collector import crawl_source
from ..evaluation.runner import run_evaluation
from ..pipeline import ingest_document
from ..plugins.registry import get_registry
from ..runtime_config import discovery_config, eval_config, snapshot_config
from .queue import enqueue

log = logging.getLogger(__name__)

Handler = Callable[[Session, Job], dict[str, Any]]
HANDLERS: dict[str, Handler] = {}


def handler(job_type: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[job_type] = fn
        return fn

    return deco


# ----------------------------------------------------------------------------- runs


def start_run(session: Session, *, domain_id: str | None, kind: str, triggered_by: str = "cli") -> Run:
    run = Run(domain_id=domain_id, kind=kind, triggered_by=triggered_by)
    session.add(run)
    session.flush()
    return run


def start_pipeline_run(
    session: Session,
    domain_id: str,
    *,
    triggered_by: str = "cli",
    source_keys: list[str] | None = None,
    max_pages: int | None = None,
) -> tuple[Run, int]:
    """Create a run and enqueue one crawl job per enabled source of the domain."""
    plugin = get_registry().get(domain_id)  # raises for unknown domains
    run = start_run(session, domain_id=plugin.id, kind="pipeline", triggered_by=triggered_by)
    stmt = select(Source).where(
        Source.domain_id == plugin.id, Source.enabled.is_(True), Source.status == SourceStatus.ACTIVE
    )
    if source_keys:
        stmt = stmt.where(Source.key.in_(source_keys))
    count = 0
    for src in session.execute(stmt).scalars():
        payload = {"source_id": str(src.id)}
        if max_pages:
            payload["max_pages"] = max_pages
        if enqueue(session, "crawl_source", payload, run_id=run.id, idempotency_key=f"crawl:{src.id}", priority=50):
            count += 1
    run.stats = {"sources_enqueued": count}
    session.flush()
    return run, count


def start_extract_run(
    session: Session, domain_id: str, *, triggered_by: str = "cli", only_pending: bool = True
) -> tuple[Run, int]:
    """Enqueue extraction for documents (pending ones by default)."""
    run = start_run(session, domain_id=domain_id, kind="extract", triggered_by=triggered_by)
    stmt = select(Document).where(Document.domain_id == domain_id)
    if only_pending:
        stmt = stmt.where(Document.status != "EXTRACTED")
    count = 0
    for doc in session.execute(stmt).scalars():
        if enqueue(
            session,
            "extract_document",
            {"document_id": str(doc.id)},
            run_id=run.id,
            idempotency_key=f"extract:{doc.id}:{doc.content_hash}",
        ):
            count += 1
    run.stats = {"documents_enqueued": count}
    session.flush()
    return run, count


def finalize_run_if_complete(session: Session, run_id: uuid.UUID) -> bool:
    remaining = session.execute(
        select(func.count())
        .select_from(Job)
        .where(Job.run_id == run_id, Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
    ).scalar_one()
    if remaining:
        return False
    run = session.get(Run, run_id)
    if run is None or run.status != RunStatus.RUNNING:
        return False
    jobs = session.execute(select(Job).where(Job.run_id == run_id)).scalars().all()
    agg: dict[str, Any] = dict(run.stats or {})
    agg["jobs_done"] = sum(1 for j in jobs if j.status == JobStatus.DONE)
    agg["jobs_dead"] = sum(1 for j in jobs if j.status == JobStatus.DEAD)
    totals: dict[str, dict[str, float]] = {}
    for j in jobs:
        if j.status != JobStatus.DONE:
            continue
        bucket = totals.setdefault(j.type, {})
        for k, v in (j.result or {}).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                bucket[k] = bucket.get(k, 0) + v
    agg["totals"] = totals
    run.stats = agg
    run.status = RunStatus.FAILED if agg["jobs_dead"] and not agg["jobs_done"] else RunStatus.DONE
    run.finished_at = utcnow()
    session.flush()
    if (
        run.kind in ("pipeline", "scheduled", "extract")
        and run.status == RunStatus.DONE
        and run.domain_id
        and eval_config()["after_pipeline"]
        and totals.get("extract_document", {}).get("items_created", 0)
    ):
        enqueue_evaluation(session, run.domain_id, triggered_by=f"after-run:{run.id}")
    if (
        run.kind in ("pipeline", "scheduled", "extract")
        and run.status == RunStatus.DONE
        and run.domain_id
        and snapshot_config()["after_pipeline"]
        and totals.get("extract_document", {}).get("items_created", 0)
    ):
        enqueue_snapshot(session, run.domain_id, triggered_by=f"after-run:{run.id}")
    return True


def enqueue_evaluation(session: Session, domain_id: str, *, triggered_by: str = "api", question_ids=None):
    """Queue a golden-set evaluation for a domain (one queued/running at a time per domain)."""
    run = start_run(session, domain_id=domain_id, kind="evaluate", triggered_by=triggered_by)
    payload = {"domain_id": domain_id, "triggered_by": triggered_by}
    if question_ids:
        payload["question_ids"] = list(question_ids)
    job = enqueue(
        session,
        "evaluate",
        payload,
        run_id=run.id,
        idempotency_key=f"evaluate:{domain_id}",
        priority=120,
        max_attempts=1,
    )
    if job is None:
        run.status = RunStatus.FAILED
        run.stats = {"note": "an evaluation for this domain is already queued or running"}
        run.finished_at = utcnow()
        session.flush()
        return run, None
    return run, job


# ----------------------------------------------------------------------------- handlers


@handler("crawl_source")
def crawl_source_job(session: Session, job: Job) -> dict[str, Any]:
    source = session.get(Source, uuid.UUID(job.payload["source_id"]))
    if source is None:
        return {"skipped": "source missing"}
    stats = crawl_source(session, source, run_id=job.run_id, max_pages=job.payload.get("max_pages"))
    enqueued = 0
    for doc_id in stats.to_extract:
        doc = session.get(Document, uuid.UUID(doc_id))
        if doc and enqueue(
            session,
            "extract_document",
            {"document_id": doc_id},
            run_id=job.run_id,
            idempotency_key=f"extract:{doc_id}:{doc.content_hash}",
            priority=100,
        ):
            enqueued += 1
    out = stats.as_dict()
    out["extract_enqueued"] = enqueued
    return out


@handler("extract_document")
def extract_document_job(session: Session, job: Job) -> dict[str, Any]:
    doc = session.get(Document, uuid.UUID(job.payload["document_id"]))
    if doc is None:
        return {"skipped": "document missing"}
    plugin = get_registry().get(doc.domain_id)
    stats = ingest_document(session, doc, plugin, run_id=job.run_id, max_chunks=job.payload.get("max_chunks"))
    out = stats.as_dict()
    ext = out.pop("extraction", {})
    out.update({f"extraction_{k}": v for k, v in ext.items()})
    return out


@handler("evaluate")
def evaluate_job(session: Session, job: Job) -> dict[str, Any]:
    """Run the domain's golden question set and store results, metrics and regression status (req. 15)."""
    plugin = get_registry().get(job.payload["domain_id"])
    ev = run_evaluation(
        session,
        plugin,
        triggered_by=job.payload.get("triggered_by", "worker"),
        run_id=job.run_id,
        question_ids=job.payload.get("question_ids"),
    )
    out = {"evaluation_run_id": str(ev.id), "status": ev.status, "regression": ev.regression}
    for k in ("questions", "passed", "accuracy", "citation_correctness", "hallucination_rate"):
        v = ev.metrics.get(k)
        if isinstance(v, (int, float)):
            out[k] = v
    if ev.error:
        out["error"] = ev.error
    return out


@handler("revalidate_item")
def revalidate_item_job(session: Session, job: Job) -> dict[str, Any]:
    """Re-check an item flagged by a dependency change (req. 17): validators, score, conflicts; clear the
    *dependency* flag only when every propagating dependency is live again. Never edits the item's statement
    and never touches the review flag (needs_review): that is a reviewer's decision (audit P0.2)."""
    from ..pipeline import rescore, run_validators
    from ..verification.conflicts import detect_conflicts
    from ..versioning.dependencies import unresolved_dependencies

    item = session.get(KnowledgeItem, uuid.UUID(job.payload["item_id"]))
    if item is None:
        return {"skipped": "item missing"}
    plugin = get_registry().get(item.domain_id)
    validators = run_validators(session, item, plugin)
    rescore(session, item, plugin, actor="system:revalidate")
    conflicts = len(detect_conflicts(session, item, domain_name=plugin.name, run_id=job.run_id))
    unresolved = unresolved_dependencies(session, item)
    if unresolved:
        item.needs_revalidation = True
        item.revalidation_reason = (
            "unresolved dependencies: " + ", ".join(f"{k.subject} ({k.status})" for k in unresolved)[:400]
        )
    else:
        item.needs_revalidation = False
        item.revalidation_reason = None
    session.flush()
    return {
        "validators_run": validators,
        "conflicts": conflicts,
        "unresolved_dependencies": len(unresolved),
        "status": item.status,
    }


def enqueue_revalidations(session: Session, domain_id: str | None = None) -> int:
    """Queue revalidate_item jobs for every flagged item (called by the API and the scheduler)."""
    stmt = select(KnowledgeItem).where(KnowledgeItem.needs_revalidation.is_(True))
    if domain_id:
        stmt = stmt.where(KnowledgeItem.domain_id == domain_id)
    count = 0
    for it in session.execute(stmt).scalars():
        if enqueue(
            session,
            "revalidate_item",
            {"item_id": str(it.id)},
            idempotency_key=f"revalidate:{it.id}",
            priority=95,
            max_attempts=2,
        ):
            count += 1
    return count


@handler("falsify_item")
def falsify_item_job(session: Session, job: Job) -> dict[str, Any]:
    """Optional active falsification: look for counter-evidence on the web; flag, never rewrite (V2 §P7)."""
    from ..verification.falsify import falsify_item

    item = session.get(KnowledgeItem, uuid.UUID(job.payload["item_id"]))
    if item is None:
        return {"skipped": "item missing"}
    plugin = get_registry().get(item.domain_id)
    out = falsify_item(session, plugin, item, max_pages=int(job.payload.get("max_pages", 4)))
    out.pop("hits", None)
    return out


def enqueue_falsifications(session: Session, domain_id: str, *, limit: int = 5, triggered_by: str = "api") -> int:
    """Queue falsification for the live items least recently checked (or never)."""
    items = (
        session.execute(
            select(KnowledgeItem)
            .where(KnowledgeItem.domain_id == domain_id, KnowledgeItem.status.in_(["SUPPORTED", "VERIFIED"]))
            .order_by(KnowledgeItem.last_verified_at.asc().nulls_first())
            .limit(limit * 3)
        )
        .scalars()
        .all()
    )
    items.sort(key=lambda k: (k.details or {}).get("last_falsified_at") or "")
    run = start_run(session, domain_id=domain_id, kind="falsify", triggered_by=triggered_by)
    count = 0
    for item in items[:limit]:
        if enqueue(
            session,
            "falsify_item",
            {"item_id": str(item.id)},
            run_id=run.id,
            idempotency_key=f"falsify:{item.id}",
            priority=130,
            max_attempts=1,
        ):
            count += 1
    run.stats = {"items_enqueued": count}
    session.flush()
    return count


@handler("snapshot")
def snapshot_job(session: Session, job: Job) -> dict[str, Any]:
    """Build a Canonical Knowledge Snapshot (full) for a domain (req. 3–6)."""
    from ..export.snapshot import build_snapshot

    plugin = get_registry().get(job.payload["domain_id"])
    snap = build_snapshot(session, plugin, created_by=job.payload.get("created_by", "worker"))
    out: dict[str, Any] = {"snapshot_id": str(snap.id), "version": snap.version, "status": snap.status}
    if snap.status == "ready":
        out.update({k: v for k, v in (snap.manifest.get("counts") or {}).items() if isinstance(v, int)})
        out["size_bytes"] = snap.size_bytes
    if snap.error:
        out["error"] = snap.error
    return out


@handler("reembed")
def reembed_job(session: Session, job: Job) -> dict[str, Any]:
    """Re-embed live items with the active embedding model; adjusts the vector column when the dimension changed."""
    from sqlalchemy import text

    from ...adapters import get_embedder
    from ..retrieval.embeddings import embed_items

    embedder = get_embedder()
    current_dim = session.execute(
        text(
            "SELECT atttypmod FROM pg_attribute WHERE attrelid = 'knowledge_items'::regclass AND attname = 'embedding'"
        )
    ).scalar_one()
    changed_dim = False
    if current_dim != embedder.dimension:
        # pgvector stores the dimension in atttypmod; changing it means every stored vector is invalid.
        session.execute(text("DROP INDEX IF EXISTS ix_ki_embedding_hnsw"))
        session.execute(text("UPDATE knowledge_items SET embedding = NULL, embedding_model = NULL"))
        session.execute(
            text(f"ALTER TABLE knowledge_items ALTER COLUMN embedding TYPE vector({int(embedder.dimension)})")
        )
        session.execute(
            text("CREATE INDEX ix_ki_embedding_hnsw ON knowledge_items USING hnsw (embedding vector_cosine_ops)")
        )
        session.commit()
        changed_dim = True

    stmt = select(KnowledgeItem).where(
        KnowledgeItem.status != "REJECTED",
        (KnowledgeItem.embedding_model.is_(None)) | (KnowledgeItem.embedding_model != embedder.identity),
    )
    if job.payload.get("domain_id"):
        stmt = stmt.where(KnowledgeItem.domain_id == job.payload["domain_id"])
    total = 0
    while True:
        batch = session.execute(stmt.limit(200)).scalars().all()
        if not batch:
            break
        embed_items(session, batch)
        session.commit()
        total += len(batch)
    return {"reembedded": total, "identity": embedder.identity, "dimension_changed": changed_dim}


@handler("discover")
def discover_job(session: Session, job: Job) -> dict[str, Any]:
    """Web discovery (ADR 0005): scored, filtered candidate sources for the domain — never approved here."""
    from ..collection.discovery import discover

    plugin = get_registry().get(job.payload["domain_id"])
    search = get_search()
    if search is None:
        return {"skipped": "no search provider configured"}
    stats = discover(
        session, plugin, search=search, queries=job.payload.get("queries"), limit=int(job.payload.get("limit", 10))
    )
    return stats.as_dict()


# ----------------------------------------------------------------------------- scheduler


def schedule_due_evaluations(session: Session) -> int:
    """Periodic evaluation per domain that has knowledge (req. 37)."""
    hours = eval_config()["interval_hours"]
    if hours <= 0:
        return 0
    cutoff = utcnow() - timedelta(hours=hours)
    count = 0
    for domain_id in session.execute(
        select(KnowledgeItem.domain_id).where(KnowledgeItem.status.in_(["SUPPORTED", "VERIFIED"])).distinct()
    ).scalars():
        if domain_id not in get_registry() or not get_registry().get(domain_id).evaluation_set():
            continue
        last = session.execute(
            select(func.max(EvaluationRun.started_at)).where(EvaluationRun.domain_id == domain_id)
        ).scalar_one()
        if last is None or last <= cutoff:
            _, job = enqueue_evaluation(session, domain_id, triggered_by="scheduler")
            if job:
                count += 1
    return count


def enqueue_snapshot(session: Session, domain_id: str, *, triggered_by: str = "api") -> Job | None:
    run = start_run(session, domain_id=domain_id, kind="snapshot", triggered_by=triggered_by)
    job = enqueue(
        session,
        "snapshot",
        {"domain_id": domain_id, "created_by": triggered_by},
        run_id=run.id,
        idempotency_key=f"snapshot:{domain_id}",
        priority=110,
        max_attempts=1,
    )
    if job is None:
        run.status = RunStatus.FAILED
        run.stats = {"note": "a snapshot for this domain is already queued or running"}
        run.finished_at = utcnow()
        session.flush()
    return job


def schedule_due_snapshots(session: Session) -> int:
    """Periodic full snapshot per domain with live knowledge (export scheduling, V2 §P7)."""
    hours = snapshot_config()["interval_hours"]
    if hours <= 0:
        return 0
    cutoff = utcnow() - timedelta(hours=hours)
    count = 0
    for domain_id in session.execute(
        select(KnowledgeItem.domain_id).where(KnowledgeItem.status.in_(["SUPPORTED", "VERIFIED"])).distinct()
    ).scalars():
        if domain_id not in get_registry():
            continue
        last = session.execute(
            select(func.max(Snapshot.created_at)).where(
                Snapshot.domain_id == domain_id, Snapshot.kind == "full", Snapshot.status == "ready"
            )
        ).scalar_one()
        if last is None or last <= cutoff:
            if enqueue_snapshot(session, domain_id, triggered_by="scheduler"):
                count += 1
    return count


def schedule_revalidations(session: Session) -> int:
    return enqueue_revalidations(session)


def schedule_due_discovery(session: Session) -> int:
    """Recurring new-source discovery (ADR 0005): off unless discovery.interval_hours > 0; one job per domain whose
    last discovery run is older than the interval. Only candidates are added — approval stays with a person."""
    hours = discovery_config()["interval_hours"]
    if hours <= 0 or get_search() is None:
        return 0
    cutoff = utcnow() - timedelta(hours=hours)
    count = 0
    for plugin in get_registry().all():
        if not plugin.discovery().queries:
            continue
        last = session.execute(
            select(func.max(Run.started_at)).where(Run.domain_id == plugin.id, Run.kind == "discover")
        ).scalar_one()
        if last is not None and last > cutoff:
            continue
        run = start_run(session, domain_id=plugin.id, kind="discover", triggered_by="scheduler")
        if enqueue(
            session,
            "discover",
            {"domain_id": plugin.id},
            run_id=run.id,
            idempotency_key=f"discover:{plugin.id}",
            priority=120,
        ):
            count += 1
    return count


def schedule_due_sources(session: Session) -> int:
    """Enqueue re-checks for sources whose crawl_frequency has elapsed (§28 scheduling).

    Only sources that have been crawled at least once are eligible: the first crawl of a source is an
    explicit decision (Run pipeline / Crawl now), so adding a source never silently starts a large crawl.
    """
    now = utcnow()
    due = [
        s
        for s in session.execute(
            select(Source).where(
                Source.enabled.is_(True), Source.status == SourceStatus.ACTIVE, Source.last_checked_at.is_not(None)
            )
        ).scalars()
        if s.last_checked_at + timedelta(hours=s.crawl_frequency_hours) <= now
    ]
    if not due:
        return 0
    by_domain: dict[str, list[Source]] = {}
    for s in due:
        by_domain.setdefault(s.domain_id, []).append(s)
    count = 0
    for domain_id, sources in by_domain.items():
        run = start_run(session, domain_id=domain_id, kind="scheduled", triggered_by="scheduler")
        for s in sources:
            if enqueue(
                session,
                "crawl_source",
                {"source_id": str(s.id)},
                run_id=run.id,
                idempotency_key=f"crawl:{s.id}",
                priority=80,
            ):
                count += 1
        run.stats = {"sources_enqueued": count}
    session.flush()
    return count
