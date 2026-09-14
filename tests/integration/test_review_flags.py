"""Review flags survive dependency revalidation and the scheduler; only a reviewer clears them (audit P0.2)."""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from tests.conftest import requires_db

from knowledge_platform import adapters
from knowledge_platform.api.app import app
from knowledge_platform.core.collection.fetcher import Fetcher
from knowledge_platform.core.domains import sync_domain
from knowledge_platform.core.export.render import ai_record
from knowledge_platform.core.export.snapshot import gather
from knowledge_platform.core.knowledge_entry import KnowledgeEntry, create_knowledge
from knowledge_platform.core.orchestration.jobs import enqueue_revalidations, revalidate_item_job
from knowledge_platform.core.plugins.registry import get_registry
from knowledge_platform.core.verification import falsify
from knowledge_platform.core.verification.review_flags import flag_for_review, resolve_review
from knowledge_platform.db import session_scope
from knowledge_platform.models import Job, KnowledgeItem

pytestmark = requires_db
client = TestClient(app)
DOMAIN = "example"


class _Emb:
    name, model, dimension = "fake", "fake", 768
    identity = "fake:fake:768"

    def embed(self, texts):
        return [[((hash(t) >> (i % 32)) & 1) * 0.9 for i in range(768)] for t in texts]

    def embed_one(self, text):
        return self.embed([text])[0]


class _Hit:
    def __init__(self, url):
        self.url, self.title, self.snippet = url, "t", ""


class _Search:
    name = "fake"

    def search(self, query, *, limit=10):
        return [_Hit("https://blog.test/counter")]


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    emb = _Emb()
    monkeypatch.setattr(adapters, "get_embedder", lambda: emb)
    monkeypatch.setattr("knowledge_platform.core.retrieval.embeddings.get_embedder", lambda: emb)
    monkeypatch.setattr("knowledge_platform.core.retrieval.search.get_embedder", lambda: emb)
    monkeypatch.setattr(falsify, "get_search", lambda: _Search())
    monkeypatch.setattr(
        falsify,
        "call_json",
        lambda **kw: {
            "verdict": "contradicts",
            "quote": "WIDGET never starts on its own.",
            "rationale": "page says otherwise",
        },
    )
    with session_scope() as s:
        sync_domain(s, get_registry().get(DOMAIN))
    yield
    with session_scope() as s:
        s.execute(delete(KnowledgeItem).where(KnowledgeItem.domain_id == DOMAIN))
        s.execute(delete(Job).where(Job.type.in_(["revalidate_item", "falsify_item"])))


def _entry(kind, statement, obj):
    return KnowledgeEntry(
        statement=statement,
        subject="WIDGET",
        predicate="has",
        object=obj,
        knowledge_type=kind,
        provenance="ORGANIZATION",
        provided_by="qa",
        authority=90,
        evidence_text="internal spec",
    )


@respx.mock
def test_falsification_flag_survives_revalidation_and_scheduler_until_resolved():
    respx.get("https://blog.test/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://blog.test/counter").mock(
        return_value=httpx.Response(
            200, text="<p>WIDGET never starts on its own.</p>", headers={"Content-Type": "text/html"}
        )
    )
    plugin = get_registry().get(DOMAIN)
    with session_scope() as s:
        fact = create_knowledge(s, plugin, _entry("definition", "WIDGET is the core component.", "core role"))
        example = create_knowledge(
            s, plugin, _entry("example", "WIDGET example: widget.run() starts it.", "run example")
        )
        fact_id, example_id = fact.id, example.id
        # 1. falsification finds counter-evidence -> review flag, not the dependency flag
        out = falsify.falsify_item(s, plugin, example, fetcher=Fetcher(default_delay=0, max_retries=1))
        assert out["contradictions"] == 1
        assert example.needs_review and example.review_kind == "falsification"
        assert "blog.test" in example.review_reason and example.review_flagged_at is not None
        assert not example.needs_revalidation
        assert any(e.evidence_type == "falsification" and e.relation == "contradicts" for e in example.evidence)

    # 2. a dependency change flags revalidation on top of the review flag
    r = client.post(f"/api/knowledge/{fact_id}/review", json={"action": "stale", "reason": "spec withdrawn"})
    assert r.status_code == 200
    with session_scope() as s:
        ex = s.get(KnowledgeItem, example_id)
        assert ex.needs_revalidation and ex.needs_review

    # 3. the foundation is restored; the scheduler's revalidation succeeds
    r = client.post(f"/api/knowledge/{fact_id}/review", json={"action": "approve", "reason": "restored"})
    assert r.status_code == 200
    with session_scope() as s:
        assert enqueue_revalidations(s, DOMAIN) == 1  # what the scheduler queues
        out = revalidate_item_job(s, Job(type="revalidate_item", payload={"item_id": str(example_id)}))
        assert out["unresolved_dependencies"] == 0
        ex = s.get(KnowledgeItem, example_id)
        assert not ex.needs_revalidation and ex.revalidation_reason is None
        # the review flag, its reason and the counter-evidence are untouched
        assert ex.needs_review and ex.review_kind == "falsification" and "blog.test" in ex.review_reason
        assert any(e.evidence_type == "falsification" for e in ex.evidence)
        # a review-only flag is never queued for revalidation: the scheduler has no business with it
        assert enqueue_revalidations(s, DOMAIN) == 0

    # 4. the export carries the trust state: the canonical record and the AI record both say "caution"
    with session_scope() as s:
        data = gather(s, plugin)
        rec = next(k for k in data["knowledge"] if k.id == str(example_id))
        assert rec.needs_review and rec.review_kind == "falsification" and "blog.test" in rec.review_reason
        assert rec.review_flagged_at and not rec.needs_revalidation
        assert rec.evidence_status["contradicting"] == 1 and rec.evidence_status["verified"] >= 1
        ai = ai_record(rec, [e for e in data["evidence"] if e.knowledge_item_id == rec.id], [])
        assert ai["usage"] == "caution" and ai["text"].startswith("Caution: flagged for review (falsification)")
        assert ai["needs_review"] and ai["evidence_status"]["contradicting"] == 1
        untouched = next(k for k in data["knowledge"] if k.id == str(fact_id))
        assert not untouched.needs_review and ai_record(untouched, [], [])["usage"] == "cite"

    # 5. the flag is visible to the API/UI and counted
    listed = client.get(f"/api/knowledge?domain={DOMAIN}&needs_review=true").json()
    assert [k["id"] for k in listed["items"]] == [str(example_id)]
    assert listed["items"][0]["review_kind"] == "falsification"
    assert client.get(f"/api/stats?domain={DOMAIN}").json()["needs_review"] == 1

    # 6. only an explicit reviewer decision clears it; the decision is logged and the evidence kept
    r = client.post(
        f"/api/knowledge/{example_id}/review",
        json={"action": "dismiss", "reason": "counter-evidence refers to an older release", "reviewer": "alice"},
    )
    assert r.status_code == 200
    body = r.json()
    assert not body["needs_review"] and body["review_reason"] is None
    log = body["details"]["review_log"]
    assert log[-1]["kind"] == "falsification" and log[-1]["resolved_by"] == "alice"
    assert log[-1]["action"] == "dismiss" and "older release" in log[-1]["resolution"]
    assert "blog.test" in log[-1]["reason"]
    assert any(e["evidence_type"] == "falsification" for e in body["evidence"])
    assert body["status"] == "VERIFIED"  # dismiss changes nothing else
    assert client.get(f"/api/knowledge?domain={DOMAIN}&needs_review=true").json()["total"] == 0


def test_review_actions_resolve_and_flag_helpers_validate():
    plugin = get_registry().get(DOMAIN)
    with session_scope() as s:
        item = create_knowledge(s, plugin, _entry("fact", "WIDGET ships with a manual.", "manual"))
        with pytest.raises(ValueError):
            flag_for_review(item, "nope", "x")
        flag_for_review(item, "manual", "please double-check the manual claim")
        first = item.review_flagged_at
        flag_for_review(item, "quality", "second concern")  # refresh keeps the first flag time
        assert item.review_flagged_at == first and item.review_kind == "quality"
        assert resolve_review(item, resolved_by="bob", resolution="ok", action="dismiss")["kind"] == "quality"
        assert resolve_review(item, resolved_by="bob", resolution="ok", action="dismiss") is None
        flag_for_review(item, "manual", "again")
        item_id = item.id
    # approve/reject/stale are explicit decisions too: they resolve the flag and record it
    r = client.post(
        f"/api/knowledge/{item_id}/review", json={"action": "approve", "reason": "checked", "reviewer": "bob"}
    )
    assert r.status_code == 200 and not r.json()["needs_review"]
    assert r.json()["details"]["review_log"][-1]["action"] == "approve"
    with session_scope() as s:
        it = s.execute(select(KnowledgeItem).where(KnowledgeItem.id == item_id)).scalar_one()
        assert it.status == "VERIFIED" and len(it.details["review_log"]) == 2


def test_scheduler_does_not_churn_on_revalidations_a_reviewer_must_unblock(monkeypatch):
    """P2.0 long run: dependents of a CONFLICTED item were revalidated every scheduler tick for hours with the
    same 'unresolved dependencies' result. A repeat is only queued when the item or a dependency changed since
    the last revalidation — or when a person asks explicitly."""
    from knowledge_platform.core.versioning.dependencies import add_relation
    from knowledge_platform.core.versioning.lifecycle import transition
    from knowledge_platform.models import ItemStatus, utcnow

    monkeypatch.setattr("knowledge_platform.core.verification.conflicts.judge", lambda *a, **k: ("compatible", "", ""))
    plugin = get_registry().get(DOMAIN)
    with session_scope() as s:
        base = create_knowledge(s, plugin, _entry("definition", "WIDGET has a 12 V supply.", "12 V supply"))
        dep = create_knowledge(s, plugin, _entry("procedure", "WIDGET has a start sequence.", "start sequence"))
        add_relation(s, dep, base, "depends_on", origin="user")
        transition(s, base, ItemStatus.CONFLICTED, reason="another source disagrees", actor="test", force=True)
        assert dep.needs_revalidation
        base_id, dep_id = base.id, dep.id
    with session_scope() as s:
        assert enqueue_revalidations(s, DOMAIN) == 1  # first time: worth a look
        job = s.execute(select(Job).where(Job.type == "revalidate_item", Job.status == "QUEUED")).scalars().one()
        job.status, job.finished_at = "RUNNING", None
        job.result = revalidate_item_job(s, job)
        job.status, job.finished_at, job.idempotency_key = "DONE", utcnow(), None
        assert job.result["unresolved_dependencies"] == 1
        s.flush()
        assert s.get(KnowledgeItem, dep_id).needs_revalidation
        # nothing changed: the scheduler queues nothing again ...
        assert enqueue_revalidations(s, DOMAIN) == 0
        # ... but a person can still force it
        assert enqueue_revalidations(s, DOMAIN, force=True) == 1
        s.execute(delete(Job).where(Job.type == "revalidate_item", Job.status == "QUEUED"))
        # the dependency changed (the conflict was settled): revalidation is worthwhile again
        base = s.get(KnowledgeItem, base_id)
        transition(s, base, ItemStatus.VERIFIED, reason="reviewer settled the conflict", actor="human:bob")
        s.flush()
        assert enqueue_revalidations(s, DOMAIN) == 1
