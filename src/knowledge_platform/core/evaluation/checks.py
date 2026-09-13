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


def is_abstention(answer: str, insufficient_flag: bool) -> bool:
    a = _norm(answer)
    return insufficient_flag or any(p in a for p in _ABSTAIN_PATTERNS)


def check_abstention(answer: str, insufficient_flag: bool, expect_abstain: bool) -> dict[str, Any]:
    abstained = is_abstention(answer, insufficient_flag)
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
        return {"ok": True, "detail": "n/a", "value": None}
    top = topic.split("/")[0].lower()
    hits = sum(1 for r in retrieved if (r.topic or "").lower().startswith(top))
    value = hits / len(retrieved)
    return {
        "ok": value >= 0.5,
        "detail": f"{hits}/{len(retrieved)} retrieved items match topic '{topic}'",
        "value": value,
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


def check_validators(cited: list[CitedItem]) -> dict[str, Any]:
    results = [r for c in cited for r in c.validator_results]
    if not results:
        return {"ok": True, "detail": "no validator evidence on cited items", "value": None}
    value = sum(1 for r in results if r) / len(results)
    return {"ok": value == 1.0, "detail": f"{sum(results)}/{len(results)} validator checks passed", "value": value}


def run_all_checks(
    *,
    answer: str,
    insufficient_flag: bool,
    question: Any,
    cited: list[CitedItem],
    retrieved: list[RetrievedItem],
) -> dict[str, dict[str, Any]]:
    retrieved_ids = {r.id for r in retrieved}
    checks: dict[str, dict[str, Any]] = {
        "abstention": check_abstention(answer, insufficient_flag, question.expect_abstain),
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


def failure_causes(checks: dict[str, dict[str, Any]], judge: dict[str, Any], expect_abstain: bool) -> list[str]:
    """Structured failure analysis (req. 16): what went wrong, in terms an operator can act on."""
    causes: list[str] = []
    c = checks
    if not c["abstention"]["ok"]:
        causes.append("abstained_unexpectedly" if not expect_abstain else "answered_instead_of_abstaining")
    if expect_abstain:
        return causes
    if c["abstention"].get("abstained"):
        # the answer declined: only retrieval explains why; concept/citation checks would be noise
        if c.get("retrieval_recall", {}).get("value") == 0.0:
            causes.append("authoritative_source_not_retrieved")
        if c.get("retrieval_precision", {}).get("value") is not None and c["retrieval_precision"]["value"] < 0.5:
            causes.append("off_topic_retrieval")
        return causes
    if c.get("citations") and c["citations"]["cited"] == 0:
        causes.append("no_citations")
    elif c.get("citations") and not c["citations"]["ok"]:
        causes.append("invalid_citations")
    if c.get("required_concepts") and not c["required_concepts"]["ok"]:
        causes.append("missing_concepts")
    if c.get("retrieval_recall", {}).get("value") == 0.0:
        causes.append("authoritative_source_not_retrieved")
    if c.get("retrieval_precision", {}).get("value") is not None and c["retrieval_precision"]["value"] < 0.5:
        causes.append("off_topic_retrieval")
    if c.get("freshness") and not c["freshness"]["ok"]:
        causes.append("stale_cited_without_warning")
    if c.get("contradictions") and not c["contradictions"]["ok"]:
        causes.append("conflict_cited_without_warning")
    if c.get("version") and not c["version"]["ok"]:
        causes.append("wrong_or_missing_version")
    if c.get("validators", {}).get("value") not in (None, 1.0):
        causes.append("validator_failures_cited")
    if judge and judge.get("correct") is False:
        causes.append("judge_incorrect")
    if judge and judge.get("supported_by_citations") is False:
        causes.append("judge_unsupported")
    if judge and judge.get("hallucinated_claims"):
        causes.append("hallucination")
    return causes


SUGGESTED_ACTIONS = {
    "no_citations": "Knowledge base has no items for this question: add or crawl a source that covers it.",
    "authoritative_source_not_retrieved": "The authoritative page is not in the repository or not extracted: crawl it.",
    "off_topic_retrieval": "Retrieval returned unrelated topics: check taxonomy filing or add a more specific source.",
    "missing_concepts": "Extracted items lack a required concept: review the source page's extraction.",
    "invalid_citations": "Answer cited items without verified evidence: review those items.",
    "stale_cited_without_warning": "Re-verify or retire the stale items; answer prompt must flag staleness.",
    "conflict_cited_without_warning": "Resolve the open conflict on the Review page.",
    "wrong_or_missing_version": "Add product-version information to the items or their source.",
    "validator_failures_cited": "Cited examples failed a domain validator: review them.",
    "judge_incorrect": "Answer disagrees with the reference: inspect cited items vs. expected answer (one is wrong).",
    "judge_unsupported": "Answer contains claims beyond the citations: tighten the answer prompt or add knowledge.",
    "hallucination": "Answer invented claims: tighten the answer prompt; consider a stronger answer model.",
    "abstained_unexpectedly": "Retrieval found nothing usable: add sources or lower retrieval thresholds.",
    "answered_instead_of_abstaining": "Answer prompt should decline for off-topic items; check retrieval filtering.",
}
