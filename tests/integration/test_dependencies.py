"""Dependency graph: derived relations, propagation on invalidation, revalidation (needs PostgreSQL)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from tests.conftest import requires_db

from knowledge_platform import adapters
from knowledge_platform.api.app import app
from knowledge_platform.core.domains import sync_domain
from knowledge_platform.core.export.snapshot import gather
from knowledge_platform.core.knowledge_entry import KnowledgeEntry, create_knowledge
from knowledge_platform.core.orchestration.jobs import revalidate_item_job
from knowledge_platform.core.plugins.registry import get_registry
from knowledge_platform.db import session_scope
from knowledge_platform.models import Job, KnowledgeItem, KnowledgeRelation

pytestmark = requires_db
client = TestClient(app)
DOMAIN = "example"


class _Emb:
    name, model, dimension = "fake", "fake", 768
    identity = "fake:fake:768"

    def embed(self, texts):
        # distinct vectors per text so near-dedup never merges the fixtures
        return [[((hash(t) >> (i % 32)) & 1) * 0.9 for i in range(768)] for t in texts]

    def embed_one(self, text):
        return self.embed([text])[0]


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    emb = _Emb()
    monkeypatch.setattr(adapters, "get_embedder", lambda: emb)
    monkeypatch.setattr("knowledge_platform.core.retrieval.embeddings.get_embedder", lambda: emb)
    monkeypatch.setattr("knowledge_platform.core.retrieval.search.get_embedder", lambda: emb)
    with session_scope() as s:
        sync_domain(s, get_registry().get(DOMAIN))
    yield
    with session_scope() as s:
        s.execute(delete(KnowledgeItem).where(KnowledgeItem.domain_id == DOMAIN))


def _entry(kind: str, statement: str, obj: str) -> KnowledgeEntry:
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


def test_relations_propagate_and_revalidate():
    plugin = get_registry().get(DOMAIN)
    with session_scope() as s:
        fact = create_knowledge(
            s, plugin, _entry("definition", "WIDGET is the core component of the example system.", "core role")
        )
        example = create_knowledge(
            s, plugin, _entry("example", "WIDGET example: widget.run() starts the component.", "run example")
        )
        fact_id, example_id = fact.id, example.id
        rels = s.execute(select(KnowledgeRelation).where(KnowledgeRelation.from_item_id == example_id)).scalars().all()
        assert [r.relation_type for r in rels] == ["example_of"] and rels[0].to_item_id == fact_id
        assert rels[0].origin == "system" and rels[0].details["rule"]
        assert not s.get(KnowledgeItem, example_id).needs_revalidation

    # invalidate the foundation → the example is flagged
    r = client.post(f"/api/knowledge/{fact_id}/review", json={"action": "stale", "reason": "spec withdrawn"})
    assert r.status_code == 200 and r.json()["status"] == "STALE"
    with session_scope() as s:
        ex = s.get(KnowledgeItem, example_id)
        assert ex.needs_revalidation and "spec withdrawn" in ex.revalidation_reason
    listed = client.get(f"/api/knowledge?domain={DOMAIN}&needs_revalidation=true").json()
    assert [k["id"] for k in listed["items"]] == [str(example_id)]
    detail = client.get(f"/api/knowledge/{example_id}").json()
    assert detail["relations"]["outgoing"][0]["relation_type"] == "example_of"
    assert detail["relations"]["outgoing"][0]["status"] == "STALE"

    # revalidation while the dependency is still stale keeps the flag with the reason
    with session_scope() as s:
        job = Job(type="revalidate_item", payload={"item_id": str(example_id)})
        out = revalidate_item_job(s, job)
        assert out["unresolved_dependencies"] == 1
        assert s.get(KnowledgeItem, example_id).needs_revalidation

    # the foundation is approved again → revalidation clears the flag
    assert (
        client.post(
            f"/api/knowledge/{fact_id}/review", json={"action": "approve", "reason": "spec restored"}
        ).status_code
        == 200
    )
    with session_scope() as s:
        out = revalidate_item_job(s, Job(type="revalidate_item", payload={"item_id": str(example_id)}))
        assert out["unresolved_dependencies"] == 0
        assert not s.get(KnowledgeItem, example_id).needs_revalidation

    # manual relation + export
    r = client.post(
        f"/api/knowledge/{example_id}/relations", json={"to_item_id": str(fact_id), "relation_type": "related_to"}
    )
    assert r.status_code == 201 and len(r.json()["relations"]["outgoing"]) == 2
    assert (
        client.post(
            f"/api/knowledge/{example_id}/relations",
            json={"to_item_id": str(example_id), "relation_type": "related_to"},
        ).status_code
        == 422
    )
    with session_scope() as s:
        data = gather(s, plugin)
        rel_types = {r.relation_type for r in data["relationships"]}
        assert rel_types == {"example_of", "related_to"}
        rec = next(k for k in data["knowledge"] if k.id == str(example_id))
        assert {"relation": "example_of", "item_id": str(fact_id)} in rec.dependencies
    assert client.get(f"/api/stats?domain={DOMAIN}").json()["needs_revalidation"] == 0


def test_subject_matching_is_exact_not_a_like_pattern(monkeypatch):
    """Found by the P2.0 crawl: subjects such as "CU %", "Calc_method" or a trailing backslash were used as ILIKE
    patterns, so "CU %" matched "CU % Limit" (false same-subject relations / conflict candidates) and a subject
    ending in a backslash crashed extraction (LIKE pattern must not end with escape character)."""
    from knowledge_platform.core.verification.conflicts import detect_conflicts
    from knowledge_platform.core.versioning.dependencies import dependencies_of, derive_relations

    monkeypatch.setattr("knowledge_platform.core.verification.conflicts.judge", lambda *a, **k: ("contradict", "", ""))
    plugin = get_registry().get(DOMAIN)

    def fact(subject, statement, obj, kind="fact", predicate="is"):
        return create_knowledge(
            s,
            plugin,
            KnowledgeEntry(
                statement=statement,
                subject=subject,
                predicate=predicate,
                object=obj,
                knowledge_type=kind,
                provenance="ORGANIZATION",
                provided_by="qa",
                authority=90,
                evidence_text="spec",
            ),
        )

    with session_scope() as s:
        a = fact("CU %", "CU % is a capacity metric.", "a capacity metric", kind="definition")
        b = fact("CU % Limit", "CU % Limit caps usage at 100.", "a cap", kind="procedure")
        # different subjects: no structural relation and no conflict candidate between them
        assert derive_relations(s, b, plugin) == []
        assert detect_conflicts(s, b, domain_name="x") == []
        assert not [r for r, k in dependencies_of(s, b.id) if k.id == a.id]
        # a subject ending in a backslash (custom format strings) must not crash the query
        c = fact("Escape\\", "Escape\ ends a format string.", "a format string", predicate="ends")
        assert derive_relations(s, c, plugin) == [] and detect_conflicts(s, c, domain_name="x") == []
        # the same subject in different case still matches
        d = fact("cu %", "cu % is measured per second.", "per second", kind="procedure", predicate="is measured")
        assert {k.id for r, k in dependencies_of(s, d.id)} == {a.id}
