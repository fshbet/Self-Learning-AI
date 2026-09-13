"""USER / ORGANIZATION knowledge entry and provenance flow (needs PostgreSQL)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from tests.conftest import requires_db

from knowledge_platform import adapters
from knowledge_platform.api.app import app
from knowledge_platform.core.domains import sync_domain
from knowledge_platform.core.plugins.registry import get_registry
from knowledge_platform.db import session_scope
from knowledge_platform.models import KnowledgeItem

pytestmark = requires_db
client = TestClient(app)
DOMAIN = "example"


class _Emb:
    name, model, dimension = "fake", "fake", 768

    @property
    def identity(self):
        return "fake:fake:768"

    def embed(self, texts):
        return [[(i % 7) / 7.0 for i in range(768)] for _ in texts]

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


def _body(**over):
    base = {
        "domain": DOMAIN,
        "statement": "Our organisation requires every report to use the corporate date table.",
        "subject": "Corporate date table",
        "predicate": "is required by",
        "object": "every report",
        "knowledge_type": "best_practice",
        "topic": "How-to/Configuration",
        "provenance": "ORGANIZATION",
        "evidence_text": "Data governance standard v3, section 2.1",
        "evidence_url": "https://intranet.example/standards/v3",
        "provided_by": "data-governance",
        "authority": 90,
    }
    base.update(over)
    return base


def test_organization_knowledge_keeps_provenance_and_is_scored():
    r = client.post("/api/knowledge", json=_body())
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["provenance"] == "ORGANIZATION" and item["origin"] == "DIRECT" and item["polarity"] == "positive"
    assert item["status"] in ("SUPPORTED", "VERIFIED") and item["verification_level"] >= 1
    assert item["evidence"][0]["evidence_type"] == "human" and item["evidence"][0]["details"]["provided"] is True
    assert item["quality_factors"]["freshness"] == 1.0 and item["quality_factors"]["rule_version"] == "score@2.0"
    assert item["evidence_count"] == 0  # human-provided evidence is not an extraction citation
    # the item is distinguishable in listings
    listed = client.get(f"/api/knowledge?domain={DOMAIN}&provenance=ORGANIZATION").json()
    assert any(k["id"] == item["id"] for k in listed["items"])
    assert client.get(f"/api/knowledge?domain={DOMAIN}&provenance=OFFICIAL").json()["total"] == 0
    # exact duplicate is refused
    assert client.post("/api/knowledge", json=_body()).status_code == 409
    # validation
    assert client.post("/api/knowledge", json=_body(provenance="OFFICIAL")).status_code == 422
    assert (
        client.post(
            "/api/knowledge", json=_body(knowledge_type="nope", statement="Different statement here.")
        ).status_code
        == 422
    )


def test_negative_user_knowledge_and_export_provenance():
    r = client.post(
        "/api/knowledge",
        json=_body(
            statement="Do not schedule refreshes during the 02:00 maintenance window; they fail.",
            subject="Scheduled refresh",
            predicate="fails during",
            object="the 02:00 maintenance window",
            knowledge_type="limitation",
            provenance="USER",
            details={"condition": "02:00-03:00 maintenance window"},
            provided_by="alice",
            authority=50,
        ),
    )
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["polarity"] == "negative" and item["provenance"] == "USER"
    assert item["details"] == {"condition": "02:00-03:00 maintenance window"}
    with session_scope() as s:
        from knowledge_platform.core.export.snapshot import gather, run_gate

        plugin = get_registry().get(DOMAIN)
        data = gather(s, plugin)
        run_gate(data)  # human-provided evidence satisfies the provenance gate
        recs = {k.id: k for k in data["knowledge"]}
        assert recs[item["id"]].provenance == "USER" and recs[item["id"]].polarity == "negative"
        assert any(n.id == item["id"] for n in data["negative"])
        ev = [e for e in data["evidence"] if e.knowledge_item_id == item["id"]][0]
        assert ev.evidence_type == "human" and ev.relation == "supports" and ev.retrieved_at
        assert s.execute(select(KnowledgeItem).where(KnowledgeItem.id == item["id"])).scalar_one().origin == "DIRECT"
