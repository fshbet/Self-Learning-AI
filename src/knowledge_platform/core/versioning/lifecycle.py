"""Knowledge item status lifecycle (§14) and verification levels (§31)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ...models import ALLOWED_TRANSITIONS, ItemStatus, KnowledgeItem, StatusTransition, utcnow


class IllegalTransition(ValueError):
    pass


def transition(
    session: Session,
    item: KnowledgeItem,
    to_status: ItemStatus | str,
    *,
    reason: str = "",
    actor: str = "system",
    force: bool = False,
) -> bool:
    """Move ``item`` to ``to_status`` if the transition is allowed. Returns False when already there."""
    to = ItemStatus(to_status)
    current = ItemStatus(item.status)
    if current == to:
        return False
    if not force and to not in ALLOWED_TRANSITIONS[current]:
        raise IllegalTransition(f"{current} -> {to} is not allowed")
    session.add(
        StatusTransition(knowledge_item_id=item.id, from_status=current, to_status=to, reason=reason, actor=actor)
    )
    item.status = to
    if to == ItemStatus.VERIFIED:
        item.last_verified_at = utcnow()
    if to in (ItemStatus.STALE, ItemStatus.SUPERSEDED, ItemStatus.REJECTED, ItemStatus.CONFLICTED):
        # req. 17: B changed → find dependents → mark affected → (revalidate job)
        from .dependencies import mark_dependents

        mark_dependents(session, item, reason=f"became {to}: {reason}")
    return True


def verification_level(
    *, distinct_sources: int, max_authority: int, validator_passed: bool, human_approved: bool
) -> int:
    """Levels from §31: 0 unverified … 5 expert approved."""
    if distinct_sources == 0:
        return 0
    level = 1
    if max_authority >= 80:
        level = 2
    if distinct_sources >= 2:
        level = max(level, 3)
    if validator_passed:
        level = max(level, 4)
    if human_approved:
        level = 5
    return level


def status_for_level(level: int, *, has_conflict: bool) -> ItemStatus:
    if has_conflict:
        return ItemStatus.CONFLICTED
    if level >= 2:
        return ItemStatus.VERIFIED
    if level >= 1:
        return ItemStatus.SUPPORTED
    return ItemStatus.CANDIDATE


def advance_to(session: Session, item: KnowledgeItem, target: ItemStatus, *, reason: str, actor: str) -> None:
    """Walk the item forward through the legal chain EXTRACTED→CANDIDATE→SUPPORTED→VERIFIED as far as ``target``."""
    order = [ItemStatus.EXTRACTED, ItemStatus.CANDIDATE, ItemStatus.SUPPORTED, ItemStatus.VERIFIED]
    if target not in order:
        transition(session, item, target, reason=reason, actor=actor)
        return
    current = ItemStatus(item.status)
    if current not in order:
        # e.g. STALE/CONFLICTED being re-verified
        transition(session, item, target, reason=reason, actor=actor)
        return
    for nxt in order[order.index(current) + 1 : order.index(target) + 1]:
        transition(session, item, nxt, reason=reason, actor=actor)
