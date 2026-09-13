"""Contradiction detection (§18).

Candidates share subject + predicate and differ in object. Before a conflict record is
opened, three checks avoid false positives:

1. Items whose evidence comes from the same document are list members, not contradictions.
2. A (subject, predicate) pair with many distinct objects is a multi-valued predicate ("covers", "includes").
3. Remaining pairs are adjudicated by the model: only a "contradict" verdict opens a conflict;
   "different_conditions" is recorded as compatible-under-conditions (§18 "both valid under different conditions").
"""

from __future__ import annotations

import logging
import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...models import Conflict, Evidence, ItemStatus, KnowledgeItem
from ..extraction.prompts import CONFLICT_SCHEMA, CONFLICT_SYSTEM, CONFLICT_USER
from ..llm_service import call_json
from ..versioning.lifecycle import transition

log = logging.getLogger(__name__)

_WS = re.compile(r"\s+")
_LIVE = (ItemStatus.SUPPORTED, ItemStatus.VERIFIED, ItemStatus.CONFLICTED, ItemStatus.CANDIDATE)
MULTI_VALUED_THRESHOLD = 3  # >= this many distinct objects for one subject+predicate => not a contradiction


def _norm(s: str) -> str:
    return _WS.sub(" ", (s or "").strip().lower()).rstrip(".")


def _document_ids(session: Session, item_id: uuid.UUID) -> set[uuid.UUID]:
    rows = session.execute(
        select(Evidence.document_id).where(
            Evidence.knowledge_item_id == item_id,
            Evidence.document_id.is_not(None),
            Evidence.verified.is_(True),  # evidence from an older version of a changed page no longer counts
        )
    ).scalars()
    return set(rows)


def judge(session: Session, domain_name: str, a: KnowledgeItem, b: KnowledgeItem, *, run_id=None) -> tuple[str, str]:
    """Ask the model whether two statements contradict. Falls back to 'compatible' on model failure."""
    try:
        data = call_json(
            purpose="reason",
            system=CONFLICT_SYSTEM.format(domain_name=domain_name),
            user=CONFLICT_USER.format(a=a.statement, b=b.statement),
            schema=CONFLICT_SCHEMA,
            session=session,
            run_id=run_id,
        )
        return str(data.get("verdict", "compatible")), str(data.get("rationale", ""))[:500]
    except Exception as exc:  # never let adjudication break ingestion
        log.warning("conflict adjudication failed: %s", exc)
        return "compatible", f"adjudication unavailable: {exc}"


def detect_conflicts(
    session: Session, item: KnowledgeItem, *, domain_name: str = "", run_id=None, adjudicate: bool = True
) -> list[Conflict]:
    """Compare ``item`` with live items sharing its subject/predicate. Returns newly created conflict rows."""
    if not item.subject or not item.predicate:
        return []
    pair_filter = (
        KnowledgeItem.domain_id == item.domain_id,
        KnowledgeItem.status.in_(_LIVE),
        KnowledgeItem.subject.ilike(item.subject),
        KnowledgeItem.predicate.ilike(item.predicate),
    )
    same_pair = select(KnowledgeItem).where(*pair_filter)
    distinct_objects = session.execute(
        select(func.count(func.distinct(func.lower(KnowledgeItem.object)))).where(*pair_filter)
    ).scalar_one()
    if distinct_objects >= MULTI_VALUED_THRESHOLD:
        return []  # multi-valued predicate: "X covers A", "X covers B", ...

    my_docs = _document_ids(session, item.id)
    created: list[Conflict] = []
    for other in session.execute(same_pair.where(KnowledgeItem.id != item.id)).scalars():
        if _norm(other.object) == _norm(item.object):
            continue
        if item.product_version and other.product_version and item.product_version != other.product_version:
            continue  # different versions may legitimately differ
        if my_docs & _document_ids(session, other.id):
            continue  # same document lists both; not a contradiction
        a, b = sorted([item, other], key=lambda i: str(i.id))
        exists = session.execute(
            select(Conflict).where(Conflict.item_a_id == a.id, Conflict.item_b_id == b.id)
        ).scalar_one_or_none()
        if exists:
            continue
        verdict, rationale = (
            ("contradict", "heuristic")
            if not adjudicate
            else judge(session, domain_name or item.domain_id, a, b, run_id=run_id)
        )
        if verdict != "contradict":
            log.info("no conflict (%s): %s | %s", verdict, a.statement[:80], b.statement[:80])
            continue
        c = Conflict(
            domain_id=item.domain_id,
            item_a_id=a.id,
            item_b_id=b.id,
            reason=f"'{item.subject}' {item.predicate}: '{a.object[:100]}' vs '{b.object[:100]}' — {rationale}",
        )
        session.add(c)
        created.append(c)
        for it in (a, b):
            if ItemStatus(it.status) in (ItemStatus.SUPPORTED, ItemStatus.VERIFIED):
                transition(session, it, ItemStatus.CONFLICTED, reason=c.reason, actor="system:conflicts")
    session.flush()
    return created
