"""Explainable quality scoring (§16). Factors are stored with the score."""

from __future__ import annotations

from typing import Any

SCORING_RULE_VERSION = "score@2.0"

_WEIGHTS = {
    "source_authority": 0.30,
    "evidence_verified": 0.15,
    "source_agreement": 0.15,
    "specificity": 0.08,
    "taxonomy_match": 0.04,
    "domain_validation": 0.13,
    "freshness": 0.08,  # time since last verification (req. 14, 31)
    "contradiction_status": 0.05,  # an open conflict lowers trust (req. 12, 14)
    "version_known": 0.02,  # a product version is recorded (req. 14 "version match")
}


def freshness(last_confirmed_at, *, is_stale: bool, now=None) -> float:
    """How recently the source was seen to still state the claim (audit P1.4): 1.0 within 30 days, decaying to 0.2
    after half a year; STALE is 0. ``last_confirmed_at`` is ``last_source_checked_at`` (an unchanged or still-matching
    re-crawl), falling back to ``last_verified_at`` and finally to discovery — it never implies a new verification."""
    from datetime import UTC, datetime

    if is_stale:
        return 0.0
    if last_confirmed_at is None:
        return 0.5
    now = now or datetime.now(UTC)
    if last_confirmed_at.tzinfo is None:
        last_confirmed_at = last_confirmed_at.replace(tzinfo=UTC)
    days = (now - last_confirmed_at).days
    return 1.0 if days < 30 else 0.7 if days < 90 else 0.4 if days < 180 else 0.2


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
    last_verified_at=None,  # last confirmation of the claim (see freshness); name kept for the factor
    is_stale: bool = False,
    has_open_conflict: bool = False,
    version_known: bool = False,
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
        "freshness": freshness(last_verified_at, is_stale=is_stale),
        "contradiction_status": 0.3 if has_open_conflict else 1.0,
        "version_known": 1.0 if version_known else 0.6,
    }
    total = sum(_WEIGHTS[k] * v for k, v in factors.items())
    factors["weights"] = _WEIGHTS
    factors["rule_version"] = SCORING_RULE_VERSION
    return round(total, 4), factors
