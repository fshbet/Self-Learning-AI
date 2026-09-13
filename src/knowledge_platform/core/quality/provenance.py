"""Knowledge origin, provenance level and polarity (req. 8, 9, 19).

Until items carry explicit columns (P2), these are derived from the item's sources and type. The same
rules are used to backfill the columns, so exports and the database agree.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

NEGATIVE_TYPES = frozenset({"limitation", "warning", "anti_pattern", "common_mistake", "pitfall"})
OFFICIAL_AUTHORITY = 80


def derive_provenance(sources: Iterable[Any]) -> str:
    """``sources``: Source rows (or objects with ``origin`` and ``authority``) that evidence the item."""
    srcs = list(sources)
    if not srcs:
        return "DERIVED"
    origins = {getattr(s, "origin", "plugin") for s in srcs}
    if "user" in origins and origins == {"user"}:
        return "USER"
    best = max(srcs, key=lambda s: int(getattr(s, "authority", 0) or 0))
    if getattr(best, "origin", "plugin") == "discovered":
        return "COMMUNITY"
    if int(getattr(best, "authority", 0) or 0) >= OFFICIAL_AUTHORITY:
        return "OFFICIAL"
    return "EXTERNAL"


def derive_polarity(knowledge_type: str) -> str:
    return "negative" if (knowledge_type or "").lower() in NEGATIVE_TYPES else "positive"


def derive_origin(item: Any) -> str:
    """Extracted items with verbatim evidence are DIRECT; validator-passed examples are EXPERIMENTALLY_VALIDATED."""
    explicit = getattr(item, "origin", None)
    if explicit:
        return str(explicit)
    evidence = getattr(item, "evidence", []) or []
    if any(e.evidence_type == "validator" and e.details.get("passed") for e in evidence):
        return "EXPERIMENTALLY_VALIDATED"
    return "DIRECT"
