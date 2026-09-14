"""New-source discovery (ADR 0005, P2.8): scored, filtered candidates; never trusted or approved automatically;
recurring discovery is opt-in and adds candidates only."""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select
from tests.conftest import requires_db

from knowledge_platform.adapters.search.base import SearchHit
from knowledge_platform.core.collection.discovery import Candidate, discover, score_candidate, vocabulary_terms
from knowledge_platform.core.domains import sync_domain
from knowledge_platform.core.orchestration import jobs as jobs_mod
from knowledge_platform.core.orchestration.jobs import schedule_due_discovery
from knowledge_platform.core.plugins.registry import get_registry
from knowledge_platform.db import session_scope
from knowledge_platform.models import Job, Run, Source, SourceStatus

pytestmark = requires_db
DOMAIN = "example"


class _Search:
    name = "fake"

    def __init__(self, results: dict[str, list[SearchHit]]) -> None:
        self.results = results

    def search(self, query, *, limit=10):
        return self.results.get(query, [])[:limit]


@pytest.fixture(autouse=True)
def setup():
    with session_scope() as s:
        sync_domain(s, get_registry().get(DOMAIN))
        s.execute(delete(Source).where(Source.domain_id == DOMAIN, Source.origin == "discovered"))
    yield
    with session_scope() as s:
        s.execute(delete(Source).where(Source.domain_id == DOMAIN, Source.origin == "discovered"))
        s.execute(delete(Job).where(Job.type == "discover"))
        s.execute(delete(Run).where(Run.kind == "discover", Run.triggered_by == "scheduler"))


def test_candidates_are_scored_filtered_and_left_for_approval(monkeypatch):
    plugin = get_registry().get(DOMAIN)
    monkeypatch.setattr(plugin.manifest.discovery, "deny_hosts", ["spam.example"])
    monkeypatch.setattr(plugin.manifest.discovery, "prefer_hosts", ["docs.preferred.example"])
    with session_scope() as s:
        known = s.execute(select(Source).where(Source.domain_id == DOMAIN)).scalars().first()
        known_host = known.url.split("/")[2]
    hits = {
        "q1": [
            SearchHit(
                "Example Domain architecture concepts",
                "https://docs.preferred.example/arch",
                "installation and configuration of the example",
            ),
            SearchHit("cheap pills", "https://spam.example/buy", "unrelated"),
            SearchHit("known", f"https://{known_host}/other", "already a source"),
            SearchHit(
                "Example terminology", "https://blog.other.example/post?utm_source=x", "example term architecture"
            ),
        ],
        "q2": [
            SearchHit("Example Domain architecture", "https://docs.preferred.example/arch2", "concepts"),
            SearchHit("random weather", "https://weather.example/today", "sunny with clouds"),
        ],
    }
    with session_scope() as s:
        stats = discover(s, plugin, search=_Search(hits), queries=["q1", "q2"])
        assert stats.queries == 2 and stats.hits == 6
        assert stats.denied == 1 and stats.duplicates == 1 and stats.irrelevant == 1  # weather scores 5 < 20
        assert stats.candidates_added == 2
        cands = {
            c.publisher: c
            for c in s.execute(
                select(Source).where(Source.domain_id == DOMAIN, Source.origin == "discovered")
            ).scalars()
        }
        pref, blog = cands["docs.preferred.example"], cands["blog.other.example"]
        # never trusted automatically
        for c in (pref, blog):
            assert c.status == SourceStatus.CANDIDATE and not c.enabled
            assert c.source_class == "community" and c.authority == 30
            assert "Why this score:" in c.notes
        assert pref.relevance > blog.relevance and pref.relevance >= 50
        assert "preferred by the plugin" in pref.notes and "hit by 2 discovery queries" in pref.notes
        assert "utm_source" not in blog.url  # canonicalised
        # a second run registers nothing new (hosts now known)
        again = discover(s, plugin, search=_Search(hits), queries=["q1", "q2"])
        assert again.candidates_added == 0 and again.duplicates >= 3


def test_scoring_is_explainable_and_capped():
    plugin = get_registry().get(DOMAIN)
    terms = vocabulary_terms(plugin)
    assert {"example", "domain", "architecture", "configuration"} <= terms
    c = Candidate(
        host="a.example",
        url="https://a.example/",
        title="Example architecture configuration installation",
        snippet="terminology concepts domain",
        queries=["a", "b", "c", "d", "e"],
        best_rank=1,
    )
    score_candidate(c, terms, ["a.example"])
    assert c.relevance == min(100, 45 + 30 + 10 + 25) and len(c.reasons) == 4
    low = Candidate(host="b.example", url="https://b.example/", title="x", snippet="y", queries=["a"], best_rank=50)
    score_candidate(low, terms, [])
    assert low.relevance == 0 and low.reasons == []


def test_recurring_discovery_is_opt_in_and_adds_candidates_only(monkeypatch):
    monkeypatch.setattr(jobs_mod, "get_search", lambda: _Search({}))
    with session_scope() as s:
        monkeypatch.setattr(jobs_mod, "discovery_config", lambda: {"interval_hours": 0})
        assert schedule_due_discovery(s) == 0
        monkeypatch.setattr(jobs_mod, "discovery_config", lambda: {"interval_hours": 24})
        n = schedule_due_discovery(s)
        assert n >= 1
        queued = s.execute(select(Job).where(Job.type == "discover", Job.status == "QUEUED")).scalars().all()
        assert any(j.payload["domain_id"] == DOMAIN for j in queued)
        # not twice within the interval: the run exists (and the job key is still queued)
        assert schedule_due_discovery(s) == 0
        # the job itself only ever adds CANDIDATE rows
        job = next(j for j in queued if j.payload["domain_id"] == DOMAIN)
        monkeypatch.setattr(
            jobs_mod,
            "get_search",
            lambda: _Search({"x": [SearchHit("Example domain docs", "https://new.example/d", "example architecture")]}),
        )
        result = jobs_mod.discover_job(s, Job(type="discover", payload={"domain_id": DOMAIN, "queries": ["x"]}))
        assert result["candidates_added"] == 1
        src = s.execute(
            select(Source).where(Source.domain_id == DOMAIN, Source.publisher == "new.example")
        ).scalar_one()
        assert src.status == SourceStatus.CANDIDATE and not src.enabled and job.status == "QUEUED"
