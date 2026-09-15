"""ADR 0006 retrieval and answer intelligence through the real database (P3, phase 8).

Regression and adversarial cases for the pipeline: exact entity match against a semantically similar wrong
concept, concept coverage, authority as a signal (not an override), trust visible but not erased, intent/type
affinity, hyphenation variants, bounded relationship expansion, version-specific knowledge, multi-concept
queries, the deterministic plan, completeness in lexeme space, and the single bounded regeneration that must
keep its citations. The knowledge is invented (FLUXCAP, GRIDLINK, PULSEMOD) so nothing here depends on a domain
vocabulary; the embedder is a bag-of-words stand-in so the vector channel behaves like a weak semantic signal.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import delete, select
from tests.conftest import requires_db

from knowledge_platform import adapters
from knowledge_platform.core.domains import sync_domain
from knowledge_platform.core.knowledge_entry import KnowledgeEntry, create_knowledge
from knowledge_platform.core.plugins.registry import get_registry
from knowledge_platform.core.retrieval import answer as answer_mod
from knowledge_platform.core.retrieval.answer import answer_question, looks_like_abstention
from knowledge_platform.core.retrieval.plan import build_plan, check_completeness
from knowledge_platform.core.retrieval.retrieve import RETRIEVAL_VERSION, retrieve
from knowledge_platform.core.retrieval.search import resolve_text_search_config
from knowledge_platform.core.verification.review_flags import flag_for_review
from knowledge_platform.core.versioning.lifecycle import transition
from knowledge_platform.db import session_scope
from knowledge_platform.models import ItemStatus, KnowledgeItem, Source

pytestmark = requires_db
DOMAIN = "example"


class _BagOfWords:
    """Deterministic embedder: hashed word buckets, so shared vocabulary means nearby vectors."""

    name, model, dimension = "fake", "fake-bow", 768
    identity = "fake:fake-bow:768"

    def embed(self, texts):
        out = []
        for t in texts:
            v = [0.0] * 768
            for w in t.lower().replace("-", " ").split():
                v[sum(ord(ch) * (i + 1) for i, ch in enumerate(w)) % 768] += 1.0
            norm = sum(x * x for x in v) ** 0.5 or 1.0
            out.append([x / norm for x in v])
        return out

    def embed_one(self, text):
        return self.embed([text])[0]


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    emb = _BagOfWords()
    monkeypatch.setattr(adapters, "get_embedder", lambda: emb)
    monkeypatch.setattr("knowledge_platform.core.retrieval.embeddings.get_embedder", lambda: emb)
    monkeypatch.setattr("knowledge_platform.core.retrieval.search.get_embedder", lambda: emb)
    monkeypatch.setattr("knowledge_platform.core.verification.conflicts.judge", lambda *a, **k: ("compatible", "", ""))
    monkeypatch.setattr("knowledge_platform.core.knowledge_entry.find_near", lambda *a, **k: None)
    with session_scope() as s:
        sync_domain(s, get_registry().get(DOMAIN))
        s.execute(delete(KnowledgeItem).where(KnowledgeItem.domain_id == DOMAIN))
        s.execute(delete(Source).where(Source.domain_id == DOMAIN, Source.key.like("p3test:%")))
    yield
    with session_scope() as s:
        s.execute(delete(KnowledgeItem).where(KnowledgeItem.domain_id == DOMAIN))
        s.execute(delete(Source).where(Source.domain_id == DOMAIN, Source.key.like("p3test:%")))


def _source(s, key: str, authority: int, source_class: str) -> uuid.UUID:
    src = Source(
        domain_id=DOMAIN,
        key=f"p3test:{key}",
        origin="plugin",
        source_class=source_class,
        name=key,
        url=f"https://{key}.test/",
        authority=authority,
    )
    s.add(src)
    s.flush()
    return src.id


def _item(
    s,
    subject: str,
    statement: str,
    *,
    ktype: str = "fact",
    topic: str = "",
    source_id: uuid.UUID | None = None,
    version: str | None = None,
    code: str | None = None,
) -> KnowledgeItem:
    plugin = get_registry().get(DOMAIN)
    it = create_knowledge(
        s,
        plugin,
        KnowledgeEntry(
            statement=statement,
            subject=subject,
            predicate="describes",
            object=statement.split(" ", 1)[1][:40],
            knowledge_type=ktype,
            topic=topic,
            code=code,
            product_version=version,
            provenance="ORGANIZATION",
            provided_by="qa",
            authority=80,
            evidence_text=statement,
        ),
    )
    if source_id is not None:
        for e in it.evidence:
            e.source_id = source_id
    s.flush()
    return it


def _ranked(s, query: str, **kw) -> list[Any]:
    return retrieve(s, get_registry().get(DOMAIN), query, **kw).selected


def _pos(selected, item_id: uuid.UUID) -> int | None:
    for n, sc in enumerate(selected, start=1):
        if sc.item.id == item_id:
            return n
    return None


def _by_id(selected, item_id: uuid.UUID):
    return next(sc for sc in selected if sc.item.id == item_id)


# ----------------------------------------------------------------------------- ranking


def test_exact_entity_match_beats_a_semantically_similar_wrong_concept():
    with session_scope() as s:
        right = _item(s, "FLUXCAP", "FLUXCAP stores charge for the drive train.", ktype="definition").id
        wrong = _item(s, "FLUXGATE", "FLUXGATE senses magnetic fields for the drive train.", ktype="definition").id
        _item(s, "FLUXGATE", "FLUXGATE is calibrated for the drive train every week.")
    with session_scope() as s:
        r = retrieve(s, get_registry().get(DOMAIN), "What is FLUXCAP?")
        assert [e.canonical for e in r.analysis.entities] == ["FLUXCAP"]
        assert r.analysis.entities[0].kind == "identifier" and r.analysis.intent == "definition"
        assert _pos(r.selected, right) == 1 and _pos(r.selected, wrong) not in (None, 1)
        top = r.selected[0]
        assert top.signals["entity_subject"] > 0 and "subject is the query entity 'FLUXCAP'" in " ".join(top.explanation)
        assert "entity_subject" not in _by_id(r.selected, wrong).signals
        # the ranking explains itself: every contribution is recorded and the score is their sum
        assert abs(sum(top.signals.values()) - top.score) < 1e-6
        assert r.summary()["version"] == RETRIEVAL_VERSION and r.summary()["channels"]["entity"] >= 1


def test_concept_coverage_prefers_the_item_that_answers_the_whole_question():
    with session_scope() as s:
        full = _item(s, "FLUXCAP", "FLUXCAP shuts down on thermal overload above ninety degrees.").id
        partial = _item(s, "FLUXCAP", "FLUXCAP stores charge for the drive train.", ktype="definition").id
    with session_scope() as s:
        sel = _ranked(s, "How does FLUXCAP handle thermal overload?")
        assert _pos(sel, full) == 1 and _pos(sel, partial) is not None
        assert _by_id(sel, full).signals["concept_coverage"] > _by_id(sel, partial).signals["concept_coverage"]
        assert any("covers 3/4 query terms" in e for e in _by_id(sel, full).explanation)  # handle is not in it


def test_authority_is_a_signal_not_an_override():
    with session_scope() as s:
        official = _source(s, "official-docs", 95, "official")
        blog = _source(s, "community-blog", 40, "community")
        same_a = _item(s, "GRIDLINK", "GRIDLINK adds two milliseconds of latency per hop.", source_id=official).id
        same_b = _item(s, "GRIDLINK", "GRIDLINK adds two milliseconds of latency for each hop.", source_id=blog).id
        relevant_blog = _item(s, "GRIDLINK", "GRIDLINK retries a failed hop three times.", source_id=blog).id
        irrelevant_official = _item(s, "PULSEMOD", "PULSEMOD emits a heartbeat every second.", source_id=official).id
    with session_scope() as s:
        # equal relevance: the authoritative source wins, and says so
        sel = _ranked(s, "How much latency does GRIDLINK add per hop?")
        assert _pos(sel, same_a) < _pos(sel, same_b)
        a, b = _by_id(sel, same_a), _by_id(sel, same_b)
        assert a.signals["authority"] > b.signals["authority"] and a.signals.get("official") and not b.signals.get("official")
        assert "source authority 95" in " ".join(a.explanation)
        # an authoritative but irrelevant page never beats a relevant secondary one
        sel = _ranked(s, "How many times does GRIDLINK retry a failed hop?")
        assert _pos(sel, relevant_blog) == 1
        assert _pos(sel, irrelevant_official) in (None, *range(2, 20))


def test_trust_is_visible_and_penalised_but_nothing_is_erased():
    with session_scope() as s:
        plugin = get_registry().get(DOMAIN)
        good = _item(s, "PULSEMOD", "PULSEMOD emits a heartbeat every second.")
        good.verification_level = 3
        stale = _item(s, "PULSEMOD", "PULSEMOD emits a heartbeat every two seconds.")
        transition(s, stale, ItemStatus.STALE, reason="page changed", actor="test")
        conflicted = _item(s, "PULSEMOD", "PULSEMOD emits a heartbeat every five seconds.")
        transition(s, conflicted, ItemStatus.CONFLICTED, reason="disagrees", actor="test", force=True)
        flagged = _item(s, "PULSEMOD", "PULSEMOD emits a heartbeat every ten seconds.")
        flag_for_review(flagged, "falsification", "a forum post says otherwise")
        ids = {"good": good.id, "stale": stale.id, "conflicted": conflicted.id, "flagged": flagged.id}
        prompts: list[str] = []
    with session_scope() as s:
        sel = _ranked(s, "How often does PULSEMOD emit a heartbeat?")
        assert _pos(sel, ids["good"]) == 1
        for key in ("stale", "conflicted", "flagged"):
            assert _pos(sel, ids[key]) is not None, key  # penalised, still served
        assert _by_id(sel, ids["stale"]).signals["stale"] < 0
        assert _by_id(sel, ids["conflicted"]).signals["conflicted"] < 0
        assert _by_id(sel, ids["flagged"]).signals["needs_review"] < 0
        assert _by_id(sel, ids["good"]).signals["verification"] > 0
        # the answer context carries the labels (P2 provenance / trust information preserved)
        original = answer_mod.call_text
        answer_mod.call_text = lambda **kw: prompts.append(kw["user"]) or "PULSEMOD emits a heartbeat every second [1]."
        try:
            ans = answer_question(s, plugin, "How often does PULSEMOD emit a heartbeat?")
        finally:
            answer_mod.call_text = original
        assert "STALE: its evidence no longer appears" in prompts[-1]
        assert "CONFLICTED" in prompts[-1] and "FLAGGED FOR REVIEW (falsification)" in prompts[-1]
        assert ans.plan and "say explicitly which cited items are stale, conflicted or flagged" in ans.plan["notes"]


def test_intent_affinity_prefers_the_type_that_answers_and_expands_the_definition():
    with session_scope() as s:
        definition = _item(s, "GRIDLINK", "GRIDLINK is a mesh transport for lab devices.", ktype="definition").id
        limit_ = _item(s, "GRIDLINK", "GRIDLINK cannot exceed one hundred nodes per mesh.", ktype="limitation").id
        _item(s, "GRIDLINK", "Retry a failed GRIDLINK hop three times before failing over.", ktype="procedure")
        _item(s, "GRIDLINK", "Keep GRIDLINK hops under two milliseconds of latency.", ktype="best_practice")
    with session_scope() as s:
        r = retrieve(s, get_registry().get(DOMAIN), "What are the limitations of GRIDLINK?", k=2)
        assert r.analysis.intent == "limitation"
        assert _pos(r.selected, limit_) == 1
        assert _by_id(r.selected, limit_).signals["intent_affinity"] > 0
        assert "limitation suits intent 'limitation'" in " ".join(_by_id(r.selected, limit_).explanation)
        # no foundation item about GRIDLINK is in the top-2: bounded expansion adds its definition, marked as such
        added = [sc for sc in r.selected if sc.expanded_from]
        assert 1 <= len(added) <= 3 and len(r.selected) <= 2 + 3
        assert _by_id(r.selected, definition).expanded_from == "GRIDLINK"
        assert _by_id(r.selected, definition).item.knowledge_type == "definition"


def test_hyphenation_variants_match_the_corpus_spelling():
    with session_scope() as s:
        bidi = _item(s, "GRIDLINK", "Bidirectional links slow GRIDLINK meshes down.", ktype="limitation").id
        _item(s, "GRIDLINK", "GRIDLINK is a mesh transport for lab devices.", ktype="definition")
    with session_scope() as s:
        r = retrieve(s, get_registry().get(DOMAIN), "Why do bi-directional links slow GRIDLINK down?")
        assert "bidirectional" in r.analysis.variants
        assert _pos(r.selected, bidi) == 1
        assert any("bidirect" in e for e in _by_id(r.selected, bidi).explanation)


def test_version_specific_knowledge_is_preferred_and_the_mismatch_is_recorded():
    with session_scope() as s:
        v2 = _item(s, "GRIDLINK", "GRIDLINK supports two hundred nodes per mesh.", version="2.0").id
        v1 = _item(s, "GRIDLINK", "GRIDLINK supports one hundred nodes per mesh.", version="1.0").id
    with session_scope() as s:
        r = retrieve(s, get_registry().get(DOMAIN), "How many nodes per mesh does GRIDLINK version 2.0 support?")
        assert r.analysis.version == "2.0"
        assert _pos(r.selected, v2) == 1 and _pos(r.selected, v1) == 2  # the older one is still served
        assert _by_id(r.selected, v2).signals["version_match"] > 0 > _by_id(r.selected, v1).signals["version_mismatch"]


def test_example_intent_expands_with_examples_and_negative_knowledge_is_kept():
    with session_scope() as s:
        _item(s, "FLUXCAP", "FLUXCAP stores charge for the drive train.", ktype="definition")
        ex = _item(s, "FLUXCAP", "Charging FLUXCAP from the bench supply.", ktype="example", code="fluxcap charge --bench").id
        neg = _item(s, "FLUXCAP", "FLUXCAP must not be charged above forty volts.", ktype="limitation").id
    with session_scope() as s:
        r = retrieve(s, get_registry().get(DOMAIN), "Show me an example of charging FLUXCAP", k=1)
        assert r.analysis.intent == "example"
        assert _pos(r.selected, ex) is not None
        r2 = retrieve(s, get_registry().get(DOMAIN), "What should I avoid when charging FLUXCAP?", k=1)
        assert r2.analysis.intent == "limitation" and _pos(r2.selected, neg) is not None


def test_multi_concept_query_covers_both_entities_and_plans_both():
    with session_scope() as s:
        plugin = get_registry().get(DOMAIN)
        f = _item(s, "FLUXCAP", "FLUXCAP stores charge for the drive train.", ktype="definition").id
        g = _item(s, "GRIDLINK", "GRIDLINK is a mesh transport for lab devices.", ktype="definition").id
        both = _item(s, "GRIDLINK", "GRIDLINK reports FLUXCAP charge levels to the mesh.").id
        for n in range(4):
            _item(s, "PULSEMOD", f"PULSEMOD emits a heartbeat every {n + 1} seconds on channel {n}.")
    with session_scope() as s:
        r = retrieve(s, plugin, "How does GRIDLINK report FLUXCAP charge?")
        assert {e.canonical for e in r.analysis.entities} == {"GRIDLINK", "FLUXCAP"}
        assert _pos(r.selected, both) == 1 and _pos(r.selected, f) and _pos(r.selected, g)
        config = resolve_text_search_config(s, plugin.text_search_config())
        plan = build_plan(s, r.analysis, r.selected, config=config, plugin=plugin)
        assert {c.term for c in plan.must_cover} == {"GRIDLINK", "FLUXCAP"}
        assert all(c.evidence for c in plan.must_cover + plan.should_cover)  # nothing planned without evidence


def test_diversity_caps_one_subject_without_dropping_distinct_members():
    with session_scope() as s:
        for n in range(6):
            _item(s, "PULSEMOD", f"PULSEMOD heartbeat variant {n} runs on channel {n} of the lab bus.")
        other = _item(s, "GRIDLINK", "GRIDLINK carries the PULSEMOD heartbeat across the mesh.").id
    with session_scope() as s:
        sel = _ranked(s, "How does the PULSEMOD heartbeat travel?", k=4)
        assert sum(1 for sc in sel if sc.item.subject == "PULSEMOD" and not sc.expanded_from) == 3
        assert _pos(sel, other) is not None  # the distinct subject is not crowded out by the sixfold one
        # with nothing else in the pool the cap is backfilled rather than leaving the context short
        sel6 = _ranked(s, "How does the PULSEMOD heartbeat travel?", k=6)
        assert len([sc for sc in sel6 if not sc.expanded_from]) == 6


# ----------------------------------------------------------------------------- plan, completeness, regeneration


def test_plan_only_requires_supported_concepts_and_completeness_is_lexeme_based():
    with session_scope() as s:
        plugin = get_registry().get(DOMAIN)
        _item(s, "FLUXCAP", "FLUXCAP shuts down on thermal overload above ninety degrees.")
        _item(s, "FLUXCAP", "FLUXCAP logs every thermal overload to the bench recorder.")
        _item(s, "FLUXCAP", "FLUXCAP stores charge for the drive train.", ktype="definition")
    with session_scope() as s:
        # ZORBLAT is in the question but no evidence carries it: it must not be planned
        r = retrieve(s, plugin, "What should I know about FLUXCAP and ZORBLAT?")
        assert {e.canonical for e in r.analysis.entities} >= {"FLUXCAP", "ZORBLAT"}
        config = resolve_text_search_config(s, plugin.text_search_config())
        plan = build_plan(s, r.analysis, r.selected, config=config, plugin=plugin)
        assert [c.term for c in plan.must_cover] == ["FLUXCAP"]
        assert "thermal overload" in [c.term for c in plan.should_cover]
        # inflection, not substring: "thermal overloads" satisfies "thermal overload"; "overloads thermally" not
        yes = check_completeness(s, config, "FLUXCAP handles thermal overloads by shutting down [1].", plan)
        no = check_completeness(s, config, "FLUXCAP overloads thermally and shuts down [1].", plan)
        assert "thermal overload" not in [c["term"] for c in yes["missing_should"]]
        assert "thermal overload" in [c["term"] for c in no["missing_should"]]
        assert "FLUXCAP" in [c["term"] for c in check_completeness(s, config, "It shuts down.", plan)["missing_must"]]


def test_regeneration_is_single_bounded_and_keeps_citations(monkeypatch):
    with session_scope() as s:
        plugin = get_registry().get(DOMAIN)
        _item(s, "FLUXCAP", "FLUXCAP shuts down on thermal overload above ninety degrees.")
        _item(s, "FLUXCAP", "FLUXCAP logs every thermal overload to the bench recorder.")
        _item(s, "FLUXCAP", "FLUXCAP resets after a thermal overload once the bench recorder confirms.")
        _item(s, "FLUXCAP", "FLUXCAP stores charge for the drive train.", ktype="definition")
    calls: list[str] = []
    replies = iter(
        [
            "FLUXCAP stores charge for the drive train [1].",
            "FLUXCAP stores charge for the drive train [1]. It shuts down on thermal overload and logs it to the "
            "bench recorder [2] [3].",
            "THIRD CALL MUST NOT HAPPEN",
        ]
    )
    monkeypatch.setattr(answer_mod, "call_text", lambda **kw: calls.append(kw["user"]) or next(replies))
    with session_scope() as s:
        ans = answer_question(s, plugin, "What should I know about FLUXCAP?")
    assert ans.mode == "p3" and ans.llm_calls == 2 and len(calls) == 2
    assert ans.regeneration and ans.regeneration["triggered"] and ans.regeneration["accepted"]
    assert "thermal overload" in ans.regeneration["missing"] and ans.regeneration["evidence"]
    assert "[2]" in calls[1] and "thermal overload" in calls[1]  # the model is told what is missing and where
    assert ans.regeneration["before"]["score"] < ans.regeneration["after"]["score"]
    # citations survive: every [n] resolves to a retrieved item with its status and evidence-bearing id
    assert {c["n"] for c in ans.citations} >= {1, 2, 3}
    assert all(c["id"] and c["status"] for c in ans.citations) and not ans.insufficient
    # the retrieved rows keep provenance / trust information after regeneration
    assert all("trust_notes" in r and "explanation" in r for r in ans.retrieved)


def test_regeneration_does_not_fire_for_abstentions_or_complete_answers(monkeypatch):
    with session_scope() as s:
        plugin = get_registry().get(DOMAIN)
        _item(s, "FLUXCAP", "FLUXCAP shuts down on thermal overload above ninety degrees.")
        _item(s, "FLUXCAP", "FLUXCAP logs every thermal overload to the bench recorder.")
        _item(s, "FLUXCAP", "FLUXCAP stores charge for the drive train.", ktype="definition")
    calls: list[str] = []
    decline = "The knowledge base does not contain information to answer this question."
    monkeypatch.setattr(answer_mod, "call_text", lambda **kw: calls.append(kw["user"]) or decline)
    with session_scope() as s:
        ans = answer_question(s, plugin, "What is the warranty period of ZORBLAT?")
    assert looks_like_abstention(ans.answer) and ans.insufficient and ans.llm_calls == 1 and len(calls) == 1
    assert ans.regeneration is None or not ans.regeneration["triggered"]
    calls.clear()
    complete = "FLUXCAP stores charge [3]; it shuts down on thermal overload and logs it to the bench recorder [1] [2]."
    monkeypatch.setattr(answer_mod, "call_text", lambda **kw: calls.append(kw["user"]) or complete)
    with session_scope() as s:
        ans = answer_question(s, plugin, "What should I know about FLUXCAP?")
    assert ans.llm_calls == 1 and ans.completeness and ans.completeness["ok"]
    assert ans.regeneration is None


def test_modes_are_distinguishable_and_p2_is_untouched(monkeypatch):
    with session_scope() as s:
        plugin = get_registry().get(DOMAIN)
        _item(s, "FLUXCAP", "FLUXCAP stores charge for the drive train.", ktype="definition")
    prompts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        answer_mod, "call_text", lambda **kw: prompts.append((kw["system"], kw["user"])) or "FLUXCAP stores charge [1]."
    )
    with session_scope() as s:
        p2 = answer_question(s, plugin, "What is FLUXCAP?", mode="p2")
        p3r = answer_question(s, plugin, "What is FLUXCAP?", mode="p3-retrieval")
        p3 = answer_question(s, plugin, "What is FLUXCAP?", mode="p3")
    assert (p2.mode, p3r.mode, p3.mode) == ("p2", "p3-retrieval", "p3")
    assert p2.plan is None and p2.retrieval is None and "Source:" not in prompts[0][1]
    assert p3r.plan is not None and p3r.regeneration is None and "Must address" not in prompts[1][1]
    assert "Must address" in prompts[2][1] and "Source:" in prompts[2][1]
    assert prompts[0][0] != prompts[2][0]  # the frozen P2 system prompt is not the P3 one
    with session_scope() as s:
        assert s.execute(select(KnowledgeItem).where(KnowledgeItem.domain_id == DOMAIN)).scalars().one()
