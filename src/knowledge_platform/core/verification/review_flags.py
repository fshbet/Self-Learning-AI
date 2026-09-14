"""Review flags (audit P0.2): human/security/quality concerns that only a person may clear.

Two independent kinds of state live on a knowledge item:

* ``needs_revalidation`` / ``revalidation_reason`` — *dependency* state, owned by the versioning code. The scheduler
  re-runs validators, scoring and conflict checks and clears it once every dependency is live again.
* ``needs_review`` / ``review_kind`` / ``review_reason`` / ``review_flagged_at`` — a *review* flag raised by active
  falsification (``falsification``), a person (``manual``) or a quality rule (``quality``). Nothing automatic clears it:
  it stays until a reviewer resolves or dismisses it, and every resolution is appended to ``details["review_log"]``
  so the export keeps the history.
"""

from __future__ import annotations

from typing import Any

from ...models import KnowledgeItem, utcnow

REVIEW_KINDS = ("falsification", "manual", "quality")
REVIEW_LOG_KEY = "review_log"
REVIEW_LOG_LIMIT = 50


def flag_for_review(item: KnowledgeItem, kind: str, reason: str) -> None:
    """Raise (or refresh) the review flag. A newer reason replaces the text; the first flag time is kept."""
    if kind not in REVIEW_KINDS:
        raise ValueError(f"unknown review kind {kind!r}")
    if not item.needs_review:
        item.review_flagged_at = utcnow()
    item.needs_review = True
    item.review_kind = kind
    item.review_reason = reason[:500]


def resolve_review(item: KnowledgeItem, *, resolved_by: str, resolution: str, action: str) -> dict[str, Any] | None:
    """Explicitly clear the review flag and record who did what. Returns the log entry, or None if not flagged."""
    if not item.needs_review:
        return None
    entry = {
        "kind": item.review_kind,
        "reason": item.review_reason,
        "flagged_at": item.review_flagged_at.isoformat() if item.review_flagged_at else None,
        "resolved_at": utcnow().isoformat(),
        "resolved_by": resolved_by,
        "action": action,
        "resolution": (resolution or "")[:500],
    }
    log = list((item.details or {}).get(REVIEW_LOG_KEY) or [])
    log.append(entry)
    item.details = {**(item.details or {}), REVIEW_LOG_KEY: log[-REVIEW_LOG_LIMIT:]}
    item.needs_review = False
    item.review_kind = None
    item.review_reason = None
    item.review_flagged_at = None
    return entry


__all__ = ["REVIEW_KINDS", "REVIEW_LOG_KEY", "flag_for_review", "resolve_review"]
