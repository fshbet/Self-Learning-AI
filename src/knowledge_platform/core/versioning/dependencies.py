"""Knowledge dependency graph (req. 17).

    Knowledge A ──depends_on──▶ Knowledge B

When B changes (stale, superseded, rejected, conflicted, new version) every A that depends on it is flagged
``needs_revalidation`` with the reason, and a ``revalidate_item`` job re-runs validators, scoring and
contradiction detection for A. The flag is cleared only when all of A's dependencies are live again.

Relations are derived automatically from structure (examples and procedures depend on the facts and definitions
about the same subject; DERIVED items point at their sources) and can be added by hand through the API.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ...models import ItemStatus, KnowledgeItem, KnowledgeRelation

log = logging.getLogger(__name__)

RELATION_TYPES = (
    "depends_on",
    "example_of",
    "derived_from",
    "related_to",
    "supersedes",
    "contradicts",
    "compatible_under",  # both true under different versions / scopes / conditions (details say which)
)
# relation types along which a change in the target invalidates the source
PROPAGATING = ("depends_on", "example_of", "derived_from")
LIVE = (ItemStatus.SUPPORTED, ItemStatus.VERIFIED)
_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _WS.sub(" ", (s or "").strip().lower())


def add_relation(
    session: Session,
    from_item: KnowledgeItem,
    to_item: KnowledgeItem,
    relation_type: str,
    *,
    origin: str = "system",
    details: dict[str, Any] | None = None,
) -> KnowledgeRelation | None:
    if relation_type not in RELATION_TYPES:
        raise ValueError(f"unknown relation type {relation_type!r}")
    if from_item.id == to_item.id or from_item.domain_id != to_item.domain_id:
        return None
    existing = session.execute(
        select(KnowledgeRelation).where(
            KnowledgeRelation.from_item_id == from_item.id,
            KnowledgeRelation.to_item_id == to_item.id,
            KnowledgeRelation.relation_type == relation_type,
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    rel = KnowledgeRelation(
        domain_id=from_item.domain_id,
        from_item_id=from_item.id,
        to_item_id=to_item.id,
        relation_type=relation_type,
        origin=origin,
        details=details or {},
    )
    session.add(rel)
    session.flush()
    return rel


def derive_relations(session: Session, item: KnowledgeItem, plugin: Any) -> list[KnowledgeRelation]:
    """Structural relations for a new/updated item (see module docstring). Which types are foundations, dependents
    or examples is the plugin's declaration (audit P1.5), never a core assumption."""
    created: list[KnowledgeRelation] = []
    foundation = plugin.types_with_role("foundation")
    dependent = plugin.types_with_role("dependent", "example")
    example = plugin.types_with_role("example")
    subject = _norm(item.subject)
    if not subject:
        return created
    same_subject = (
        session.execute(
            select(KnowledgeItem).where(
                KnowledgeItem.domain_id == item.domain_id,
                KnowledgeItem.id != item.id,
                KnowledgeItem.status.in_(LIVE + (ItemStatus.CONFLICTED, ItemStatus.STALE, ItemStatus.CANDIDATE)),
                func.lower(KnowledgeItem.subject) == item.subject.strip().lower(),  # exact, never a LIKE pattern
            )
        )
        .scalars()
        .all()
    )
    if item.knowledge_type in dependent:
        for other in same_subject:
            if other.knowledge_type in foundation:
                rtype = "example_of" if item.knowledge_type in example else "depends_on"
                rel = add_relation(session, item, other, rtype, details={"rule": "same-subject foundation"})
                if rel:
                    created.append(rel)
    elif item.knowledge_type in foundation:
        # a new foundation item: existing dependents about the same subject now depend on it too
        for other in same_subject:
            if other.knowledge_type in dependent:
                rtype = "example_of" if other.knowledge_type in example else "depends_on"
                rel = add_relation(session, other, item, rtype, details={"rule": "same-subject foundation"})
                if rel:
                    created.append(rel)
    return created


def dependents_of(session: Session, item_id: uuid.UUID) -> list[KnowledgeItem]:
    """Items that point at ``item_id`` with a propagating relation."""
    ids = list(
        session.execute(
            select(KnowledgeRelation.from_item_id).where(
                KnowledgeRelation.to_item_id == item_id, KnowledgeRelation.relation_type.in_(PROPAGATING)
            )
        ).scalars()
    )
    if not ids:
        return []
    return list(session.execute(select(KnowledgeItem).where(KnowledgeItem.id.in_(ids))).scalars())


def dependencies_of(session: Session, item_id: uuid.UUID) -> list[tuple[KnowledgeRelation, KnowledgeItem]]:
    rows = session.execute(
        select(KnowledgeRelation, KnowledgeItem)
        .join(KnowledgeItem, KnowledgeItem.id == KnowledgeRelation.to_item_id)
        .where(KnowledgeRelation.from_item_id == item_id)
    ).all()
    return [(r, k) for r, k in rows]


MAX_PROPAGATION_DEPTH = 50  # safety cap; real graphs are a handful of hops deep


def affected_by(session: Session, item_id: uuid.UUID) -> list[tuple[uuid.UUID, uuid.UUID, int]]:
    """Every item that transitively depends on ``item_id`` (audit P1.7): (dependent id, parent id, hops).

    Breadth-first over the propagating relations, so each item is reported once at its shortest distance;
    the visited set makes cycles and diamonds safe, sorted expansion makes the order deterministic.
    """
    visited: set[uuid.UUID] = {item_id}
    frontier: list[uuid.UUID] = [item_id]
    out: list[tuple[uuid.UUID, uuid.UUID, int]] = []
    hops = 0
    while frontier and hops < MAX_PROPAGATION_DEPTH:
        hops += 1
        rows = session.execute(
            select(KnowledgeRelation.from_item_id, KnowledgeRelation.to_item_id)
            .where(KnowledgeRelation.to_item_id.in_(frontier), KnowledgeRelation.relation_type.in_(PROPAGATING))
            .order_by(KnowledgeRelation.to_item_id, KnowledgeRelation.from_item_id)
        ).all()
        next_frontier: list[uuid.UUID] = []
        for dependent, parent in rows:
            if dependent in visited:
                continue
            visited.add(dependent)
            next_frontier.append(dependent)
            out.append((dependent, parent, hops))
        frontier = sorted(next_frontier, key=str)
    return out


def mark_dependents(session: Session, item: KnowledgeItem, reason: str) -> int:
    """Flag everything that depends on ``item`` — directly or through other items (req. 17, audit P1.7).

    Only the *dependency* flag is touched: review flags (falsification, manual, quality) belong to a reviewer.
    An item already flagged for this same root keeps its reason (no churn); its descendants are still visited.
    """
    count = 0
    root_tag = f"dependency {item.id}"
    affected = affected_by(session, item.id)
    if not affected:
        return 0
    items = {
        k.id: k
        for k in session.execute(
            select(KnowledgeItem).where(KnowledgeItem.id.in_([d for d, _, _ in affected]))
        ).scalars()
    }
    for dep_id, parent_id, hops in affected:
        dep = items.get(dep_id)
        if dep is None or ItemStatus(dep.status) in (ItemStatus.REJECTED, ItemStatus.SUPERSEDED):
            continue
        if dep.needs_revalidation and (dep.revalidation_reason or "").startswith(root_tag):
            continue
        via = "" if hops == 1 else f" via {parent_id} ({hops} hops)"
        dep.needs_revalidation = True
        dep.revalidation_reason = f"{root_tag} ({item.subject}){via}: {reason}"[:500]
        count += 1
    if count:
        session.flush()
        log.info("flagged %d dependents of %s for revalidation (transitive)", count, item.id)
    return count


def _reaches(session: Session, start: uuid.UUID, target: uuid.UUID) -> bool:
    """Is there a propagating path start -> ... -> target (i.e. is ``target`` on a cycle with ``start``)?"""
    return any(d == target for d, _, _ in affected_by(session, start))


def unresolved_dependencies(session: Session, item: KnowledgeItem) -> list[KnowledgeItem]:
    """Dependencies that are not settled: not live (stale, conflicted, rejected, superseded), or themselves still
    awaiting revalidation — unless that dependency lies on a cycle back to ``item`` (then the flag alone must not
    hold both hostage; its liveness decides). A superseded dependency whose successor relation was carried over
    is settled by the successor, except for ``derived_from`` (see versioning/supersede.py)."""
    out: list[KnowledgeItem] = []
    deps = dependencies_of(session, item.id)
    present = {(r.relation_type, k.id) for r, k in deps}
    for r, k in deps:
        if r.relation_type not in PROPAGATING:
            continue
        if (
            k.superseded_by_id
            and (r.relation_type, k.superseded_by_id) in present
            and r.relation_type != "derived_from"
        ):
            # superseded, and the relation was carried to the new version: the current version decides (P2.1).
            # A derived conclusion is the exception — its premise changed, a reviewer must re-derive it.
            continue
        if ItemStatus(k.status) not in LIVE:
            out.append(k)
        elif k.needs_revalidation and not _reaches(session, item.id, k.id):
            out.append(k)
    return out


def relations_for_api(session: Session, item_id: uuid.UUID) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {"outgoing": [], "incoming": []}
    rows = session.execute(
        select(KnowledgeRelation).where(
            or_(KnowledgeRelation.from_item_id == item_id, KnowledgeRelation.to_item_id == item_id)
        )
    ).scalars()
    for r in rows:
        other_id = r.to_item_id if r.from_item_id == item_id else r.from_item_id
        other = session.get(KnowledgeItem, other_id)
        entry = {
            "id": str(r.id),
            "relation_type": r.relation_type,
            "origin": r.origin,
            "item_id": str(other_id),
            "statement": other.statement if other else "",
            "status": other.status if other else "",
            "knowledge_type": other.knowledge_type if other else "",
        }
        out["outgoing" if r.from_item_id == item_id else "incoming"].append(entry)
    return out
