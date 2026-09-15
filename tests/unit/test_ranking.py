"""Explainable ranking (ADR 0006 stages 4–6) on synthetic candidates: every claim about the score is a claim
about a recorded signal, so "why did this rank above that?" is answerable from the data."""

from __future__ import annotations

from types import SimpleNamespace

from knowledge_platform.core.retrieval.candidates import Candidate
from knowledge_platform.core.retrieval.query import Entity, QueryAnalysis
from knowledge_platform.core.retrieval.rank import W, diversify, score_candidates

SRC_OFFICIAL, SRC_BLOG = "s-official", "s-blog"
SOURCES = {SRC_OFFICIAL: (95, "official"), SRC_BLOG: (40, "community")}


class _Plugin:
    manifest = SimpleNamespace(retrieval=SimpleNamespace(intent_types={}))

    def role_of(self, t):
        return {"definition": "foundation", "fact": "foundation", "limitation": "dependent", "example": "example"}.get(
            t, "neutral"
        )

    def types_with_role(self, *roles):
        return [
            t for t, r in {"definition": "foundation", "fact": "foundation", "example": "example"}.items() if r in roles
        ]

    def taxonomy_paths(self):
        return ["Modeling", "Modeling/Star schema", "DAX", "DAX/Functions"]


def _item(**kw):
    base = dict(
        id=kw.get("subject", "x"),
        subject="X",
        statement="",
        explanation="",
        knowledge_type="fact",
        polarity="positive",
        topic="",
        status="VERIFIED",
        verification_level=2,
        product_version=None,
        needs_review=False,
        needs_revalidation=False,
        evidence=[SimpleNamespace(source_id=SRC_OFFICIAL)],
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _analysis(query, lexemes, entities=(), intent=None, weights=None):
    return QueryAnalysis(
        query=query,
        normalized=query.lower(),
        tokens=query.split(),
        variants=[],
        lexemes=lexemes,
        entities=list(entities),
        intent=intent,
        term_weights=weights or {},
    )


def _rank(cands, analysis):
    return score_candidates({str(i): c for i, c in enumerate(cands)}, analysis, plugin=_Plugin(), sources=SOURCES)


def test_exact_subject_entity_beats_term_dense_noise():
    """The CALCULATE case: a definition filed under the entity outranks items that merely repeat the query words."""
    definition = Candidate(
        item=_item(
            subject="CALCULATE",
            statement="CALCULATE evaluates an expression in a modified filter context.",
            knowledge_type="definition",
        ),
        vec_rank=7,
        lexemes={"calcul", "evalu", "express", "modifi", "filter", "context"},
    )
    noise = Candidate(
        item=_item(
            subject="DAX user-defined function",
            statement="A DAX user-defined function is a function that returns a scalar or a table.",
        ),
        vec_rank=2,
        lex_rank=1,
        lexemes={"dax", "user-defin", "function", "return", "scalar", "tabl"},
    )
    a = _analysis(
        "What does the CALCULATE function do in DAX?",
        ["calcul", "function", "dax"],
        entities=[Entity(text="CALCULATE", canonical="CALCULATE", kind="identifier", items=3)],
        intent="definition",
        weights={"calcul": 4.0, "function": 3.0, "dax": 3.0},
    )
    ranked = _rank([noise, definition], a)
    assert ranked[0].item.subject == "CALCULATE"
    assert ranked[0].signals["entity_subject"] > 0 and "entity_subject" not in ranked[1].signals
    assert any("subject is the query entity" in e for e in ranked[0].explanation)


def test_idf_weighted_concept_coverage_prefers_the_rare_terms():
    """Two items with the same channel ranks: the one carrying the discriminating query terms wins."""
    a = _analysis(
        "row-level security restrict data",
        ["row-level", "secur", "restrict", "data"],
        weights={"row-level": 6.0, "secur": 4.0, "restrict": 6.0, "data": 2.0},
    )
    rare = Candidate(
        item=_item(subject="A", statement="RLS restricts rows."), vec_rank=5, lexemes={"rls", "restrict", "row"}
    )
    common = Candidate(item=_item(subject="B", statement="Data is data."), vec_rank=5, lexemes={"data"})
    ranked = _rank([common, rare], a)
    assert ranked[0].item.subject == "A"
    assert ranked[0].signals["concept_coverage"] > ranked[1].signals["concept_coverage"]


def test_authority_is_a_tie_breaker_never_an_override():
    a = _analysis(
        "star schema recommended",
        ["star", "schema", "recommend"],
        weights={"star": 5.0, "schema": 5.0, "recommend": 3.0},
    )
    relevant_blog = Candidate(
        item=_item(
            subject="Star schema",
            statement="A star schema is recommended for models.",
            evidence=[SimpleNamespace(source_id=SRC_BLOG)],
            verification_level=1,
        ),
        vec_rank=1,
        lexemes={"star", "schema", "recommend", "model"},
    )
    irrelevant_official = Candidate(
        item=_item(subject="Licensing", statement="Pro licences are assigned per user."),
        vec_rank=30,
        lexemes={"pro", "licenc", "assign", "user"},
    )
    ranked = _rank([irrelevant_official, relevant_blog], a)
    assert ranked[0].item.subject == "Star schema"
    # the official source did get its authority credit — it just cannot beat relevance
    assert ranked[1].signals["authority"] > ranked[0].signals["authority"]
    assert sum(v for k, v in W.items() if k in ("authority", "verification", "official")) < W["entity_subject"]


def test_trust_states_are_penalised_but_stay_visible_and_labelled():
    a = _analysis("widget supply", ["widget", "suppli"])
    fine = Candidate(
        item=_item(subject="WIDGET", statement="WIDGET has a 12 V supply."), vec_rank=1, lexemes={"widget", "suppli"}
    )
    stale = Candidate(
        item=_item(subject="WIDGET", statement="WIDGET has a 9 V supply.", status="STALE"),
        vec_rank=1,
        lexemes={"widget", "suppli"},
    )
    flagged = Candidate(
        item=_item(subject="WIDGET", statement="WIDGET has a 6 V supply.", needs_review=True),
        vec_rank=1,
        lexemes={"widget", "suppli"},
    )
    conflicted = Candidate(
        item=_item(subject="WIDGET", statement="WIDGET has a 24 V supply.", status="CONFLICTED"),
        vec_rank=1,
        lexemes={"widget", "suppli"},
    )
    ranked = _rank([stale, flagged, conflicted, fine], a)
    assert ranked[0].item.statement.startswith("WIDGET has a 12")
    assert len(ranked) == 4  # nothing excluded
    labels = {r.item.statement.split()[3]: r.explanation for r in ranked}  # the voltage
    assert any("STALE" in e for e in labels["9"]) and any("review" in e for e in labels["6"])
    assert any("CONFLICTED" in e for e in labels["24"])


def test_intent_affinity_prefers_negative_knowledge_for_limitation_questions():
    a = _analysis("What are the risks of bidirectional filtering?", ["risk", "bidirect", "filter"], intent="limitation")
    positive = Candidate(
        item=_item(
            subject="Bidirectional filtering", statement="Bidirectional filtering propagates filters both ways."
        ),
        vec_rank=1,
        lexemes={"bidirect", "filter", "propag"},
    )
    negative = Candidate(
        item=_item(
            subject="Bidirectional filtering",
            statement="Bidirectional filtering can create ambiguous paths.",
            knowledge_type="limitation",
            polarity="negative",
        ),
        vec_rank=2,
        lexemes={"bidirect", "filter", "ambigu", "path"},
    )
    ranked = _rank([positive, negative], a)
    assert ranked[0].item.polarity == "negative" and "intent_affinity" in ranked[0].signals


def test_listing_intent_prefers_members_over_the_class_itself():
    a = _analysis(
        "Which functions are only available in visual calculations?",
        ["function", "avail", "visual", "calcul"],
        entities=[Entity(text="visual calculations", canonical="visual calculations", kind="subject", items=10)],
        intent="listing",
    )
    klass = Candidate(
        item=_item(subject="Visual calculations", statement="Visual calculations are DAX calculations on a visual."),
        vec_rank=1,
        lexemes={"visual", "calcul", "dax"},
    )
    member = Candidate(
        item=_item(subject="PREVIOUS", statement="The PREVIOUS function is used in visual calculations only."),
        vec_rank=4,
        lexemes={"previous", "function", "visual", "calcul"},
    )
    ranked = _rank([klass, member], a)
    assert ranked[0].item.subject == "PREVIOUS" and "member" in ranked[0].signals


def test_version_match_and_mismatch_are_recorded():
    a = _analysis("In version 2024 what changed", ["version", "2024", "chang"])
    a.version = "2024"
    match = Candidate(
        item=_item(subject="A", statement="Changed in 2024.", product_version="2024"),
        vec_rank=2,
        lexemes={"chang", "2024"},
    )
    mismatch = Candidate(
        item=_item(subject="B", statement="Changed in 2019.", product_version="2019"),
        vec_rank=1,
        lexemes={"chang", "2019"},
    )
    ranked = _rank([mismatch, match], a)
    assert ranked[0].item.subject == "A" and ranked[1].signals["version_mismatch"] < 0


def test_diversity_collapses_restatements_of_one_subject_but_keeps_list_members():
    a = _analysis("functions in visual calculations", ["function", "visual", "calcul"])
    same = [
        Candidate(
            item=_item(
                subject="Gateway", statement=f"The standard gateway is recommended for scenarios requiring {x}."
            ),
            vec_rank=i + 1,
            lexemes=set(),
        )
        for i, x in enumerate(["DirectQuery", "auditing", "clustering", "load balancing"])
    ]
    members = [
        Candidate(
            item=_item(subject=n, statement=f"The {n} function is used in visual calculations only."),
            vec_rank=10 + i,
            lexemes=set(),
        )
        for i, n in enumerate(["FIRST", "LAST", "NEXT", "PREVIOUS"])
    ]
    ranked = _rank(same + members, a)
    chosen = diversify(ranked, k=6)
    subjects = [c.item.subject for c in chosen]
    assert subjects.count("Gateway") <= 3
    assert {"FIRST", "LAST", "NEXT"} <= set(subjects)  # different subjects are never "duplicates"
    assert any("held back for diversity" in e for r in ranked for e in r.explanation)
