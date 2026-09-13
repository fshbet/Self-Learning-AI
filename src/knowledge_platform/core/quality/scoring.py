"""Explainable quality scoring (§16). Factors are stored with the score."""

from __future__ import annotations

from typing import Any

SCORING_RULE_VERSION = "score@1.0"

_WEIGHTS = {
    "source_authority": 0.35,
    "evidence_verified": 0.20,
    "source_agreement": 0.15,
    "specificity": 0.10,
    "taxonomy_match": 0.05,
    "domain_validation": 0.15,
}


def specificity(statement: str, code: str | None) -> float:
    """Longer, concrete statements with numbers/identifiers/code score higher."""
    n = len(statement)
    score = 0.3 if n < 40 else 0.6 if n < 90 else 0.8
    if any(ch.isdigit() for ch in statement):
        score += 0.1
    if code:
        score += 0.1
    return min(score, 1.0)


def score(
    *,
    source_authority: int,
    evidence_verified: bool,
    distinct_sources: int,
    statement: str,
    code: str | None,
    topic_matched: bool,
    validator_results: list[dict[str, Any]] | None = None,
) -> tuple[float, dict[str, Any]]:
    validator_results = validator_results or []
    if validator_results:
        dv = 1.0 if all(v.get("passed") for v in validator_results) else 0.0
    else:
        dv = 0.5  # unknown: neither rewarded nor punished fully
    factors = {
        "source_authority": max(0.0, min(source_authority, 100)) / 100.0,
        "evidence_verified": 1.0 if evidence_verified else 0.0,
        "source_agreement": min(distinct_sources, 3) / 3.0,
        "specificity": specificity(statement, code),
        "taxonomy_match": 1.0 if topic_matched else 0.0,
        "domain_validation": dv,
    }
    total = sum(_WEIGHTS[k] * v for k, v in factors.items())
    factors["weights"] = _WEIGHTS
    factors["rule_version"] = SCORING_RULE_VERSION
    return round(total, 4), factors
