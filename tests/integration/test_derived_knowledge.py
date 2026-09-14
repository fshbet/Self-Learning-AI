"""DERIVED / SYNTHESIZED knowledge (audit P1.3): explicit chain, no fabricated quote, follows its premises."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from tests.conftest import requires_db

from knowledge_platform import adapters
from knowledge_platform.api.app import app
from knowledge_platform.core.domains import sync_domain
from knowledge_platform.core.export.render import ai_record
from knowledge_platform.core.export.snapshot import gather, run_gate
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


def _fact(subject, statement, obj, **kw):
    return KnowledgeEntry(
        statement=statement,
        subject=subject,
        predicate=kw.pop("predicate", "has"),
        object=obj,
        knowledge_type="fact",
        provenance="ORGANIZATION",
        provided_by="qa",
        authority=90,
        evidence_text="internal spec",
        **kw,
    )


def test_derived_item_keeps_its_chain_and_follows_its_premises():
    plugin = get_registry().get(DOMAIN)
    with session_scope() as s:
        a = create_knowledge(s, plugin, _fact("WIDGET", "WIDGET needs 12 V to start.", "12 V requirement"))
        b = create_knowledge(s, plugin, _fact("BATTERY", "BATTERY supplies 9 V.", "9 V output", predicate="supplies"))
        a_id, b_id = a.id, b.id
        a_conf = float(a.confidence)
        # validation: no premises / no rationale / wrong origin are refused, nothing fabricated
        with pytest.raises(ValueError):
            create_knowledge(s, plugin, _fact("X", "WIDGET cannot start from BATTERY alone.", "x", origin="DERIVED"))
        with pytest.raises(ValueError):
            create_knowledge(
                s,
                plugin,
                _fact(
                    "X",
                    "WIDGET cannot start from BATTERY alone.",
                    "x",
                    origin="SYNTHESIZED",
                    derived_from=[a_id],
                    rationale="r",
                ),
            )
        with pytest.raises(ValueError):
            create_knowledge(
                s,
                plugin,
                _fact("X", "WIDGET cannot start from BATTERY alone.", "x", origin="DERIVED", derived_from=[a_id]),
            )
        d = create_knowledge(
            s,
            plugin,
            _fact(
                "WIDGET",
                "WIDGET cannot start from BATTERY alone.",
                "no battery start",
                predicate="cannot start from",
                origin="SYNTHESIZED",
                derived_from=[a_id, b_id],
                rationale="12 V is required and the battery supplies only 9 V.",
            ),
        )
        d_id = d.id
        assert d.origin == "SYNTHESIZED" and d.provenance == "DERIVED"
        assert d.status == "SUPPORTED" and d.confidence == round(min(a_conf, float(b.confidence)) * 0.9, 4)
        assert d.verification_level <= 3 and d.quality_factors["derivation"]["premises"] == 2
        ev = [e for e in d.evidence if e.evidence_type == "derivation"]
        assert len(ev) == 1 and not ev[0].verified and "9 V" in ev[0].excerpt
        assert not any(e.evidence_type == "extraction" for e in d.evidence)  # no fabricated quote
        rels = s.execute(select(KnowledgeRelation).where(KnowledgeRelation.from_item_id == d_id)).scalars().all()
        assert {(r.relation_type, r.to_item_id) for r in rels} >= {("derived_from", a_id), ("derived_from", b_id)}

        # export: the gate accepts it (chain present) and the AI record names its premises
        data = gather(s, plugin)
        assert run_gate(data)["provenance_ok"]
        rec = next(k for k in data["knowledge"] if k.id == str(d_id))
        assert rec.origin == "SYNTHESIZED" and rec.provenance == "DERIVED"
        assert {x["item_id"] for x in rec.dependencies if x["relation"] == "derived_from"} == {str(a_id), str(b_id)}
        ai = ai_record(rec, [], [], premises=["WIDGET needs 12 V to start.", "BATTERY supplies 9 V."])
        assert "Synthesized from (synthesized knowledge, no verbatim source of its own):" in ai["text"]
        assert "- BATTERY supplies 9 V." in ai["text"] and ai["citations"] == [] and ai["usage"] == "cite"

    # a premise goes stale -> the conclusion is flagged, revalidation demotes it and keeps the flag
    assert (
        client.post(f"/api/knowledge/{a_id}/review", json={"action": "stale", "reason": "spec changed"}).status_code
        == 200
    )
    with session_scope() as s:
        d = s.get(KnowledgeItem, d_id)
        assert d.needs_revalidation and "became STALE" in d.revalidation_reason
        out = revalidate_item_job(s, Job(type="revalidate_item", payload={"item_id": str(d_id)}))
        assert out["unresolved_dependencies"] == 1
        d = s.get(KnowledgeItem, d_id)
        assert d.status == "STALE" and d.needs_revalidation and d.quality_factors["derivation"]["premises_live"] == 1

    # the premise is restored -> revalidation brings the conclusion back and clears the dependency flag
    assert (
        client.post(f"/api/knowledge/{a_id}/review", json={"action": "approve", "reason": "restored"}).status_code
        == 200
    )
    with session_scope() as s:
        out = revalidate_item_job(s, Job(type="revalidate_item", payload={"item_id": str(d_id)}))
        assert out["unresolved_dependencies"] == 0
        d = s.get(KnowledgeItem, d_id)
        a, b = s.get(KnowledgeItem, a_id), s.get(KnowledgeItem, b_id)
        assert d.status == "SUPPORTED" and not d.needs_revalidation, (
            d.status,
            d.quality_factors,
            a.status,
            a.verification_level,
            b.status,
            b.verification_level,
        )


def test_api_creates_derived_knowledge_and_rejects_missing_chain():
    plugin = get_registry().get(DOMAIN)
    with session_scope() as s:
        a = create_knowledge(s, plugin, _fact("GADGET", "GADGET ships with a manual.", "manual"))
        a_id = str(a.id)
    body = {
        "domain": DOMAIN,
        "statement": "GADGET users can consult the manual for setup.",
        "subject": "GADGET",
        "predicate": "can be set up with",
        "object": "the manual",
        "origin": "DERIVED",
        "rationale": "a shipped manual can be consulted",
        "derived_from": [a_id],
    }
    r = client.post("/api/knowledge", json=body)
    assert r.status_code == 201, r.text
    assert r.json()["origin"] == "DERIVED" and r.json()["provenance"] == "DERIVED"
    assert [x["relation_type"] for x in r.json()["relations"]["outgoing"]] == ["derived_from"]
    assert (
        client.post(
            "/api/knowledge", json={**body, "statement": "GADGET manual is optional reading.", "derived_from": []}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/knowledge", json={**body, "statement": "GADGET manual is optional reading.", "rationale": ""}
        ).status_code
        == 422
    )
