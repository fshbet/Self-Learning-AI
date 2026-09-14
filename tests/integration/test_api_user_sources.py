"""User-defined URLs and discovery keywords via the HTTP API (needs PostgreSQL)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from tests.conftest import requires_db

from knowledge_platform.api.app import app
from knowledge_platform.core.domains import sync_domain
from knowledge_platform.core.plugins.registry import get_registry
from knowledge_platform.db import session_scope
from knowledge_platform.models import DomainKeyword, Source, utcnow

pytestmark = requires_db

# TestClient without a `with` block does not run the lifespan, so no embedded worker thread starts.
client = TestClient(app)
DOMAIN = "example"


@pytest.fixture(autouse=True)
def synced_domain():
    with session_scope() as s:
        sync_domain(s, get_registry().get(DOMAIN))
    yield
    with session_scope() as s:
        s.execute(delete(DomainKeyword).where(DomainKeyword.domain_id == DOMAIN))
        s.execute(delete(Source).where(Source.domain_id == DOMAIN, Source.origin != "plugin"))


def test_keywords_crud_and_dedup():
    r = client.post(f"/api/domains/{DOMAIN}/keywords", json={"keyword": "  example   tutorial  "})
    assert r.status_code == 201, r.text
    kw = r.json()
    assert kw["keyword"] == "example tutorial"
    # case-insensitive duplicate re-uses the existing row
    r2 = client.post(f"/api/domains/{DOMAIN}/keywords", json={"keyword": "Example Tutorial"})
    assert r2.status_code == 201 and r2.json()["id"] == kw["id"]
    assert [k["keyword"] for k in client.get(f"/api/domains/{DOMAIN}/keywords").json()] == ["example tutorial"]
    assert any(k["id"] == kw["id"] for k in client.get(f"/api/domains/{DOMAIN}").json()["keywords"])
    assert client.delete(f"/api/domains/{DOMAIN}/keywords/{kw['id']}").status_code == 204
    assert client.get(f"/api/domains/{DOMAIN}/keywords").json() == []
    assert client.delete(f"/api/domains/{DOMAIN}/keywords/{uuid.uuid4()}").status_code == 404


def test_user_source_lifecycle_and_plugin_protection():
    url = "https://docs.example.org/guide/?utm_source=x"
    r = client.post("/api/sources", json={"domain": DOMAIN, "url": url, "authority": 70, "max_pages": 5})
    assert r.status_code == 201, r.text
    src = r.json()
    assert src["origin"] == "user" and src["url"] == "https://docs.example.org/guide/"
    assert src["name"] == "docs.example.org" and src["status"] == "ACTIVE"
    # same URL again (even with tracking params) is a conflict
    assert client.post("/api/sources", json={"domain": DOMAIN, "url": url}).status_code == 409
    # bad input
    assert client.post("/api/sources", json={"domain": DOMAIN, "url": "ftp://x"}).status_code == 422
    # SSRF guard: internal addresses are refused at registration
    for bad in ("http://127.0.0.1:5433/", "http://localhost:8010/api/settings", "http://169.254.169.254/"):
        r = client.post("/api/sources", json={"domain": DOMAIN, "url": bad})
        assert r.status_code == 422 and "cannot be crawled" in r.text, bad
    # per-source cadence, class and scope are editable
    r = client.patch(
        f"/api/sources/{src['id']}",
        json={"crawl_frequency_hours": 6, "source_class": "community", "authority": 40, "max_depth": 0},
    )
    assert r.status_code == 200 and r.json()["crawl_frequency_hours"] == 6
    assert r.json()["source_class"] == "community" and r.json()["authority"] == 40
    assert client.post("/api/sources", json={"domain": "nope", "url": "https://x.test/"}).status_code == 404
    # plugin sync must not remove or alter the user source
    with session_scope() as s:
        sync_domain(s, get_registry().get(DOMAIN))
    listed = {x["id"]: x for x in client.get(f"/api/sources?domain={DOMAIN}").json()}
    assert listed[src["id"]]["origin"] == "user"
    # plugin-defined sources cannot be deleted, user ones can
    plugin_src = next(x for x in listed.values() if x["origin"] == "plugin")
    assert client.delete(f"/api/sources/{plugin_src['id']}").status_code == 409
    assert client.delete(f"/api/sources/{src['id']}").status_code == 204
    assert src["id"] not in {x["id"] for x in client.get(f"/api/sources?domain={DOMAIN}").json()}


def test_sync_reclassifies_items_when_a_source_class_is_curated(monkeypatch):
    """Curating a catalog class re-derives provenance of the items that source evidences (audit P1.8)."""
    from knowledge_platform.models import Evidence, KnowledgeItem, Source

    plugin = get_registry().get(DOMAIN)
    spec = plugin.sources()[0]
    with session_scope() as s:
        sync_domain(s, plugin)
        src = s.execute(select(Source).where(Source.domain_id == DOMAIN, Source.key == spec.key)).scalar_one()
        src.source_class = "external"
        item = KnowledgeItem(
            domain_id=DOMAIN,
            knowledge_type="fact",
            subject="S",
            predicate="p",
            object="o",
            statement="S p o (class test).",
            status="SUPPORTED",
            content_hash="sha256:classtest",
            provenance="EXTERNAL",
            first_discovered_at=utcnow(),
        )
        s.add(item)
        s.flush()
        s.add(
            Evidence(
                knowledge_item_id=item.id, source_id=src.id, evidence_type="extraction", excerpt="q", verified=True
            )
        )
        item_id = item.id
    try:
        curated = spec.model_copy(update={"source_class": "official"})
        monkeypatch.setattr(plugin, "sources", lambda: [curated])
        with session_scope() as s:
            out = sync_domain(s, plugin)
            assert out["items_reclassified"] == 1
            assert s.get(KnowledgeItem, item_id).provenance == "OFFICIAL"
            assert s.execute(select(Source).where(Source.id == src.id)).scalar_one().source_class == "official"
    finally:
        with session_scope() as s:
            s.execute(delete(KnowledgeItem).where(KnowledgeItem.id == item_id))
        with session_scope() as s:
            sync_domain(s, plugin)
