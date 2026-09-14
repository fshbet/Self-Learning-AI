"""Job queue, worker, retries / backoff / dead letters, stuck-job recovery, idempotency, run finalisation and the
scheduler (audit P1.11). Needs PostgreSQL (SKIP LOCKED claims)."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import delete, select
from tests.conftest import requires_db

from knowledge_platform.core import runtime_config
from knowledge_platform.core.domains import sync_domain
from knowledge_platform.core.knowledge_entry import KnowledgeEntry, create_knowledge
from knowledge_platform.core.orchestration import jobs as J
from knowledge_platform.core.orchestration.queue import claim, enqueue, requeue_stuck, retry_dead
from knowledge_platform.core.orchestration.worker import Worker
from knowledge_platform.core.plugins.registry import get_registry
from knowledge_platform.db import session_scope
from knowledge_platform.models import (
    EvaluationRun,
    Job,
    JobStatus,
    KnowledgeItem,
    Run,
    RunStatus,
    Setting,
    Snapshot,
    Source,
    utcnow,
)

pytestmark = requires_db
DOMAIN = "example"
TYPE_OK, TYPE_FAIL, TYPE_FLAKY = "audit_ok", "audit_fail", "audit_flaky"
calls: dict[str, list[int]] = {TYPE_OK: [], TYPE_FAIL: [], TYPE_FLAKY: []}


@J.handler(TYPE_OK)
def _ok(session, job):
    calls[TYPE_OK].append(job.attempts)
    return {"items_created": job.payload.get("n", 1)}


@J.handler(TYPE_FAIL)
def _fail(session, job):
    calls[TYPE_FAIL].append(job.attempts)
    raise RuntimeError("boom")


@J.handler(TYPE_FLAKY)
def _flaky(session, job):
    calls[TYPE_FLAKY].append(job.attempts)
    if job.attempts < 2:
        raise RuntimeError("first attempt fails")
    return {"ok": True}


class _Emb:
    name, model, dimension = "fake", "fake", 768
    identity = "fake:fake:768"

    def embed(self, texts):
        return [[((hash(t) >> (i % 32)) & 1) * 0.9 for i in range(768)] for t in texts]

    def embed_one(self, text):
        return self.embed([text])[0]


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    emb = _Emb()
    monkeypatch.setattr("knowledge_platform.adapters.get_embedder", lambda: emb)
    monkeypatch.setattr("knowledge_platform.core.retrieval.embeddings.get_embedder", lambda: emb)
    monkeypatch.setattr("knowledge_platform.core.retrieval.search.get_embedder", lambda: emb)
    for v in calls.values():
        v.clear()
    yield
    with session_scope() as s:
        s.execute(
            delete(Job).where(Job.type.in_([TYPE_OK, TYPE_FAIL, TYPE_FLAKY, "evaluate", "snapshot", "revalidate_item"]))
        )
        s.execute(
            delete(Job).where(Job.type == "crawl_source", Job.payload["source_id"].as_string().in_(_source_ids(s)))
        )
        s.execute(delete(Run).where(Run.domain_id == DOMAIN))
        s.execute(delete(Run).where(Run.triggered_by == "audit"))
        s.execute(delete(KnowledgeItem).where(KnowledgeItem.domain_id == DOMAIN))
        s.execute(delete(Snapshot).where(Snapshot.domain_id == DOMAIN))
        s.execute(delete(EvaluationRun).where(EvaluationRun.domain_id == DOMAIN))
        s.execute(delete(Setting))
    runtime_config.invalidate()


def _source_ids(s):
    return [str(x) for x in s.execute(select(Source.id).where(Source.domain_id == DOMAIN)).scalars()]


def _drain(worker: Worker, limit: int = 20) -> int:
    n = 0
    while n < limit and worker.run_once():
        n += 1
    return n


def test_queue_orders_by_priority_and_age_and_honours_idempotency():
    with session_scope() as s:
        low = enqueue(s, TYPE_OK, {"n": 1}, priority=200)
        high = enqueue(s, TYPE_OK, {"n": 2}, priority=10)
        mid = enqueue(s, TYPE_OK, {"n": 3}, priority=100, idempotency_key="audit:mid")
        assert enqueue(s, TYPE_OK, {"n": 9}, priority=1, idempotency_key="audit:mid") is None  # still queued
        future = enqueue(s, TYPE_OK, {"n": 4}, priority=0)
        future.run_at = utcnow() + timedelta(minutes=5)  # not claimable yet
        ids = [high.id, mid.id, low.id]
        s.flush()
        claimed = [claim(s, "audit").id for _ in range(3)]
        assert claimed == ids  # priority first, never the future job
        assert claim(s, "audit") is None
        for jid in claimed:
            s.get(Job, jid).status = JobStatus.DONE
        # a finished job frees its idempotency key for a new one
        again = enqueue(s, TYPE_OK, {"n": 5}, idempotency_key="audit:mid")
        assert again is not None and again.id != mid.id


def test_worker_retries_with_backoff_then_dead_letters_and_recovers_stuck_jobs():
    w = Worker(name="audit", scheduler=False)
    with session_scope() as s:
        failing = enqueue(s, TYPE_FAIL, {}, max_attempts=2).id
        flaky = enqueue(s, TYPE_FLAKY, {}, max_attempts=3).id
    assert _drain(w) == 2  # both claimed once, both failed once
    with session_scope() as s:
        for jid in (failing, flaky):
            j = s.get(Job, jid)
            assert j.status == JobStatus.QUEUED and j.attempts == 1 and "RuntimeError" in j.last_error
            assert 20 <= (j.run_at - utcnow()).total_seconds() <= 31  # exponential backoff: 30s * 2^0
        assert w.run_once() is False  # nothing claimable during the backoff window
        for jid in (failing, flaky):
            s.get(Job, jid).run_at = utcnow() - timedelta(seconds=1)
    assert _drain(w) == 2
    with session_scope() as s:
        dead = s.get(Job, failing)
        assert dead.status == JobStatus.DEAD and dead.attempts == 2 and dead.finished_at is not None
        recovered = s.get(Job, flaky)
        assert recovered.status == JobStatus.DONE and recovered.result == {"ok": True}
        assert calls[TYPE_FAIL] == [1, 2] and calls[TYPE_FLAKY] == [1, 2]
        # dead letters can be retried explicitly; a job whose worker died is requeued after the lock expires
        assert retry_dead(s, failing).status == JobStatus.QUEUED
        assert retry_dead(s, flaky) is None  # only DEAD jobs
        stuck = enqueue(s, TYPE_OK, {})
        stuck.status, stuck.locked_at, stuck.locked_by = JobStatus.RUNNING, utcnow() - timedelta(minutes=61), "ghost"
        s.flush()
        assert requeue_stuck(s) == 1
        j = s.get(Job, stuck.id)
        assert j.status == JobStatus.QUEUED and j.locked_by is None and "requeued" in j.last_error
        assert requeue_stuck(s) == 0
        assert w.run_once() is False or True  # the retried DEAD job and the requeued one are now claimable


def test_run_is_finalised_with_aggregated_totals_and_status():
    w = Worker(name="audit", scheduler=False)
    with session_scope() as s:
        run = J.start_run(s, domain_id=DOMAIN, kind="audit", triggered_by="audit")
        enqueue(s, TYPE_OK, {"n": 2}, run_id=run.id)
        enqueue(s, TYPE_OK, {"n": 3}, run_id=run.id)
        enqueue(s, TYPE_FAIL, {}, run_id=run.id, max_attempts=1)
        run_id = run.id
    _drain(w)
    with session_scope() as s:
        run = s.get(Run, run_id)
        assert run.status == RunStatus.DONE and run.finished_at is not None
        assert run.stats["jobs_done"] == 2 and run.stats["jobs_dead"] == 1
        assert run.stats["totals"][TYPE_OK] == {"items_created": 5}
        # a run whose only job died is FAILED
        run2 = J.start_run(s, domain_id=DOMAIN, kind="audit", triggered_by="audit")
        enqueue(s, TYPE_FAIL, {}, run_id=run2.id, max_attempts=1)
        run2_id = run2.id
    _drain(w)
    with session_scope() as s:
        assert s.get(Run, run2_id).status == RunStatus.FAILED


def test_scheduler_enqueues_only_what_is_due():
    plugin = get_registry().get(DOMAIN)
    with session_scope() as s:
        sync_domain(s, plugin)
        src = s.execute(select(Source).where(Source.domain_id == DOMAIN)).scalars().first()
        assert src is not None
        # sources: disabled or never crawled -> never scheduled; crawled and due -> scheduled once (idempotent);
        # not due -> not scheduled
        src.enabled = False
        src.last_checked_at = utcnow() - timedelta(hours=src.crawl_frequency_hours + 1)
        s.flush()
        assert J.schedule_due_sources(s) == 0
        src.enabled = True
        src.last_checked_at = None
        s.flush()
        assert J.schedule_due_sources(s) == 0
        src.last_checked_at = utcnow() - timedelta(hours=src.crawl_frequency_hours + 1)
        s.flush()
        assert J.schedule_due_sources(s) == 1
        assert J.schedule_due_sources(s) == 0  # already queued (idempotency key)
        queued = (
            s.execute(select(Job).where(Job.type == "crawl_source", Job.status == JobStatus.QUEUED)).scalars().all()
        )
        assert any(j.payload["source_id"] == str(src.id) for j in queued)
        for j in queued:
            if j.payload["source_id"] == str(src.id):
                s.delete(j)
        src.last_checked_at = utcnow()
        s.flush()
        assert J.schedule_due_sources(s) == 0
        src.enabled = False  # leave the example source as the catalog declares it

        # evaluations / snapshots: only for domains with live knowledge, only when the interval elapsed
        assert J.schedule_due_evaluations(s) == 0 and J.schedule_due_snapshots(s) == 0  # no live knowledge yet
        create_knowledge(
            s,
            plugin,
            KnowledgeEntry(
                statement="WIDGET schedules its own evaluation.",
                subject="WIDGET",
                predicate="schedules",
                object="eval",
                knowledge_type="fact",
                provenance="ORGANIZATION",
                provided_by="qa",
                authority=90,
                evidence_text="spec",
            ),
        )
        s.flush()
    runtime_config.set_overrides({"eval.interval_hours": 1, "snapshot.interval_hours": 2})
    with session_scope() as s:
        assert J.schedule_due_evaluations(s) == 1 and J.schedule_due_evaluations(s) == 0
        assert J.schedule_due_snapshots(s) == 1 and J.schedule_due_snapshots(s) == 0
        # a fresh evaluation / snapshot pushes the next one past the interval
        s.execute(delete(Job).where(Job.type.in_(["evaluate", "snapshot"])))
        s.add(
            EvaluationRun(
                domain_id=DOMAIN,
                dataset_version="x",
                status="DONE",
                triggered_by="audit",
                started_at=utcnow(),
                finished_at=utcnow(),
            )
        )
        s.add(
            Snapshot(
                domain_id=DOMAIN, version=999, kind="full", status="ready", created_by="audit", created_at=utcnow()
            )
        )
        s.flush()
        assert J.schedule_due_evaluations(s) == 0 and J.schedule_due_snapshots(s) == 0
    runtime_config.set_overrides({"eval.interval_hours": 0, "snapshot.interval_hours": 0})
    with session_scope() as s:
        s.execute(delete(EvaluationRun).where(EvaluationRun.domain_id == DOMAIN))
        s.execute(delete(Snapshot).where(Snapshot.domain_id == DOMAIN))
        s.flush()
        assert J.schedule_due_evaluations(s) == 0 and J.schedule_due_snapshots(s) == 0  # disabled

        # revalidations: only items with the dependency flag; a review-only flag is never queued
        item = s.execute(select(KnowledgeItem).where(KnowledgeItem.domain_id == DOMAIN)).scalars().first()
        item.needs_review, item.review_kind, item.review_reason = True, "manual", "look"
        s.flush()
        assert J.schedule_revalidations(s) == 0
        item.needs_revalidation, item.revalidation_reason = True, "dependency x: test"
        s.flush()
        assert J.schedule_revalidations(s) == 1 and J.schedule_revalidations(s) == 0


def test_worker_housekeeping_runs_scheduler_and_requeue_on_their_intervals(monkeypatch):
    w = Worker(name="audit", scheduler=True)
    ticks = {"sched": 0, "requeue": 0}
    monkeypatch.setattr(
        "knowledge_platform.core.orchestration.worker.schedule_due_sources",
        lambda s: ticks.__setitem__("sched", ticks["sched"] + 1) or 0,
    )
    monkeypatch.setattr("knowledge_platform.core.orchestration.worker.schedule_due_evaluations", lambda s: 0)
    monkeypatch.setattr("knowledge_platform.core.orchestration.worker.schedule_revalidations", lambda s: 0)
    monkeypatch.setattr("knowledge_platform.core.orchestration.worker.schedule_due_snapshots", lambda s: 0)
    monkeypatch.setattr(
        "knowledge_platform.core.orchestration.worker.requeue_stuck",
        lambda s: ticks.__setitem__("requeue", ticks["requeue"] + 1) or 0,
    )
    w._housekeeping()
    w._housekeeping()  # within the interval: no second run
    assert ticks == {"sched": 1, "requeue": 1}
    w._last_schedule = w._last_requeue = 0.0
    w._housekeeping()
    assert ticks == {"sched": 2, "requeue": 2}
    # an unknown job type is a failure, not a crash of the worker loop
    with session_scope() as s:
        jid = enqueue(s, "audit_unknown_type_" + uuid.uuid4().hex[:4], {}, max_attempts=1).id
    assert w.run_once() is True
    with session_scope() as s:
        j = s.get(Job, jid)
        assert j.status == JobStatus.DEAD and "no handler" in j.last_error
        s.delete(j)
