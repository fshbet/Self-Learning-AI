"""Supersede-by-new-version (P2.1): version links, dependency carry-over, revalidation, review API, retrieval."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from tests.conftest import jobs_since, requires_db

from knowledge_platform import adapters
from knowledge_platform.api.app import app
from knowledge_platform.core.domains import sync_domain
from knowledge_platform.core.export.render import ai_record
from knowledge_platform.core.export.snapshot import gather
from knowledge_platform.core.knowledge_entry import KnowledgeEntry, create_knowledge
from knowledge_platform.core.orchestration.jobs import revalidate_item_job
from knowledge_platform.core.plugins.registry import get_registry
from knowledge_platform.core.retrieval.search import hybrid_search
from knowledge_platform.core.versioning.dependencies import add_relation, dependencies_of, unresolved_dependencies
from knowledge_platform.core.versioning.lifecycle import transition
from knowledge_platform.core.versioning.supersede import supersede, version_key
from knowledge_platform.db import session_scope
from knowledge_platform.models import ItemStatus, Job, KnowledgeItem, KnowledgeRelation, utcnow

pytestmark = requires_db
T0 = utcnow()  # jobs the tests create are newer than this; cleanups never touch older ones
client = TestClient(app)
DOMAIN = "example"


class _Emb:
    name, model, dimension = "fake", "fake", 768
    identity = "fake:fake:768"

    def embed(self, texts):
        return [[((hash(t) >> (i % 32)) & 1) * 0.9 for i in range(768)] for t in texts]

    def embed_one(self, text):
        return self.embed([text])[0]


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    emb = _Emb()
    monkeypatch.setattr(adapters, "get_embedder", lambda: emb)
    monkeypatch.setattr("knowledge_platform.core.retrieval.embeddings.get_embedder", lambda: emb)
    monkeypatch.setattr("knowledge_platform.core.retrieval.search.get_embedder", lambda: emb)
    monkeypatch.setattr("knowledge_platform.core.verification.conflicts.judge", lambda *a, **k: ("compatible", "", ""))
    with session_scope() as s:
        sync_domain(s, get_registry().get(DOMAIN))
    yield
    with session_scope() as s:
        s.execute(delete(KnowledgeItem).where(KnowledgeItem.domain_id == DOMAIN))
        s.execute(delete(Job).where(Job.type == "revalidate_item", jobs_since(T0)))


def _fact(subject, statement, obj, predicate="has", **kw):
    return KnowledgeEntry(
        statement=statement,
        subject=subject,
        predicate=predicate,
        object=obj,
        knowledge_type="fact",
        provenance="ORGANIZATION",
        provided_by="qa",
        authority=90,
        evidence_text="internal spec",
        **kw,
    )


def _job(item_id: uuid.UUID) -> Job:
    return Job(type="revalidate_item", payload={"item_id": str(item_id)})


def test_supersede_links_versions_carries_dependencies_and_keeps_history():
    plugin = get_registry().get(DOMAIN)
    with session_scope() as s:
        v1 = create_knowledge(s, plugin, _fact("WIDGET", "WIDGET has a 12 V supply.", "12 V supply"))
        dependent = create_knowledge(s, plugin, _fact("WIDGET cable", "WIDGET cable carries 12 V.", "12 V"))
        add_relation(s, dependent, v1, "depends_on", origin="user")
        derived = create_knowledge(
            s,
            plugin,
            _fact(
                "WIDGET",
                "WIDGET cannot run from a 9 V battery.",
                "no 9 V battery",
                predicate="cannot run from",
                origin="DERIVED",
                derived_from=[v1.id],
                rationale="12 V is required.",
            ),
        )
        # the claim changed: a new current statement about the same subject / predicate
        transition(s, v1, ItemStatus.STALE, reason="page changed", actor="test")
        v2 = create_knowledge(s, plugin, _fact("WIDGET", "WIDGET has a 24 V supply.", "24 V supply"))
        assert version_key(v1) == version_key(v2)
        assert supersede(s, v1, v2, reason="claim changed")
        assert not supersede(s, v1, v2, reason="again")  # idempotent
        ids = {k: v.id for k, v in {"v1": v1, "v2": v2, "dep": dependent, "der": derived}.items()}

    with session_scope() as s:
        v1, v2 = s.get(KnowledgeItem, ids["v1"]), s.get(KnowledgeItem, ids["v2"])
        dep, der = s.get(KnowledgeItem, ids["dep"]), s.get(KnowledgeItem, ids["der"])
        # links and lifecycle: the old version is historical, kept with its evidence and transitions
        assert v1.status == ItemStatus.SUPERSEDED and v1.superseded_by_id == v2.id
        assert v2.previous_version_id == v1.id and v2.version == v1.version + 1
        assert v1.evidence and v1.transitions[-1].to_status == "SUPERSEDED"
        rels = s.execute(select(KnowledgeRelation).where(KnowledgeRelation.from_item_id == v2.id)).scalars().all()
        assert ("supersedes", v1.id) in {(r.relation_type, r.to_item_id) for r in rels}
        # dependencies were carried to the current version and the dependents flagged
        deps = {(r.relation_type, k.id, (r.details or {}).get("carried_from")) for r, k in dependencies_of(s, dep.id)}
        assert ("depends_on", v1.id, None) in deps and ("depends_on", v2.id, str(v1.id)) in deps
        assert dep.needs_revalidation and der.needs_revalidation
        # revalidation: the ordinary dependent settles on the live successor …
        assert unresolved_dependencies(s, dep) == []
        revalidate_item_job(s, _job(dep.id))
        assert not dep.needs_revalidation
        # … a derived conclusion does not: its premise changed, a person must re-derive it
        assert [k.id for k in unresolved_dependencies(s, der)] == [v1.id]
        revalidate_item_job(s, _job(der.id))
        assert der.needs_revalidation and "SUPERSEDED" in (der.revalidation_reason or "")
        assert der.status != ItemStatus.REJECTED
        # retrieval serves only the current version
        hits = hybrid_search(s, domain_id=DOMAIN, query="WIDGET supply", limit=10)
        found = {h.item.id for h in hits}
        assert v2.id in found and v1.id not in found
        # export: historical record names its successor; the current one is citable
        data = gather(s, plugin)
        recs = {r.id: r for r in data["knowledge"]}
        old, new = recs[str(v1.id)], recs[str(v2.id)]
        assert old.historical and old.superseded_by_id == str(v2.id) and new.previous_version_id == str(v1.id)
        ai_old = ai_record(old, [], {})
        assert ai_old["usage"] == "historical" and str(v2.id) in ai_old["text"]
        assert ai_record(new, [], {})["usage"] == "cite"

    # a reviewer re-derives the conclusion: approval settles the dependency flag explicitly
    r = client.post(
        f"/api/knowledge/{ids['der']}/review",
        json={"action": "approve", "reason": "re-derived against 24 V", "reviewer": "bob"},
    )
    assert r.status_code == 200 and r.json()["needs_revalidation"] is False


def test_review_api_supersede_action_and_guards():
    plugin = get_registry().get(DOMAIN)
    with session_scope() as s:
        old = create_knowledge(s, plugin, _fact("GADGET", "GADGET weighs 2 kg.", "2 kg", predicate="weighs"))
        new = create_knowledge(s, plugin, _fact("GADGET", "GADGET weighs 3 kg.", "3 kg", predicate="weighs"))
        other_domain = s.execute(
            select(KnowledgeItem).where(KnowledgeItem.domain_id != DOMAIN).limit(1)
        ).scalar_one_or_none()
        old_id, new_id = old.id, new.id
        other_id = other_domain.id if other_domain else None
    base = f"/api/knowledge/{old_id}/review"
    assert client.post(base, json={"action": "supersede", "reviewer": "bob"}).status_code == 422
    if other_id:
        r = client.post(base, json={"action": "supersede", "superseded_by": str(other_id), "reviewer": "bob"})
        assert r.status_code == 422
    r = client.post(base, json={"action": "supersede", "superseded_by": str(new_id), "reviewer": "bob"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "SUPERSEDED" and body["superseded_by_id"] == str(new_id)
    # a second attempt is refused, the chain stays single-linked
    r = client.post(base, json={"action": "supersede", "superseded_by": str(new_id), "reviewer": "bob"})
    assert r.status_code == 409
    with session_scope() as s:
        new, old = s.get(KnowledgeItem, new_id), s.get(KnowledgeItem, old_id)
        assert new.previous_version_id == old_id and new.version == 2
        assert old.transitions[-1].actor == "human:bob" and "superseded by" in old.transitions[-1].reason
