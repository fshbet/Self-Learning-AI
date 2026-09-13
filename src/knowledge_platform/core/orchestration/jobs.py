"""Job handlers and run management. Handlers are plain functions: (session, job) -> result dict."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...adapters import get_search
from ...models import (
    Document,
    Job,
    JobStatus,
    Run,
    RunStatus,
    Source,
    SourceStatus,
    utcnow,
)
from ..collection.collector import crawl_source
from ..collection.normalize import canonicalize_url
from ..pipeline import ingest_document
from ..plugins.registry import get_registry
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
    return True


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


@handler("discover")
def discover_job(session: Session, job: Job) -> dict[str, Any]:
    """Web discovery (§9): run the domain's discovery queries and register candidate sources."""
    plugin = get_registry().get(job.payload["domain_id"])
    search = get_search()
    if search is None:
        return {"skipped": "no search provider configured"}
    known_hosts = {
        urlsplit(s.url).netloc.lower()
        for s in session.execute(select(Source).where(Source.domain_id == plugin.id)).scalars()
    }
    queries = job.payload.get("queries") or plugin.discovery_queries() or [f"{plugin.name} documentation"]
    added = 0
    for q in queries:
        try:
            hits = search.search(q, limit=job.payload.get("limit", 10))
        except Exception as exc:
            log.warning("search failed for %r: %s", q, exc)
            continue
        for h in hits:
            url = canonicalize_url(h.url)
            if not url:
                continue
            host = urlsplit(url).netloc.lower()
            if host in known_hosts:
                continue
            known_hosts.add(host)
            session.add(
                Source(
                    domain_id=plugin.id,
                    key=f"discovered:{host}",
                    name=h.title[:200] or host,
                    url=url,
                    publisher=host,
                    authority=30,
                    status=SourceStatus.CANDIDATE,
                    enabled=False,
                    max_depth=1,
                    max_pages=20,
                    notes=f"Discovered via query: {q}\n{h.snippet[:300]}",
                )
            )
            added += 1
    session.flush()
    return {"queries": len(queries), "candidates_added": added}


# ----------------------------------------------------------------------------- scheduler


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
