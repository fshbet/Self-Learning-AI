"""Knowledge origin, provenance level and polarity (req. 8, 9, 19).

Until items carry explicit columns (P2), these are derived from the item's sources and type. The same
rules are used to backfill the columns, so exports and the database agree.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

OFFICIAL_AUTHORITY = 80


CLASS_TO_PROVENANCE = {
    "official": "OFFICIAL",
    "external": "EXTERNAL",
    "community": "COMMUNITY",
    "organization": "ORGANIZATION",
}


def source_class_for(authority: int, origin: str = "plugin") -> str:
    """Default class of a source when the catalog does not declare one."""
    if origin == "discovered":
        return "community"
    return "official" if int(authority or 0) >= OFFICIAL_AUTHORITY else "external"


def derive_provenance(sources: Iterable[Any]) -> str:
    """Provenance level of knowledge evidenced by ``sources`` (Source rows).

    The level follows the *class* of the most authoritative source (official / external / community /
    organization). A URL added by a user is still external content; ``USER`` is reserved for knowledge a
    person authored directly (see core/knowledge_entry).
    """
    srcs = list(sources)
    if not srcs:
        return "DERIVED"
    best = max(srcs, key=lambda s: int(getattr(s, "authority", 0) or 0))
    cls = getattr(best, "source_class", None) or source_class_for(
        int(getattr(best, "authority", 0) or 0), getattr(best, "origin", "plugin")
    )
    return CLASS_TO_PROVENANCE.get(str(cls), "EXTERNAL")


def derive_polarity(knowledge_type: str, plugin: Any | None = None) -> str:
    """Polarity of a knowledge type as the plugin declares it (audit P1.5); without a plugin, the conventional
    vocabulary's defaults apply."""
    if plugin is not None:
        return plugin.polarity_of(knowledge_type or "")
    from ..plugins.base import DEFAULT_TYPE_SPECS

    return DEFAULT_TYPE_SPECS.get((knowledge_type or "").lower(), {}).get("polarity", "positive")


def derive_origin(item: Any) -> str:
    """Extracted items with verbatim evidence are DIRECT; validator-passed examples are EXPERIMENTALLY_VALIDATED."""
    explicit = getattr(item, "origin", None)
    if explicit:
        return str(explicit)
    evidence = getattr(item, "evidence", []) or []
    if any(e.evidence_type == "validator" and e.details.get("passed") for e in evidence):
        return "EXPERIMENTALLY_VALIDATED"
    return "DIRECT"
