from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..core.pipeline import rescore
from ..core.plugins.registry import get_registry
from ..core.retrieval.answer import answer_question
from ..core.retrieval.search import hybrid_search
from ..core.versioning.lifecycle import IllegalTransition, transition
from ..db import get_db
from ..models import Conflict, Document, Evidence, ItemStatus, KnowledgeItem, Source, utcnow
from .schemas import (
    AskRequest,
    AskResponse,
    ConflictOut,
    ConflictResolve,
    EvidenceOut,
    KnowledgeDetail,
    KnowledgeOut,
    Page,
    ReviewRequest,
    SearchHitOut,
)

router = APIRouter(tags=["knowledge"])


# ----------------------------------------------------------------------------- helpers


def _evidence_stats(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[int, int]]:
    if not ids:
        return {}
    rows = db.execute(
        select(
            Evidence.knowledge_item_id,
            func.count(),
            func.count(func.distinct(Evidence.source_id)),
        )
        .where(Evidence.knowledge_item_id.in_(ids), Evidence.evidence_type == "extraction")
        .group_by(Evidence.knowledge_item_id)
    ).all()
    return {r[0]: (r[1], r[2]) for r in rows}


def _to_out(db: Session, items: list[KnowledgeItem]) -> list[KnowledgeOut]:
    stats = _evidence_stats(db, [i.id for i in items])
    out = []
    for i in items:
        o = KnowledgeOut.model_validate(i)
        o.evidence_count, o.source_count = stats.get(i.id, (0, 0))
        out.append(o)
    return out


def _evidence_out(db: Session, evidence: list[Evidence]) -> list[EvidenceOut]:
    src_ids = {e.source_id for e in evidence if e.source_id}
    doc_ids = {e.document_id for e in evidence if e.document_id}
    names = dict(db.execute(select(Source.id, Source.name).where(Source.id.in_(src_ids))).all()) if src_ids else {}
    titles = (
        dict(db.execute(select(Document.id, Document.title).where(Document.id.in_(doc_ids))).all()) if doc_ids else {}
    )
    out = []
    for e in evidence:
        o = EvidenceOut.model_validate(e)
        o.source_name = names.get(e.source_id) if e.source_id else None
        o.document_title = titles.get(e.document_id) if e.document_id else None
        out.append(o)
    return out


# ----------------------------------------------------------------------------- list / detail


@router.get("/knowledge", response_model=Page[KnowledgeOut])
def list_knowledge(
    domain: str | None = None,
    status: str | None = None,
    topic: str | None = None,
    knowledge_type: str | None = None,
    q: str | None = None,
    min_confidence: float | None = Query(None, ge=0, le=1),
    sort: str = Query("updated", pattern="^(updated|confidence|subject|created)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    db: Session = Depends(get_db),
) -> Page[KnowledgeOut]:
    stmt = select(KnowledgeItem)
    if domain:
        stmt = stmt.where(KnowledgeItem.domain_id == domain)
    if status:
        stmt = stmt.where(KnowledgeItem.status.in_([s.strip() for s in status.split(",")]))
    if topic:
        stmt = stmt.where(or_(KnowledgeItem.topic == topic, KnowledgeItem.topic.like(topic + "/%")))
    if knowledge_type:
        stmt = stmt.where(KnowledgeItem.knowledge_type == knowledge_type)
    if min_confidence is not None:
        stmt = stmt.where(KnowledgeItem.confidence >= min_confidence)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                KnowledgeItem.statement.ilike(like),
                KnowledgeItem.subject.ilike(like),
                KnowledgeItem.explanation.ilike(like),
            )
        )
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    order = {
        "updated": KnowledgeItem.updated_at.desc(),
        "created": KnowledgeItem.created_at.desc(),
        "confidence": KnowledgeItem.confidence.desc(),
        "subject": KnowledgeItem.subject.asc(),
    }[sort]
    items = db.execute(stmt.order_by(order).offset((page - 1) * page_size).limit(page_size)).scalars().all()
    return Page(items=_to_out(db, items), total=total, page=page, page_size=page_size)


@router.get("/knowledge/{item_id}", response_model=KnowledgeDetail)
def get_knowledge(item_id: uuid.UUID, db: Session = Depends(get_db)) -> KnowledgeDetail:
    item = db.execute(
        select(KnowledgeItem)
        .where(KnowledgeItem.id == item_id)
        .options(selectinload(KnowledgeItem.evidence), selectinload(KnowledgeItem.transitions))
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(404, "knowledge item not found")
    o = KnowledgeDetail.model_validate(item)
    o.evidence = _evidence_out(db, item.evidence)
    stats = _evidence_stats(db, [item.id]).get(item.id, (0, 0))
    o.evidence_count, o.source_count = stats
    conflicts = (
        db.execute(select(Conflict).where(or_(Conflict.item_a_id == item.id, Conflict.item_b_id == item.id)))
        .scalars()
        .all()
    )
    o.conflicts = [_conflict_out(db, c) for c in conflicts]
    dups = db.execute(select(KnowledgeItem).where(KnowledgeItem.duplicate_of_id == item.id)).scalars().all()
    o.duplicates = _to_out(db, dups)
    return o


# ----------------------------------------------------------------------------- human review


@router.post("/knowledge/{item_id}/review", response_model=KnowledgeDetail)
def review(item_id: uuid.UUID, body: ReviewRequest, db: Session = Depends(get_db)) -> KnowledgeDetail:
    item = db.execute(
        select(KnowledgeItem).where(KnowledgeItem.id == item_id).options(selectinload(KnowledgeItem.evidence))
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(404, "knowledge item not found")
    actor = f"human:{body.reviewer}"
    plugin = get_registry().get(item.domain_id)
    try:
        if body.action == "approve":
            item.evidence[:] = [e for e in item.evidence if e.evidence_type != "human"]
            item.evidence.append(
                Evidence(
                    knowledge_item_id=item.id,
                    evidence_type="human",
                    excerpt=body.reason or "approved by reviewer",
                    verified=True,
                    details={"approved": True, "reviewer": body.reviewer},
                )
            )
            db.flush()
            rescore(db, item, plugin, actor=actor)
            if item.status != ItemStatus.VERIFIED:
                transition(db, item, ItemStatus.VERIFIED, reason=body.reason or "approved", actor=actor, force=True)
            for c in db.execute(
                select(Conflict).where(
                    or_(Conflict.item_a_id == item.id, Conflict.item_b_id == item.id), Conflict.status == "OPEN"
                )
            ).scalars():
                c.status = "RESOLVED"
                c.resolution = f"{item.id} approved by reviewer"
                c.resolved_by = body.reviewer
                c.resolved_at = utcnow()
        elif body.action == "reject":
            transition(db, item, ItemStatus.REJECTED, reason=body.reason or "rejected", actor=actor, force=True)
        elif body.action == "stale":
            transition(db, item, ItemStatus.STALE, reason=body.reason or "marked stale", actor=actor, force=True)
        elif body.action == "reopen":
            transition(db, item, ItemStatus.CANDIDATE, reason=body.reason or "reopened", actor=actor, force=True)
            rescore(db, item, plugin, actor=actor)
    except IllegalTransition as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return get_knowledge(item_id, db)


# ----------------------------------------------------------------------------- conflicts


def _conflict_out(db: Session, c: Conflict) -> ConflictOut:
    o = ConflictOut.model_validate(c)
    a, b = db.get(KnowledgeItem, c.item_a_id), db.get(KnowledgeItem, c.item_b_id)
    outs = _to_out(db, [i for i in (a, b) if i is not None])
    o.item_a = next((x for x in outs if x.id == c.item_a_id), None)
    o.item_b = next((x for x in outs if x.id == c.item_b_id), None)
    return o


@router.get("/conflicts", response_model=list[ConflictOut])
def list_conflicts(domain: str | None = None, status: str = "OPEN", db: Session = Depends(get_db)) -> list[ConflictOut]:
    stmt = select(Conflict).order_by(Conflict.created_at.desc())
    if domain:
        stmt = stmt.where(Conflict.domain_id == domain)
    if status and status != "ALL":
        stmt = stmt.where(Conflict.status == status)
    return [_conflict_out(db, c) for c in db.execute(stmt).scalars().all()]


@router.post("/conflicts/{conflict_id}/resolve", response_model=ConflictOut)
def resolve_conflict(conflict_id: uuid.UUID, body: ConflictResolve, db: Session = Depends(get_db)) -> ConflictOut:
    c = db.get(Conflict, conflict_id)
    if c is None:
        raise HTTPException(404, "conflict not found")
    a, b = db.get(KnowledgeItem, c.item_a_id), db.get(KnowledgeItem, c.item_b_id)
    actor = f"human:{body.reviewer}"
    keep = {"a": [a], "b": [b], "both": [a, b], "neither": []}[body.keep]
    for it in (a, b):
        if it is None:
            continue
        if it in keep:
            transition(
                db,
                it,
                ItemStatus.VERIFIED,
                reason=body.resolution or f"conflict resolved: keep {body.keep}",
                actor=actor,
                force=True,
            )
        elif body.keep in ("a", "b"):
            transition(
                db,
                it,
                ItemStatus.SUPERSEDED,
                reason=body.resolution or "superseded in conflict resolution",
                actor=actor,
                force=True,
            )
            it.superseded_by_id = keep[0].id if keep and keep[0] else None
        else:
            transition(
                db,
                it,
                ItemStatus.REJECTED,
                reason=body.resolution or "rejected in conflict resolution",
                actor=actor,
                force=True,
            )
    c.status = "RESOLVED"
    c.resolution = f"keep={body.keep}. {body.resolution}".strip()
    c.resolved_by = body.reviewer
    c.resolved_at = utcnow()
    db.commit()
    return _conflict_out(db, c)


# ----------------------------------------------------------------------------- search / ask


@router.get("/search", response_model=list[SearchHitOut])
def search(
    domain: str, q: str, limit: int = Query(10, ge=1, le=50), db: Session = Depends(get_db)
) -> list[SearchHitOut]:
    if domain not in get_registry():
        raise HTTPException(404, f"unknown domain {domain}")
    results = hybrid_search(db, domain_id=domain, query=q, limit=limit)
    outs = _to_out(db, [r.item for r in results])
    hits = []
    for r, o in zip(results, outs, strict=True):
        hits.append(
            SearchHitOut(
                item=o,
                score=r.score,
                vec_rank=r.vec_rank,
                lex_rank=r.lex_rank,
                similarity=r.similarity,
                evidence=_evidence_out(db, r.item.evidence[:3]),
            )
        )
    db.commit()  # llm/embedding accounting rows
    return hits


@router.post("/ask", response_model=AskResponse)
def ask(body: AskRequest, db: Session = Depends(get_db)) -> AskResponse:
    reg = get_registry()
    if body.domain not in reg:
        raise HTTPException(404, f"unknown domain {body.domain}")
    ans = answer_question(db, reg.get(body.domain), body.question, limit=body.limit)
    db.commit()
    return AskResponse(**ans.__dict__)


@router.get("/topics")
def topics(domain: str, db: Session = Depends(get_db)) -> list[dict[str, object]]:
    rows = db.execute(
        select(KnowledgeItem.topic, KnowledgeItem.status, func.count())
        .where(KnowledgeItem.domain_id == domain)
        .group_by(KnowledgeItem.topic, KnowledgeItem.status)
    ).all()
    agg: dict[str, dict[str, int]] = {}
    for topic, status, n in rows:
        agg.setdefault(topic or "(unclassified)", {})[status] = n
    return [{"topic": t, "total": sum(v.values()), "by_status": v} for t, v in sorted(agg.items())]
