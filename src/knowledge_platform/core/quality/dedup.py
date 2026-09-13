"""Duplicate detection: exact statement hash, then near-duplicate by embedding distance.

A near-duplicate must satisfy *all* of:
  * same normalized subject (different functions with templated wording are never duplicates),
  * cosine distance below the configured threshold,
  * equivalent objects (every content word of one object appears in the other statement),
  * sufficient lexical overlap between the statements (embeddings alone over-merge templated sentences).
"""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...models import ItemStatus, KnowledgeItem

_LIVE = (
    ItemStatus.EXTRACTED,
    ItemStatus.CANDIDATE,
    ItemStatus.SUPPORTED,
    ItemStatus.VERIFIED,
    ItemStatus.CONFLICTED,
    ItemStatus.STALE,
)
_TOKEN = re.compile(r"[a-z0-9]+")
MIN_LEXICAL_OVERLAP = 0.6  # Jaccard similarity of statement tokens


def _tokens(s: str) -> set[str]:
    return set(_TOKEN.findall((s or "").lower()))


def lexical_overlap(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


_STOPWORDS = frozenset(
    "a an the of in on at to for by with and or is are was were be been being that this these those it its as from "
    "into than then when which who whom whose what where while can may might must shall should will would do does did "
    "has have had not no nor so such very".split()
)


def _content(s: str) -> set[str]:
    return {t for t in _tokens(s) if t not in _STOPWORDS}


def objects_equivalent(a: KnowledgeItem, b: KnowledgeItem) -> bool:
    """Every content word of each object must appear somewhere in the other item's statement or object.

    "financial functions" vs "other functions" differ ("financial" is absent from the other side), while
    "filter context" vs "filter context that has been modified" are equivalent.
    """
    a_all = _tokens(a.statement) | _tokens(a.object)
    b_all = _tokens(b.statement) | _tokens(b.object)
    return _content(a.object) <= b_all and _content(b.object) <= a_all


def is_near_duplicate(a: KnowledgeItem, b: KnowledgeItem, distance: float, *, max_distance: float) -> bool:
    if distance > max_distance:
        return False
    if (a.subject or "").strip().lower() != (b.subject or "").strip().lower():
        return False
    if not objects_equivalent(a, b):
        return False
    return lexical_overlap(a.statement, b.statement) >= MIN_LEXICAL_OVERLAP


def find_exact(session: Session, domain_id: str, content_hash: str) -> KnowledgeItem | None:
    return session.execute(
        select(KnowledgeItem)
        .where(
            KnowledgeItem.domain_id == domain_id,
            KnowledgeItem.content_hash == content_hash,
            KnowledgeItem.status.in_(_LIVE),
        )
        .order_by(KnowledgeItem.created_at)
        .limit(1)
    ).scalar_one_or_none()


def find_near(
    session: Session,
    domain_id: str,
    item: KnowledgeItem,
    *,
    max_distance: float | None = None,
    candidates: int = 5,
) -> tuple[KnowledgeItem, float] | None:
    """Closest live item that passes all near-duplicate guards, or None."""
    if item.embedding is None:
        return None
    max_distance = max_distance if max_distance is not None else get_settings().near_duplicate_distance
    dist = KnowledgeItem.embedding.cosine_distance(item.embedding).label("dist")
    stmt = (
        select(KnowledgeItem, dist)
        .where(
            KnowledgeItem.domain_id == domain_id,
            KnowledgeItem.id != item.id,
            KnowledgeItem.embedding.is_not(None),
            KnowledgeItem.status.in_(_LIVE),
            func.lower(KnowledgeItem.subject) == (item.subject or "").strip().lower(),
        )
        .order_by(dist)
        .limit(candidates)
    )
    for other, d in session.execute(stmt).all():
        if d is not None and is_near_duplicate(item, other, float(d), max_distance=max_distance):
            return other, float(d)
    return None
