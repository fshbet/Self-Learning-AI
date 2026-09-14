"""Operational metrics (req. 36/37): queue health, retries and dead letters, model latency, storage, schedule.

Everything here is derived from tables the platform already keeps (jobs, runs, llm_calls, documents, snapshots,
sources, evaluation_runs) — no separate metrics store. The API exposes it as ``/api/stats.ops``; the dashboard shows
it; ``kp ops`` prints it.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..models import (
    Conflict,
    Document,
    EvaluationRun,
    Evidence,
    Job,
    JobStatus,
    KnowledgeItem,
    KnowledgeRelation,
    LLMCall,
    Run,
    Snapshot,
    Source,
    SourceStatus,
    utcnow,
)
from .runtime_config import eval_config, snapshot_config


def _domain_filter(stmt, model, domain: str | None):
    return stmt.where(model.domain_id == domain) if domain and hasattr(model, "domain_id") else stmt


def queue_health(session: Session) -> dict[str, Any]:
    now = utcnow()
    by_status = {str(k): v for k, v in session.execute(select(Job.status, func.count()).group_by(Job.status)).all()}
    oldest_queued = session.execute(
        select(func.min(Job.run_at)).where(Job.status == JobStatus.QUEUED, Job.run_at <= now)
    ).scalar_one()
    retried = session.execute(select(func.count()).select_from(Job).where(Job.attempts > 1)).scalar_one()
    dead_by_type = {
        str(k): v
        for k, v in session.execute(
            select(Job.type, func.count()).where(Job.status == JobStatus.DEAD).group_by(Job.type)
        ).all()
    }
    day_ago = now - timedelta(hours=24)
    duration_ms = func.extract("epoch", Job.finished_at - Job.locked_at) * 1000
    durations = session.execute(
        select(Job.type, func.avg(duration_ms), func.max(duration_ms), func.count())
        .where(Job.status == JobStatus.DONE, Job.finished_at.is_not(None), Job.locked_at.is_not(None))
        .where(Job.finished_at >= day_ago)
        .group_by(Job.type)
    ).all()
    return {
        "by_status": by_status,
        "queued": by_status.get("QUEUED", 0),
        "running": by_status.get("RUNNING", 0),
        "failed_awaiting_retry": by_status.get("FAILED", 0),
        "dead_letter": by_status.get("DEAD", 0),
        "dead_by_type": dead_by_type,
        "jobs_retried": retried,
        "oldest_queued_age_seconds": int((now - oldest_queued).total_seconds()) if oldest_queued else 0,
        "job_duration_24h": {
            str(t): {"avg_ms": int(avg or 0), "max_ms": int(mx or 0), "count": n} for t, avg, mx, n in durations
        },
    }


def model_latency(session: Session) -> dict[str, Any]:
    now = utcnow()
    day_ago = now - timedelta(hours=24)
    rows = session.execute(
        select(
            LLMCall.purpose,
            func.count(),
            func.avg(LLMCall.latency_ms),
            func.percentile_cont(0.95).within_group(LLMCall.latency_ms),
            func.sum(case((LLMCall.ok.is_(False), 1), else_=0)),
        )
        .where(LLMCall.created_at >= day_ago)
        .group_by(LLMCall.purpose)
    ).all()
    return {
        "window_hours": 24,
        "by_purpose": {
            str(p): {"calls": n, "avg_ms": int(avg or 0), "p95_ms": int(p95 or 0), "failed": int(f or 0)}
            for p, n, avg, p95, f in rows
        },
        "calls": sum(n for _, n, *_ in rows),
        "failed": sum(int(f or 0) for *_, f in rows),
    }


def storage(session: Session, domain: str | None = None) -> dict[str, Any]:
    docs = session.execute(
        _domain_filter(select(func.count(), func.coalesce(func.sum(Document.byte_size), 0)), Document, domain)
    ).one()
    mirrors = session.execute(
        _domain_filter(
            select(func.count()).select_from(Document).where(Document.canonical_document_id.is_not(None)),
            Document,
            domain,
        )
    ).scalar_one()
    snaps = session.execute(
        _domain_filter(
            select(func.count(), func.coalesce(func.sum(Snapshot.size_bytes), 0)).where(Snapshot.status == "ready"),
            Snapshot,
            domain,
        )
    ).one()
    evidence = session.execute(
        select(func.count()).select_from(Evidence).join(KnowledgeItem, KnowledgeItem.id == Evidence.knowledge_item_id)
        if not domain
        else select(func.count())
        .select_from(Evidence)
        .join(KnowledgeItem, KnowledgeItem.id == Evidence.knowledge_item_id)
        .where(KnowledgeItem.domain_id == domain)
    ).scalar_one()
    embedded = session.execute(
        _domain_filter(
            select(func.count()).select_from(KnowledgeItem).where(KnowledgeItem.embedding.is_not(None)),
            KnowledgeItem,
            domain,
        )
    ).scalar_one()
    try:
        database_bytes = int(session.execute(select(func.pg_database_size(func.current_database()))).scalar_one())
    except Exception:  # noqa: BLE001 - a non-PostgreSQL or restricted connection simply lacks the figure
        session.rollback()
        database_bytes = 0
    return {
        "database_bytes": database_bytes,
        "documents": int(docs[0]),
        "document_bytes": int(docs[1]),
        "mirror_documents": int(mirrors),
        "snapshots": int(snaps[0]),
        "snapshot_bytes": int(snaps[1]),
        "evidence_records": int(evidence),
        "items_embedded": int(embedded),
    }


def schedule(session: Session, domain: str | None = None) -> dict[str, Any]:
    """When the scheduler will act next, per kind — so an operator can see the system is alive and on time."""
    now = utcnow()
    sources = session.execute(
        _domain_filter(
            select(Source).where(
                Source.enabled.is_(True), Source.status == SourceStatus.ACTIVE, Source.last_checked_at.is_not(None)
            ),
            Source,
            domain,
        )
    ).scalars()
    next_checks = [s.last_checked_at + timedelta(hours=s.crawl_frequency_hours) for s in sources]
    next_source = min(next_checks) if next_checks else None
    overdue_sources = sum(1 for t in next_checks if t <= now)

    ev = eval_config()
    last_eval = session.execute(
        _domain_filter(select(func.max(EvaluationRun.started_at)), EvaluationRun, domain)
    ).scalar_one()
    next_eval = (last_eval + timedelta(hours=ev["interval_hours"])) if last_eval and ev["interval_hours"] > 0 else None

    sn = snapshot_config()
    last_snap = session.execute(
        _domain_filter(
            select(func.max(Snapshot.created_at)).where(Snapshot.status == "ready", Snapshot.kind == "full"),
            Snapshot,
            domain,
        )
    ).scalar_one()
    next_snap = (last_snap + timedelta(hours=sn["interval_hours"])) if last_snap and sn["interval_hours"] > 0 else None
    last_run = session.execute(
        _domain_filter(select(Run).order_by(Run.started_at.desc()).limit(1), Run, domain)
    ).scalar_one_or_none()
    iso = lambda t: t.isoformat() if t else None  # noqa: E731
    return {
        "next_source_check": iso(next_source),
        "sources_overdue": overdue_sources,
        "evaluation_interval_hours": ev["interval_hours"],
        "last_evaluation": iso(last_eval),
        "next_evaluation": iso(next_eval),
        "snapshot_interval_hours": sn["interval_hours"],
        "last_snapshot": iso(last_snap),
        "next_snapshot": iso(next_snap),
        "last_run": {"kind": last_run.kind, "status": last_run.status, "started_at": iso(last_run.started_at)}
        if last_run
        else None,
    }


def knowledge_metrics(session: Session, domain: str | None = None) -> dict[str, Any]:
    """Corpus size and trust-state counts (P2.0.1): the numbers to compare before and after a crawl.

    Counts are by state, never a score, so growth in coverage and growth in doubt (conflicted, stale, flagged)
    are visible side by side. ``last_evaluation`` carries the latest finished evaluation's metrics verbatim.
    """
    ki = _domain_filter(
        select(KnowledgeItem.status, func.count()).group_by(KnowledgeItem.status), KnowledgeItem, domain
    )
    by_status = {str(k): int(v) for k, v in session.execute(ki).all()}
    sources = session.execute(
        _domain_filter(
            select(Source.status, Source.enabled, func.count()).group_by(Source.status, Source.enabled),
            Source,
            domain,
        )
    ).all()
    docs = session.execute(
        _domain_filter(select(Document.status, func.count()).group_by(Document.status), Document, domain)
    ).all()
    ev_stmt = (
        select(Evidence.evidence_type, Evidence.relation, Evidence.verified, func.count())
        .join(KnowledgeItem, KnowledgeItem.id == Evidence.knowledge_item_id)
        .group_by(Evidence.evidence_type, Evidence.relation, Evidence.verified)
    )
    if domain:
        ev_stmt = ev_stmt.where(KnowledgeItem.domain_id == domain)
    evidence = session.execute(ev_stmt).all()
    relations = session.execute(
        _domain_filter(
            select(KnowledgeRelation.relation_type, func.count()).group_by(KnowledgeRelation.relation_type),
            KnowledgeRelation,
            domain,
        )
    ).all()
    conflicts = session.execute(
        _domain_filter(select(Conflict.status, func.count()).group_by(Conflict.status), Conflict, domain)
    ).all()
    flags = session.execute(
        _domain_filter(
            select(
                func.sum(case((KnowledgeItem.needs_review.is_(True), 1), else_=0)),
                func.sum(case((KnowledgeItem.needs_revalidation.is_(True), 1), else_=0)),
                func.sum(case((KnowledgeItem.polarity == "negative", 1), else_=0)),
                func.sum(case((KnowledgeItem.origin != "DIRECT", 1), else_=0)),
                func.sum(case((KnowledgeItem.superseded_by_id.is_not(None), 1), else_=0)),
            ),
            KnowledgeItem,
            domain,
        )
    ).one()
    by_type = {
        str(k): int(v)
        for k, v in session.execute(
            _domain_filter(
                select(KnowledgeItem.knowledge_type, func.count()).group_by(KnowledgeItem.knowledge_type),
                KnowledgeItem,
                domain,
            )
        ).all()
    }
    area = func.split_part(KnowledgeItem.topic, "/", 1)
    topics = session.execute(_domain_filter(select(area, func.count()).group_by(area), KnowledgeItem, domain)).all()
    last_eval = session.execute(
        _domain_filter(
            select(EvaluationRun).where(EvaluationRun.status == "DONE").order_by(EvaluationRun.started_at.desc()),
            EvaluationRun,
            domain,
        ).limit(1)
    ).scalar_one_or_none()
    calls = session.execute(
        select(
            LLMCall.purpose,
            func.count(),
            func.coalesce(func.sum(LLMCall.prompt_tokens), 0),
            func.coalesce(func.sum(LLMCall.completion_tokens), 0),
        ).group_by(LLMCall.purpose)
    ).all()
    return {
        "sources": {
            "total": sum(int(n) for *_, n in sources),
            "active_enabled": sum(int(n) for st, en, n in sources if st == SourceStatus.ACTIVE and en),
            "by_status": {f"{st}{'' if en else ' (disabled)'}": int(n) for st, en, n in sources},
        },
        "documents": {"total": sum(int(n) for _, n in docs), "by_status": {str(k): int(v) for k, v in docs}},
        "knowledge_items": {
            "total": sum(by_status.values()),
            "by_status": by_status,
            "by_type": by_type,
            "by_area": {str(k or "(none)"): int(v) for k, v in topics},
            "needs_review": int(flags[0] or 0),
            "needs_revalidation": int(flags[1] or 0),
            "negative": int(flags[2] or 0),
            "derived_or_synthesized": int(flags[3] or 0),
            "superseded": int(flags[4] or 0),
        },
        "evidence": {
            "total": sum(int(n) for *_, n in evidence),
            "verified": sum(int(n) for _, _, ver, n in evidence if ver),
            "by_type": {f"{t}/{r}": int(n) for t, r, _, n in evidence},
        },
        "relationships": {
            "total": sum(int(n) for _, n in relations),
            "by_type": {str(k): int(v) for k, v in relations},
        },
        "conflicts": {str(k): int(v) for k, v in conflicts},
        "model_calls_total": {
            str(p): {"calls": int(n), "prompt_tokens": int(pt), "completion_tokens": int(ct)} for p, n, pt, ct in calls
        },
        "last_evaluation": {
            "id": str(last_eval.id),
            "started_at": last_eval.started_at.isoformat(),
            "dataset_version": last_eval.dataset_version,
            "metrics": last_eval.metrics,
        }
        if last_eval
        else None,
    }


def ops_metrics(session: Session, domain: str | None = None) -> dict[str, Any]:
    return {
        "queue": queue_health(session),
        "models": model_latency(session),
        "storage": storage(session, domain),
        "schedule": schedule(session, domain),
        "knowledge": knowledge_metrics(session, domain),
    }


__all__ = ["knowledge_metrics", "model_latency", "ops_metrics", "queue_health", "schedule", "storage"]
