"""Retrieval policy (P2.2): CANDIDATE knowledge never passes as trusted; STALE / CONFLICTED / flagged items are
labelled for the answer model; superseded items are not served at all."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from tests.conftest import requires_db

from knowledge_platform import adapters
from knowledge_platform.api.app import app
from knowledge_platform.core.domains import sync_domain
from knowledge_platform.core.knowledge_entry import KnowledgeEntry, create_knowledge
from knowledge_platform.core.plugins.registry import get_registry
from knowledge_platform.core.retrieval import answer as answer_mod
from knowledge_platform.core.retrieval.answer import answer_question, trust_notes
from knowledge_platform.core.retrieval.search import DEFAULT_STATUSES, hybrid_search
from knowledge_platform.core.verification.review_flags import flag_for_review
from knowledge_platform.core.versioning.lifecycle import transition
from knowledge_platform.db import session_scope
from knowledge_platform.models import ItemStatus, KnowledgeItem

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
    monkeypatch.setattr("knowledge_platform.core.verification.conflicts.judge", lambda *a, **k: ("compatible", "", ""))
    with session_scope() as s:
        sync_domain(s, get_registry().get(DOMAIN))
    yield
    with session_scope() as s:
        s.execute(delete(KnowledgeItem).where(KnowledgeItem.domain_id == DOMAIN))


def _fact(subject, statement, obj):
    return KnowledgeEntry(
        statement=statement,
        subject=subject,
        predicate="has",
        object=obj,
        knowledge_type="fact",
        provenance="ORGANIZATION",
        provided_by="qa",
        authority=90,
        evidence_text="internal spec",
    )


def test_candidates_are_excluded_unless_asked_for_and_then_labelled(monkeypatch):
    plugin = get_registry().get(DOMAIN)
    prompts: list[str] = []
    monkeypatch.setattr(
        answer_mod, "call_text", lambda **kw: prompts.append(kw["user"]) or "SPROCKET has a steel frame [1]."
    )
    with session_scope() as s:
        trusted = create_knowledge(s, plugin, _fact("SPROCKET", "SPROCKET has a steel frame.", "steel frame"))
        candidate = create_knowledge(s, plugin, _fact("SPROCKET", "SPROCKET has a titanium axle.", "titanium axle"))
        transition(s, candidate, ItemStatus.CANDIDATE, reason="unverified", actor="test", force=True)
        stale = create_knowledge(s, plugin, _fact("SPROCKET", "SPROCKET has a brass bell.", "brass bell"))
        transition(s, stale, ItemStatus.STALE, reason="page changed", actor="test")
        flagged = create_knowledge(s, plugin, _fact("SPROCKET", "SPROCKET has a leather seat.", "leather seat"))
        flag_for_review(flagged, "falsification", "a blog says the seat is vinyl")
        gone = create_knowledge(s, plugin, _fact("SPROCKET", "SPROCKET has a wooden rim.", "wooden rim"))
        transition(s, gone, ItemStatus.SUPERSEDED, reason="replaced", actor="test", force=True)
        ids = {"t": trusted.id, "c": candidate.id, "s": stale.id, "f": flagged.id, "g": gone.id}

    with session_scope() as s:
        default = {h.item.id for h in hybrid_search(s, domain_id=DOMAIN, query="SPROCKET", limit=20)}
        assert ids["t"] in default and ids["s"] in default and ids["f"] in default
        assert ids["c"] not in default and ids["g"] not in default
        assert ItemStatus.CANDIDATE not in DEFAULT_STATUSES
        with_c = {
            h.item.id for h in hybrid_search(s, domain_id=DOMAIN, query="SPROCKET", limit=20, include_candidates=True)
        }
        assert ids["c"] in with_c and ids["g"] not in with_c
        # answers: the default prompt never sees the candidate; on request it is labelled as unverified
        ans = answer_question(s, plugin, "What does SPROCKET have?", limit=20)
        assert str(ids["c"]) not in {r["id"] for r in ans.retrieved}
        assert "UNVERIFIED CANDIDATE" not in prompts[-1]
        assert "STALE: its evidence no longer appears" in prompts[-1]
        assert "FLAGGED FOR REVIEW (falsification)" in prompts[-1]
        stale_row = next(r for r in ans.retrieved if r["id"] == str(ids["s"]))
        assert stale_row["trust_notes"] and stale_row["trust_notes"][0].startswith("STALE")
        ans2 = answer_question(s, plugin, "What does SPROCKET have?", limit=20, include_candidates=True)
        assert str(ids["c"]) in {r["id"] for r in ans2.retrieved} and "UNVERIFIED CANDIDATE" in prompts[-1]
        assert trust_notes(s.get(KnowledgeItem, ids["c"])) == [
            "UNVERIFIED CANDIDATE: not yet supported by verified evidence"
        ]
        assert trust_notes(s.get(KnowledgeItem, ids["t"])) == []

    # the API follows the same policy
    r = client.get("/api/search", params={"domain": DOMAIN, "q": "SPROCKET", "limit": 20})
    assert r.status_code == 200 and str(ids["c"]) not in {h["item"]["id"] for h in r.json()}
    r = client.get("/api/search", params={"domain": DOMAIN, "q": "SPROCKET", "limit": 20, "include_candidates": "true"})
    assert str(ids["c"]) in {h["item"]["id"] for h in r.json()}


def test_lexical_search_uses_the_domain_text_search_configuration():
    """P2.5: the same query behaves per configuration — German stemming under 'german', literal tokens under
    'simple' — and an unknown configuration falls back to 'simple' instead of failing."""
    from knowledge_platform.core.retrieval.search import available_text_search_configs, resolve_text_search_config

    plugin = get_registry().get(DOMAIN)
    with session_scope() as s:
        item = create_knowledge(
            s, plugin, _fact("KESSEL", "KESSEL hat zwei Sicherheitsventile für den Betrieb.", "Sicherheitsventile")
        )
        item_id = item.id
    with session_scope() as s:
        assert {"english", "german", "simple"} <= available_text_search_configs(s)
        assert resolve_text_search_config(s, "klingon") == "simple"
        assert resolve_text_search_config(s, "German") == "german"
        # German stemming: the singular query form matches the plural in the statement
        german = hybrid_search(s, domain_id=DOMAIN, query="Sicherheitsventil", limit=5, text_search_config="german")
        assert any(h.item.id == item_id and h.lex_rank is not None for h in german)
        # 'simple' tokenises literally: no lexical hit for the singular, the item can still surface by vector rank only
        simple = hybrid_search(s, domain_id=DOMAIN, query="Sicherheitsventil", limit=5, text_search_config="simple")
        assert all(h.lex_rank is None for h in simple if h.item.id == item_id)
        exact = hybrid_search(s, domain_id=DOMAIN, query="Sicherheitsventile", limit=5, text_search_config="simple")
        assert any(h.item.id == item_id and h.lex_rank is not None for h in exact)
