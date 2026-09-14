"""Mechanical, explainable evaluation checks (req. 15).

Every check returns ``{"ok": bool, "detail": str, ...}`` so the UI can show *why* a question passed
or failed. The LLM judge (see runner) is an additional signal, never the only one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

_WS = re.compile(r"\s+")
_ABSTAIN_PATTERNS = (
    "does not contain",
    "do not contain",
    "not enough",
    "insufficient",
    "no verified information",
    "does not cover",
    "not covered",
    "cannot answer",
    "no information",
    "not available in the knowledge",
)


def _norm(s: str) -> str:
    return _WS.sub(" ", (s or "").lower()).strip()


@dataclass
class CitedItem:
    """What the checker needs to know about a cited knowledge item."""

    id: str
    n: int
    status: str
    topic: str = ""
    evidence_verified: bool = False
    evidence_urls: list[str] = field(default_factory=list)
    validator_results: list[bool] = field(default_factory=list)  # passed flags of validator evidence
    product_version: str | None = None
    polarity: str = "positive"


@dataclass
class RetrievedItem:
    id: str
    topic: str = ""
    evidence_urls: list[str] = field(default_factory=list)


def check_required_concepts(answer: str, required: list[str]) -> dict[str, Any]:
    a = _norm(answer)
    missing = [c for c in required if _norm(c) not in a]
    return {
        "ok": not missing,
        "detail": f"missing: {missing}" if missing else "all concepts present",
        "missing": missing,
    }


def check_must_not_contain(answer: str, forbidden: list[str]) -> dict[str, Any]:
    a = _norm(answer)
    hits = [f for f in forbidden if _norm(f) in a]
    return {"ok": not hits, "detail": f"forbidden phrases present: {hits}" if hits else "ok", "hits": hits}


ABSTENTION_MAX_CHARS = 400  # an answer that declines *and then keeps going* is an answer, not an abstention


def is_abstention(answer: str, no_results: bool) -> bool:
    """A genuine abstention: nothing was retrieved at all, or the answer is a short decline.

    ``no_results`` must mean "retrieval returned nothing" — never "no citations" (audit P0.5): an answer written
    from the model's own memory has no citations either, and that is an *uncited answer* to be judged, not an
    abstention. A decline followed by a substantive answer (> ABSTENTION_MAX_CHARS) is treated as an answer.
    """
    a = _norm(answer)
    if no_results:
        return True
    return any(p in a for p in _ABSTAIN_PATTERNS) and len(a) <= ABSTENTION_MAX_CHARS


def answer_kind(answer: str, no_results: bool, cited_count: int) -> str:
    """abstention | cited_answer | uncited_answer."""
    if is_abstention(answer, no_results):
        return "abstention"
    return "cited_answer" if cited_count else "uncited_answer"


def check_answer_kind(answer: str, no_results: bool, cited_count: int) -> dict[str, Any]:
    kind = answer_kind(answer, no_results, cited_count)
    return {"ok": True, "detail": kind.replace("_", " "), "kind": kind}


def check_coverage(coverage: dict[str, Any] | None) -> dict[str, Any]:
    """Pass-through of the coverage signal computed against the knowledge base (see runner._coverage):
    does relevant knowledge exist at all? n/a when the question carries no topic, sources or concepts."""
    if not coverage or coverage.get("value") is None:
        return {
            "ok": True,
            "detail": "n/a (question has no topic, authoritative source or concept to check)",
            "value": None,
        }
    return coverage


def check_abstention(answer: str, no_results: bool, expect_abstain: bool) -> dict[str, Any]:
    abstained = is_abstention(answer, no_results)
    ok = abstained == expect_abstain
    detail = (
        "correctly abstained"
        if ok and expect_abstain
        else "answered as expected"
        if ok
        else "should have abstained but answered"
        if expect_abstain
        else "abstained although knowledge was expected"
    )
    return {"ok": ok, "detail": detail, "abstained": abstained}


def check_citations(cited: list[CitedItem], retrieved_ids: set[str], answer: str) -> dict[str, Any]:
    """Citations must resolve to retrieved items that carry verified evidence."""
    if not cited:
        return {"ok": False, "detail": "no citations", "cited": 0, "valid": 0}
    unresolved = [c.n for c in cited if c.id not in retrieved_ids]
    unverified = [c.n for c in cited if not c.evidence_verified]
    ok = not unresolved and not unverified
    return {
        "ok": ok,
        "detail": "all citations resolve to items with verified evidence"
        if ok
        else f"unresolved: {unresolved}, without verified evidence: {unverified}",
        "cited": len(cited),
        "valid": len(cited) - len(set(unresolved) | set(unverified)),
    }


def check_retrieval_precision(retrieved: list[RetrievedItem], topic: str) -> dict[str, Any]:
    """Share of retrieved items filed under the question's topic (or its parent); n/a without a topic."""
    if not topic or not retrieved:
        return {"ok": True, "detail": "n/a", "value": None, "retrieved": len(retrieved)}
    top = topic.split("/")[0].lower()
    hits = sum(1 for r in retrieved if (r.topic or "").lower().startswith(top))
    value = hits / len(retrieved)
    return {
        "ok": value >= 0.5,
        "detail": f"{hits}/{len(retrieved)} retrieved items match topic '{topic}'",
        "value": value,
        "retrieved": len(retrieved),
    }


def _url_key(url: str) -> str:
    p = urlsplit(url)
    return (p.netloc.lower() + p.path.rstrip("/")).lower()


def check_retrieval_recall(retrieved: list[RetrievedItem], authoritative_sources: list[str]) -> dict[str, Any]:
    """Share of the question's authoritative URLs that appear among the retrieved items' evidence."""
    if not authoritative_sources:
        return {"ok": True, "detail": "n/a", "value": None}
    seen = {_url_key(u) for r in retrieved for u in r.evidence_urls}
    found = [
        s for s in authoritative_sources if any(k.startswith(_url_key(s)) or _url_key(s).startswith(k) for k in seen)
    ]
    value = len(found) / len(authoritative_sources)
    return {
        "ok": value > 0,
        "detail": f"{len(found)}/{len(authoritative_sources)} authoritative sources retrieved",
        "value": value,
    }


def check_freshness(cited: list[CitedItem], answer: str) -> dict[str, Any]:
    stale = [c.n for c in cited if c.status == "STALE"]
    if not stale:
        return {"ok": True, "detail": "no stale items cited", "stale": []}
    mentioned = any(w in _norm(answer) for w in ("stale", "outdated", "may be out of date", "no longer"))
    return {
        "ok": mentioned,
        "detail": f"stale items cited {stale}; {'flagged' if mentioned else 'not flagged'} in answer",
        "stale": stale,
    }


def check_contradictions(cited: list[CitedItem], answer: str) -> dict[str, Any]:
    conflicted = [c.n for c in cited if c.status == "CONFLICTED"]
    if not conflicted:
        return {"ok": True, "detail": "no conflicted items cited", "conflicted": []}
    mentioned = any(w in _norm(answer) for w in ("conflict", "disagree", "contradict", "differ"))
    return {
        "ok": mentioned,
        "detail": f"conflicted items cited {conflicted}; {'flagged' if mentioned else 'not flagged'} in answer",
        "conflicted": conflicted,
    }


def check_version(answer: str, expected_version: str) -> dict[str, Any]:
    if not expected_version:
        return {"ok": True, "detail": "n/a"}
    ok = _norm(expected_version) in _norm(answer)
    return {"ok": ok, "detail": f"expected version '{expected_version}' {'mentioned' if ok else 'missing'}"}


def check_negative_knowledge(cited: list[CitedItem], negative: bool) -> dict[str, Any]:
    """Questions about limitations must be answered from negative knowledge (req. 19), not inferred from
    positive statements."""
    if not negative:
        return {"ok": True, "detail": "n/a", "value": None}
    hits = [c.n for c in cited if c.polarity == "negative"]
    return {
        "ok": bool(hits),
        "detail": f"negative-knowledge items cited {hits}" if hits else "no limitation/warning item cited",
        "value": 1.0 if hits else 0.0,
        "cited_negative": hits,
    }


def check_validators(cited: list[CitedItem]) -> dict[str, Any]:
    results = [r for c in cited for r in c.validator_results]
    if not results:
        return {"ok": True, "detail": "no validator evidence on cited items", "value": None}
    value = sum(1 for r in results if r) / len(results)
    return {"ok": value == 1.0, "detail": f"{sum(results)}/{len(results)} validator checks passed", "value": value}


def run_all_checks(
    *,
    answer: str,
    no_results: bool,
    question: Any,
    cited: list[CitedItem],
    retrieved: list[RetrievedItem],
    coverage: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    retrieved_ids = {r.id for r in retrieved}
    checks: dict[str, dict[str, Any]] = {
        "answer_kind": check_answer_kind(answer, no_results, len(cited)),
        "abstention": check_abstention(answer, no_results, question.expect_abstain),
        "coverage": check_coverage(coverage),
        "must_not_contain": check_must_not_contain(answer, question.must_not_contain),
        "retrieval_precision": check_retrieval_precision(retrieved, question.topic),
        "retrieval_recall": check_retrieval_recall(retrieved, question.authoritative_sources),
    }
    if not question.expect_abstain:
        checks["required_concepts"] = check_required_concepts(answer, question.required_concepts)
        checks["citations"] = check_citations(cited, retrieved_ids, answer)
        checks["freshness"] = check_freshness(cited, answer)
        checks["contradictions"] = check_contradictions(cited, answer)
        checks["version"] = check_version(answer, question.expected_version)
        checks["validators"] = check_validators(cited)
        checks["negative_knowledge"] = check_negative_knowledge(cited, getattr(question, "negative", False))
    return checks


# Checks whose failure makes the question fail (the rest are informational metrics).
GATING_CHECKS = (
    "abstention",
    "must_not_contain",
    "required_concepts",
    "citations",
    "freshness",
    "contradictions",
    "version",
)


FAILURE_CLASSES = (
    "expected_abstention",  # the question expects a decline and got one (a pass, recorded for the breakdown)
    "coverage_failure",  # the knowledge base holds nothing relevant: nothing to retrieve, cite or answer with
    "retrieval_failure",  # relevant knowledge exists but retrieval did not surface it
    "uncited_answer",  # the model answered without citing any item (from its own memory)
    "answer_generation_failure",  # relevant items were available/cited but the answer is wrong or non-compliant
    "citation_failure",  # citations do not resolve to items with verified evidence
    "validation_failure",  # cited examples failed a domain validator
)


def _covered(checks: dict[str, dict[str, Any]]) -> bool:
    return bool(checks.get("coverage", {}).get("ok", True))


def _retrieval_missed(checks: dict[str, dict[str, Any]]) -> bool:
    """Nothing retrieved, the authoritative source missed, or mostly off-topic results."""
    p = checks.get("retrieval_precision", {})
    rec = checks.get("retrieval_recall", {}).get("value")
    if p.get("retrieved") == 0:
        return True
    return rec == 0.0 or (p.get("value") is not None and p["value"] < 0.5)


def failure_causes(checks: dict[str, dict[str, Any]], judge: dict[str, Any], expect_abstain: bool) -> list[str]:
    """Structured failure analysis (req. 16): what went wrong, in terms an operator can act on.

    Coverage comes first (audit P0.6): when the knowledge base has nothing relevant, retrieval and answer causes
    are noise. An uncited answer is reported as such (audit P0.5) and still gets the judge's findings.
    """
    causes: list[str] = []
    c = checks
    kind = c.get("answer_kind", {}).get("kind", "cited_answer")
    covered = _covered(c)
    if expect_abstain:
        if not c["abstention"]["ok"]:
            causes.append("answered_instead_of_abstaining")
        return causes
    if kind == "abstention":
        if not covered:
            return ["coverage_failure"]
        causes.append("abstained_unexpectedly")
        if c.get("retrieval_recall", {}).get("value") == 0.0:
            causes.append("authoritative_source_not_retrieved")
        if c.get("retrieval_precision", {}).get("value") is not None and c["retrieval_precision"]["value"] < 0.5:
            causes.append("off_topic_retrieval")
        return causes
    if kind == "uncited_answer":
        causes.append("uncited_answer")
        if not covered:
            causes.append("coverage_failure")
    if not covered and c.get("required_concepts") and not c["required_concepts"]["ok"]:
        if "coverage_failure" not in causes:
            causes.append("coverage_failure")
    if kind == "cited_answer" and c.get("citations") and not c["citations"]["ok"]:
        causes.append("invalid_citations")
    if c.get("required_concepts") and not c["required_concepts"]["ok"]:
        causes.append("missing_concepts")
    if covered and c.get("retrieval_recall", {}).get("value") == 0.0:
        causes.append("authoritative_source_not_retrieved")
    if (
        covered
        and c.get("retrieval_precision", {}).get("value") is not None
        and c["retrieval_precision"]["value"] < 0.5
    ):
        causes.append("off_topic_retrieval")
    if c.get("freshness") and not c["freshness"]["ok"]:
        causes.append("stale_cited_without_warning")
    if c.get("contradictions") and not c["contradictions"]["ok"]:
        causes.append("conflict_cited_without_warning")
    if c.get("version") and not c["version"]["ok"]:
        causes.append("wrong_or_missing_version")
    if c.get("validators", {}).get("value") not in (None, 1.0):
        causes.append("validator_failures_cited")
    if c.get("negative_knowledge", {}).get("value") == 0.0:
        causes.append("limitation_not_surfaced")
    if judge and judge.get("correct") is False:
        causes.append("judge_incorrect")
    if judge and judge.get("supported_by_citations") is False and kind == "cited_answer":
        causes.append("judge_unsupported")
    if judge and judge.get("hallucinated_claims"):
        causes.append("hallucination")
    return causes


def failure_class(
    checks: dict[str, dict[str, Any]], judge: dict[str, Any], expect_abstain: bool, passed: bool
) -> str | None:
    """One primary class per question (audit P0.5/P0.6): knowledge does not exist != retriever failed !=
    model answered badly. ``None`` for an ordinary pass; ``expected_abstention`` for a correct decline."""
    c = checks
    kind = c.get("answer_kind", {}).get("kind", "cited_answer")
    if passed:
        return "expected_abstention" if expect_abstain else None
    if expect_abstain:
        return "answer_generation_failure"
    if not _covered(c):
        return "coverage_failure"
    if kind == "abstention":
        return "retrieval_failure" if _retrieval_missed(c) else "answer_generation_failure"
    if kind == "uncited_answer":
        return "uncited_answer"
    if c.get("citations") and not c["citations"]["ok"]:
        return "citation_failure"
    if c.get("validators", {}).get("value") not in (None, 1.0):
        return "validation_failure"
    if _retrieval_missed(c) and c.get("required_concepts") and not c["required_concepts"]["ok"]:
        return "retrieval_failure"
    return "answer_generation_failure"


SUGGESTED_ACTIONS = {
    "coverage_failure": "The knowledge base holds nothing relevant to this question (no items under its topic, "
    "authoritative source not crawled, concepts absent): crawl or add a source that covers it.",
    "uncited_answer": "The model answered from its own memory without citing any item: tighten the answer prompt "
    "and check whether relevant knowledge exists (see coverage).",
    "no_citations": "Knowledge base has no items for this question: add or crawl a source that covers it.",
    "authoritative_source_not_retrieved": "The authoritative page is not in the repository or not extracted: crawl it.",
    "off_topic_retrieval": "Retrieval returned unrelated topics: check taxonomy filing or add a more specific source.",
    "missing_concepts": "Extracted items lack a required concept: review the source page's extraction.",
    "invalid_citations": "Answer cited items without verified evidence: review those items.",
    "stale_cited_without_warning": "Re-verify or retire the stale items; answer prompt must flag staleness.",
    "conflict_cited_without_warning": "Resolve the open conflict on the Review page.",
    "wrong_or_missing_version": "Add product-version information to the items or their source.",
    "validator_failures_cited": "Cited examples failed a domain validator: review them.",
    "limitation_not_surfaced": "The answer did not cite a limitation/warning item: extract or add the negative "
    "knowledge (polarity: negative) that covers this question.",
    "judge_incorrect": "Answer disagrees with the reference: inspect cited items vs. expected answer (one is wrong).",
    "judge_unsupported": "Answer contains claims beyond the citations: tighten the answer prompt or add knowledge.",
    "hallucination": "Answer invented claims: tighten the answer prompt; consider a stronger answer model.",
    "abstained_unexpectedly": "Retrieval found nothing usable: add sources or lower retrieval thresholds.",
    "answered_instead_of_abstaining": "Answer prompt should decline for off-topic items; check retrieval filtering.",
}
