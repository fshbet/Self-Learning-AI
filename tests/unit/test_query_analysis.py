"""Query analysis (ADR 0006 stages 1–2): deterministic, domain-independent, no model."""

from __future__ import annotations

from knowledge_platform.core.retrieval.query import (
    DEFAULT_INTENT_TYPES,
    INTENTS,
    Entity,
    classify_intent,
    compound_variants,
    lexical_variants,
    normalize,
    tokens,
)


def test_normalisation_and_tokens_keep_identifiers():
    assert normalize("  What   does CALCULATE do? ") == "what does calculate do?"
    assert tokens("RangeStart, RangeEnd and INFO.VIEW functions.") == [
        "RangeStart",
        "RangeEnd",
        "and",
        "INFO.VIEW",
        "functions",
    ]


def test_hyphenation_variants_both_directions_and_nothing_silly():
    assert compound_variants("bi-directional") == {"bidirectional", "bi directional"}
    assert compound_variants("bidirectional") == {"bi-directional", "bi directional"}
    assert compound_variants("row-level") == {"rowlevel", "row level"}
    assert compound_variants("crossfiltering") == {"cross-filtering", "cross filtering"}
    # short remainders and ordinary words are left alone: no "re-strict", "co-ntain", "bi-nary"
    assert compound_variants("restrict") == set()
    assert compound_variants("contain") == set()
    assert compound_variants("binary") == set()


def test_declared_synonyms_add_variants_only_when_a_side_is_present():
    syn = {"semantic model": ["dataset", "data model"]}
    assert lexical_variants("How do I refresh a dataset?", syn) == ["semantic model", "data model"]
    assert lexical_variants("How do I refresh a report?", syn) == []
    assert "bi-directional" in lexical_variants("risks of bidirectional filtering")


def test_intents_are_deterministic_and_extensible():
    assert classify_intent("What does CALCULATE do?")[0] == "definition"
    assert classify_intent("How do I configure a gateway?")[0] == "procedure"
    assert classify_intent("What are the risks of bidirectional cross-filtering?")[0] == "limitation"
    assert classify_intent("Show me an example of DIVIDE")[0] == "example"
    assert classify_intent("DIVIDE vs the / operator")[0] == "comparison"
    assert classify_intent("Which functions are only available in visual calculations?")[0] == "version"
    assert classify_intent("How does row-level security restrict data?")[0] == "conceptual"
    assert classify_intent("Random words without a cue")[0] is None  # unknown → no preference
    # a plugin adds its own cues without touching core vocabulary
    intent, cues = classify_intent("Is the arm payload derated at full reach?", {"limitation": [r"\bderated\b"]})
    assert intent == "limitation" and cues == [r"\bderated\b"]
    assert set(DEFAULT_INTENT_TYPES) <= set(INTENTS)


def test_entity_weight_prefers_specific_identifiers_over_common_words():
    calc = Entity(text="CALCULATE", canonical="CALCULATE", kind="identifier", items=7)
    domain = Entity(text="Power BI", canonical="Power BI", kind="subject", items=76)
    word = Entity(text="number", canonical="number", kind="subject", items=3)
    rare = Entity(text="row-level security", canonical="Row-level security", kind="subject", items=1)
    assert rare.weight == 1.0 and calc.weight > domain.weight > word.weight
    assert word.weight < 0.4
