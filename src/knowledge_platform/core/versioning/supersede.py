"""Supersede-by-new-version (P2.1): a changed claim becomes the next version of the item it replaces.

Before this module a changed page produced two unrelated items: the old one went STALE (its quote vanished) and
the new statement became a fresh item. Now the two are linked::

    old version  ──superseded_by──▶  new version      old.superseded_by_id / new.previous_version_id / new.version+1
    new version  ──supersedes─────▶  old version      relation row, exported like every other relation

The old item is never deleted: it moves to SUPERSEDED, keeps its evidence and its history, is excluded from
retrieval, and is exported as *historical* (``usage: historical``) with the successor's id.

Automatic linking is deliberately conservative. A new item extracted from a page supersedes a STALE item only
when both are about the same subject with the same core predicate, the stale item's quote vanished from *that*
page, and the pairing is unambiguous (exactly one candidate on each side). Everything else stays STALE for a
reviewer, who can link versions explicitly through the review API.

Dependencies follow the current version: every propagating relation that pointed at the old item is carried to
the new one, and the dependents are flagged for revalidation (the STALE/SUPERSEDED transition does that). The
``revalidate_item`` job then re-runs validators, scoring and conflict detection against the new version and clears
the flag when it is live — except for DERIVED / SYNTHESIZED conclusions, whose premise changed: those wait for a
reviewer (approve clears the flag), because a conclusion drawn from a claim that no longer reads the same must
be re-derived by a person, not by a scheduler.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import Document, Evidence, ItemStatus, KnowledgeItem, KnowledgeRelation
from ..verification.conflicts import predicate_core
from .dependencies import PROPAGATING, _norm, add_relation
from .lifecycle import transition

log = logging.getLogger(__name__)

LINKABLE = (ItemStatus.SUPPORTED, ItemStatus.VERIFIED, ItemStatus.CONFLICTED, ItemStatus.CANDIDATE)


def version_key(item: KnowledgeItem) -> tuple[str, str, str]:
    """What makes two statements versions of the same claim: subject, core predicate, polarity."""
    core, negated = predicate_core(item.predicate or "")
    polarity = "negative" if negated or item.polarity == "negative" else "positive"
    return (_norm(item.subject), core, polarity)


def supersede(
    session: Session,
    old: KnowledgeItem,
    new: KnowledgeItem,
    *,
    reason: str,
    actor: str = "system:versioning",
) -> bool:
    """Link ``old`` → ``new`` as consecutive versions. Returns False when nothing was done (already linked, same
    item, different domains, or ``new`` is not a live item)."""
    if old.id == new.id or old.domain_id != new.domain_id or old.superseded_by_id is not None:
        return False
    if ItemStatus(new.status) not in LINKABLE or ItemStatus(old.status) in (ItemStatus.REJECTED,):
        return False
    old.superseded_by_id = new.id
    new.previous_version_id = old.id
    new.version = max(int(new.version or 1), int(old.version or 1) + 1)
    add_relation(session, new, old, "supersedes", origin="system", details={"reason": reason})
    # dependencies follow the current version; the dependents are flagged by the transition below
    carried = 0
    for rel in session.execute(
        select(KnowledgeRelation).where(
            KnowledgeRelation.to_item_id == old.id, KnowledgeRelation.relation_type.in_(PROPAGATING)
        )
    ).scalars():
        if rel.from_item_id == new.id:
            continue
        source = session.get(KnowledgeItem, rel.from_item_id)
        if source is None:
            continue
        if add_relation(
            session,
            source,
            new,
            rel.relation_type,
            origin=rel.origin,
            details={**(rel.details or {}), "carried_from": str(old.id)},
        ):
            carried += 1
    transition(session, old, ItemStatus.SUPERSEDED, reason=f"superseded by {new.id}: {reason}", actor=actor)
    session.flush()
    log.info("%s superseded by %s (%s); %d dependency relation(s) carried over", old.id, new.id, reason, carried)
    return True


def link_superseded(
    session: Session, doc: Document, new_items: list[KnowledgeItem], *, actor: str = "system:versioning"
) -> list[tuple[KnowledgeItem, KnowledgeItem]]:
    """After a page changed: pair each new item from the page with the one STALE item whose quote vanished from the
    same page and that states the same subject + core predicate. Ambiguous groups are left alone."""
    if not new_items:
        return []
    stale = (
        session.execute(
            select(KnowledgeItem).where(
                KnowledgeItem.domain_id == doc.domain_id,
                KnowledgeItem.status == ItemStatus.STALE,
                KnowledgeItem.superseded_by_id.is_(None),
                KnowledgeItem.id.in_(
                    select(Evidence.knowledge_item_id).where(
                        Evidence.document_id == doc.id,
                        Evidence.evidence_type == "extraction",
                        Evidence.verified.is_(False),
                    )
                ),
            )
        )
        .scalars()
        .all()
    )
    if not stale:
        return []
    by_key: dict[tuple[str, str, str], list[KnowledgeItem]] = defaultdict(list)
    for item in stale:
        by_key[version_key(item)].append(item)
    fresh: dict[tuple[str, str, str], list[KnowledgeItem]] = defaultdict(list)
    for item in new_items:
        if ItemStatus(item.status) in LINKABLE:
            fresh[version_key(item)].append(item)
    linked: list[tuple[KnowledgeItem, KnowledgeItem]] = []
    for key, candidates in fresh.items():
        olds = by_key.get(key) or []
        if len(candidates) != 1 or len(olds) != 1:
            if olds:
                log.info(
                    "supersession ambiguous for %s: %d new vs %d stale, left for review",
                    key,
                    len(candidates),
                    len(olds),
                )
            continue
        old, new = olds[0], candidates[0]
        if _norm(old.statement) == _norm(new.statement):
            continue  # identical text is a dedup matter, not a new version
        if supersede(session, old, new, reason=f"claim changed in {doc.url}", actor=actor):
            linked.append((old, new))
    return linked


__all__ = ["LINKABLE", "link_superseded", "supersede", "version_key"]
