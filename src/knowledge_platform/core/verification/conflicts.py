"""Contradiction detection (§18, audit P1.6).

Candidates share a subject and the *core* of a predicate — the predicate with its negation stripped, so
"X supports Y" pairs with "X does not support Y" and a negative item ("X cannot run without power") pairs with the
positive claim it denies. Before a conflict record is opened, three checks avoid false positives:

1. Items whose evidence comes from the same document are list members, not contradictions.
2. A (subject, predicate) pair with many distinct objects is a multi-valued predicate ("covers", "includes").
3. Remaining pairs are adjudicated by the model. Only "contradict" opens a conflict; "different_versions",
   "different_scopes" and "different_conditions" are kept *structurally* as a ``compatible_under`` relation carrying
   the verdict and the distinguishing conditions (a mismatch in recorded product versions is stored the same way
   without a model call). "compatible" leaves no trace.
"""

from __future__ import annotations

import logging
import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import Conflict, Evidence, ItemStatus, KnowledgeItem
from ..extraction.prompts import CONFLICT_SCHEMA, CONFLICT_SYSTEM, CONFLICT_USER
from ..llm_service import call_json
from ..versioning.dependencies import add_relation
from ..versioning.lifecycle import transition

log = logging.getLogger(__name__)

_WS = re.compile(r"\s+")
_LIVE = (ItemStatus.SUPPORTED, ItemStatus.VERIFIED, ItemStatus.CONFLICTED, ItemStatus.CANDIDATE)
MULTI_VALUED_THRESHOLD = 3  # >= this many distinct objects for one subject+predicate => not a contradiction
QUALIFIED_VERDICTS = ("different_versions", "different_scopes", "different_conditions")
_NEGATIONS = re.compile(
    r"^(?:(?:does|do|did|is|are|was|were|has|have|can|could|will|would|should|must|may|might)\s+)?"
    r"(?:not|never|no longer|cannot|can't|won't|doesn't|isn't|aren't)(?:\s+|$)"
)
_AUX = re.compile(r"^(?:does|do|did|is|are|was|were|has|have|can|could|will|would|should|must|may|might)\s+")


def _norm(s: str) -> str:
    return _WS.sub(" ", (s or "").strip().lower()).rstrip(".")


def predicate_core(predicate: str) -> tuple[str, bool]:
    """(core predicate, negated). "does not support" -> ("support", True); "supports" -> ("support", False)."""
    p = _norm(predicate)
    negated = False
    m = _NEGATIONS.match(p)
    if m:
        negated = True
        p = p[m.end() :] or p.split(" ")[0]  # "is not" -> core "is", negated
    p = _AUX.sub("", p).strip()
    # cheap stemming so "supports" / "support" / "supported" / "modifies" / "modified" share a core
    head, _, rest = p.partition(" ")
    if len(head) > 4:
        if head.endswith("ies"):
            head = head[:-3] + "y"
        elif head.endswith("ied"):
            head = head[:-3] + "y"
        elif head.endswith(("sses", "shes", "ches", "xes", "zes")):
            head = head[:-2]
        elif head.endswith("s") and not head.endswith("ss"):
            head = head[:-1]
        elif head.endswith("ed"):
            head = head[:-2]
    p = f"{head} {rest}".strip()
    return p, negated


def is_opposing(a: KnowledgeItem, b: KnowledgeItem) -> bool:
    """Same core predicate and either the objects differ, or one side is negated / negative and the other not."""
    ca, na = predicate_core(a.predicate)
    cb, nb = predicate_core(b.predicate)
    if ca != cb:
        return False
    neg_a = na or a.polarity == "negative"
    neg_b = nb or b.polarity == "negative"
    if neg_a != neg_b:
        return True
    return _norm(a.object) != _norm(b.object)


def _document_ids(session: Session, item_id: uuid.UUID) -> set[uuid.UUID]:
    rows = session.execute(
        select(Evidence.document_id).where(
            Evidence.knowledge_item_id == item_id,
            Evidence.document_id.is_not(None),
            Evidence.verified.is_(True),  # evidence from an older version of a changed page no longer counts
        )
    ).scalars()
    return set(rows)


def judge(
    session: Session, domain_name: str, a: KnowledgeItem, b: KnowledgeItem, *, run_id=None
) -> tuple[str, str, str]:
    """Ask the model whether two statements contradict: (verdict, rationale, conditions). Falls back to
    'compatible' on model failure — an outage must never open or suppress a conflict silently."""
    try:
        data = call_json(
            purpose="reason",
            system=CONFLICT_SYSTEM.format(domain_name=domain_name),
            user=CONFLICT_USER.format(a=a.statement, b=b.statement),
            schema=CONFLICT_SCHEMA,
            session=session,
            run_id=run_id,
        )
        return (
            str(data.get("verdict", "compatible")),
            str(data.get("rationale", ""))[:500],
            str(data.get("conditions", "") or "")[:300],
        )
    except Exception as exc:  # never let adjudication break ingestion
        log.warning("conflict adjudication failed: %s", exc)
        return "compatible", f"adjudication unavailable: {exc}", ""


def record_qualified(
    session: Session, a: KnowledgeItem, b: KnowledgeItem, verdict: str, rationale: str, conditions: str
):
    """Keep a non-contradiction verdict structurally (audit P1.6): both items hold, under different
    versions / scopes / conditions."""
    return add_relation(
        session,
        a,
        b,
        "compatible_under",
        origin="system",
        details={"verdict": verdict, "conditions": conditions, "rationale": rationale},
    )


def detect_conflicts(
    session: Session, item: KnowledgeItem, *, domain_name: str = "", run_id=None, adjudicate: bool = True
) -> list[Conflict]:
    """Compare ``item`` with live items sharing its subject/predicate. Returns newly created conflict rows."""
    if not item.subject or not item.predicate:
        return []
    # candidates: same subject, same *core* predicate (negation stripped: polarity-aware pairing)
    core, _ = predicate_core(item.predicate)
    same_subject = (
        session.execute(
            select(KnowledgeItem).where(
                KnowledgeItem.domain_id == item.domain_id,
                KnowledgeItem.status.in_(_LIVE),
                KnowledgeItem.subject.ilike(item.subject),
                KnowledgeItem.id != item.id,
            )
        )
        .scalars()
        .all()
    )
    candidates = [o for o in same_subject if predicate_core(o.predicate)[0] == core]
    # multi-valued predicate: "X covers A", "X covers B", ... (counted over the same-polarity, non-negated side)
    positives = {
        _norm(o.object) for o in candidates + [item] if not predicate_core(o.predicate)[1] and o.polarity != "negative"
    }
    if len(positives) >= MULTI_VALUED_THRESHOLD and not (
        predicate_core(item.predicate)[1] or item.polarity == "negative"
    ):
        return []

    my_docs = _document_ids(session, item.id)
    created: list[Conflict] = []
    for other in candidates:
        if not is_opposing(item, other):
            continue
        a, b = sorted([item, other], key=lambda i: str(i.id))
        if session.execute(
            select(Conflict).where(Conflict.item_a_id == a.id, Conflict.item_b_id == b.id)
        ).scalar_one_or_none():
            continue
        if item.product_version and other.product_version and item.product_version != other.product_version:
            # different recorded versions may legitimately differ: kept structurally, no model call
            record_qualified(
                session,
                a,
                b,
                "different_versions",
                "recorded product versions differ",
                f"{a.product_version} vs {b.product_version}",
            )
            continue
        if my_docs & _document_ids(session, other.id):
            continue  # same document lists both; not a contradiction
        verdict, rationale, conditions = (
            ("contradict", "heuristic", "")
            if not adjudicate
            else judge(session, domain_name or item.domain_id, a, b, run_id=run_id)
        )
        if verdict in QUALIFIED_VERDICTS:
            record_qualified(session, a, b, verdict, rationale, conditions)
            log.info("qualified (%s): %s | %s", verdict, a.statement[:80], b.statement[:80])
            continue
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
