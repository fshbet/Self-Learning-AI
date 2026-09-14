"""AI Knowledge Source rendering: self-contained text, citations, usage hints, navigation index."""

from knowledge_platform.core.export.render import ai_index, ai_record, usage_hint
from knowledge_platform.core.export.schema import EvidenceRecord, KnowledgeRecord


def _rec(**kw) -> KnowledgeRecord:
    base = dict(
        id="k1",
        domain="d",
        knowledge_type="example",
        subject="DIVIDE",
        predicate="handles",
        object="division by zero",
        statement="DIVIDE returns BLANK on division by zero.",
        explanation="Use DIVIDE instead of / to avoid errors.",
        code="DIVIDE([Sales], [Qty])",
        details={"expected_result": "BLANK()", "common_mistake": "Using / directly"},
        product_version="Power BI Desktop",
        status="VERIFIED",
        confidence=0.9,
        verification_level=3,
        first_discovered_at="2026-01-01T00:00:00Z",
        content_hash="sha256:abc",
        topic="DAX/Functions",
        dependencies=[{"relation": "example_of", "item_id": "k0"}],
    )
    base.update(kw)
    return KnowledgeRecord(**base)


def _ev(kind: str, **kw) -> EvidenceRecord:
    base = dict(
        id=f"e-{kind}",
        knowledge_item_id="k1",
        evidence_type=kind,
        excerpt="DIVIDE returns BLANK on division by zero.",
        verified=True,
    )
    base.update(kw)
    return EvidenceRecord(**base)


def test_ai_record_text_is_self_contained_with_numbered_sources():
    ev = [
        _ev("extraction", document_url="https://x/divide", document_title="DIVIDE function", section=["Syntax"]),
        _ev("human", details={"provided_by": "qa", "provenance": "ORGANIZATION", "authority": 90}),
        _ev("validator", details={"validator": "dax_syntax", "passed": True}),
        _ev("validator", id="e-v2", details={"validator": "dax_semantics", "passed": False}),
        _ev("extraction", id="e-x2", document_url="https://x/old", verified=False),
    ]
    r = ai_record(_rec(), ev, related=["k2"])
    text = r["text"]
    assert text.startswith("DIVIDE returns BLANK")
    assert "```\nDIVIDE([Sales], [Qty])\n```" in text
    assert "Expected result: BLANK()" in text and "Common mistake: Using / directly" in text
    assert "Applies to: Power BI Desktop" in text
    assert "[1] DIVIDE function — https://x/divide, section: Syntax" in text
    assert "[2] qa (organization-provided)" in text
    assert "https://x/old" not in text  # unverified evidence is never cited
    assert [c["kind"] for c in r["citations"]] == ["document", "human"]
    assert r["validated_by"] == ["dax_syntax"]  # only passing validators
    assert r["usage"] == "cite" and r["dependencies"] == [{"relation": "example_of", "item_id": "k0"}]
    assert r["related_ids"] == ["k2"]


def test_negative_prefix_and_usage_hints():
    neg = _rec(knowledge_type="limitation", polarity="negative", statement="X is not supported in DirectQuery.")
    assert ai_record(neg, [], [])["text"].startswith("Limitation: X is not supported")
    assert usage_hint(_rec(status="STALE")) == "caution"
    assert usage_hint(_rec(status="CONFLICTED")) == "caution"
    assert usage_hint(_rec(status="SUPERSEDED", historical=True)) == "historical"
    assert usage_hint(_rec(status="SUPPORTED")) == "cite"


def test_ai_index_groups_by_topic_subject_and_counts():
    items = [
        _rec(),
        _rec(id="k2", knowledge_type="fact", topic="DAX", subject="divide", status="STALE"),
        _rec(id="k3", knowledge_type="warning", polarity="negative", status="SUPERSEDED", historical=True),
    ]
    idx = ai_index(items, {"taxonomy": ["DAX"], "terminology": {"DAX": "Data Analysis Expressions"}})
    assert idx["topics"]["DAX/Functions"]["ids"] == ["k1", "k3"]
    assert idx["topics"]["DAX/Functions"]["types"] == {"example": 1, "warning": 1}
    assert idx["subjects"]["divide"] == ["k1", "k2", "k3"]
    assert idx["statuses"] == {"STALE": 1, "SUPERSEDED": 1, "VERIFIED": 1}
    assert idx["polarity"] == {"negative": 1, "positive": 2}
    assert idx["recommended_filters"]["exclude"] == {"usage": ["historical"]}
    assert idx["terminology"]["DAX"]


def test_flagged_items_are_caution_and_say_so_in_the_text():
    flagged = _rec(needs_review=True, review_kind="falsification", review_reason="counter-evidence at https://x")
    r = ai_record(flagged, [], [])
    assert r["usage"] == "caution" and r["needs_review"] and r["review_kind"] == "falsification"
    assert r["caution_reasons"] == ["flagged for review (falsification): counter-evidence at https://x"]
    assert r["text"].startswith("Caution: flagged for review (falsification): counter-evidence at https://x.")
    reval = _rec(needs_revalidation=True, revalidation_reason="dependency k0 became STALE")
    assert usage_hint(reval) == "caution" and "awaiting revalidation" in ai_record(reval, [], [])["text"]
    contradicted = _rec(evidence_status={"verified": 1, "unverified": 0, "contradicting": 1})
    assert usage_hint(contradicted) == "caution"
    assert "1 contradicting evidence record(s) attached" in ai_record(contradicted, [], [])["caution_reasons"][0]
    plain = ai_record(_rec(), [], [])
    assert plain["usage"] == "cite" and plain["caution_reasons"] == [] and not plain["text"].startswith("Caution")
    hist = ai_record(_rec(status="SUPERSEDED", historical=True, needs_review=True), [], [])
    assert hist["usage"] == "historical" and hist["text"].startswith("Historical:")
    idx = ai_index([flagged, reval, _rec(id="k9")], {})
    assert idx["usage"] == {"caution": 2, "cite": 1} and idx["flagged"] == {"needs_review": 1, "needs_revalidation": 1}
