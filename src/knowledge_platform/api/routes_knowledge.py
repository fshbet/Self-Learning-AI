from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..adapters import get_search
from ..core.knowledge_entry import DuplicateKnowledge, KnowledgeEntry, create_knowledge
from ..core.orchestration.jobs import enqueue_falsifications, enqueue_revalidations
from ..core.orchestration.queue import enqueue
from ..core.pipeline import rescore
from ..core.plugins.registry import get_registry
from ..core.retrieval.answer import answer_question
from ..core.retrieval.search import hybrid_search
from ..core.verification.review_flags import resolve_review
from ..core.versioning.dependencies import add_relation, relations_for_api
from ..core.versioning.lifecycle import IllegalTransition, transition
from ..db import get_db
from ..models import Conflict, Document, Evidence, ItemStatus, KnowledgeItem, KnowledgeRelation, Source, utcnow
from .schemas import (
    AskRequest,
    AskResponse,
    ConflictOut,
    ConflictResolve,
    EvidenceOut,
    KnowledgeCreate,
    KnowledgeDetail,
    KnowledgeOut,
    Page,
    RelationCreate,
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
    provenance: str | None = None,
    polarity: str | None = None,
    origin: str | None = None,
    q: str | None = None,
    needs_revalidation: bool | None = None,
    needs_review: bool | None = None,
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
    if provenance:
        stmt = stmt.where(KnowledgeItem.provenance.in_(provenance.split(",")))
    if polarity:
        stmt = stmt.where(KnowledgeItem.polarity == polarity)
    if origin:
        stmt = stmt.where(KnowledgeItem.origin.in_(origin.split(",")))
    if needs_revalidation is not None:
        stmt = stmt.where(KnowledgeItem.needs_revalidation.is_(needs_revalidation))
    if needs_review is not None:
        stmt = stmt.where(KnowledgeItem.needs_review.is_(needs_review))
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
    o.relations = relations_for_api(db, item.id)
    return o


# ----------------------------------------------------------------------------- dependency graph (req. 17)


@router.post("/knowledge/{item_id}/relations", response_model=KnowledgeDetail, status_code=201)
def create_relation(item_id: uuid.UUID, body: RelationCreate, db: Session = Depends(get_db)) -> KnowledgeDetail:
    a, b = db.get(KnowledgeItem, item_id), db.get(KnowledgeItem, body.to_item_id)
    if a is None or b is None:
        raise HTTPException(404, "knowledge item not found")
    try:
        rel = add_relation(db, a, b, body.relation_type, origin="user")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if rel is None:
        raise HTTPException(422, "cannot relate an item to itself or across domains")
    db.commit()
    return get_knowledge(item_id, db)


@router.delete("/knowledge/{item_id}/relations/{relation_id}", status_code=204)
def delete_relation(item_id: uuid.UUID, relation_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    rel = db.get(KnowledgeRelation, relation_id)
    if rel is None or item_id not in (rel.from_item_id, rel.to_item_id):
        raise HTTPException(404, "relation not found")
    db.delete(rel)
    db.commit()


@router.post("/knowledge/falsify-sample")
def falsify_sample(domain: str, limit: int = Query(5, ge=1, le=50), db: Session = Depends(get_db)) -> dict[str, int]:
    """Queue active falsification for a sample of live items (needs a search provider)."""
    if get_search() is None:
        raise HTTPException(
            409, "no search provider configured (start SearXNG: docker compose --profile discovery up -d)"
        )
    n = enqueue_falsifications(db, domain, limit=limit)
    db.commit()
    return {"queued": n}


@router.post("/knowledge/{item_id}/falsify")
def falsify(item_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, str]:
    """Try to disprove one item with the open web; counter-evidence flags it for review (never rewrites)."""
    item = db.get(KnowledgeItem, item_id)
    if item is None:
        raise HTTPException(404, "knowledge item not found")
    if get_search() is None:
        raise HTTPException(
            409, "no search provider configured (start SearXNG: docker compose --profile discovery up -d)"
        )
    job = enqueue(
        db,
        "falsify_item",
        {"item_id": str(item.id)},
        idempotency_key=f"falsify:{item.id}",
        priority=130,
        max_attempts=1,
    )
    db.commit()
    if job is None:
        raise HTTPException(409, "a falsification for this item is already queued")
    return {"job_id": str(job.id)}


@router.post("/knowledge/{item_id}/revalidate")
def revalidate(item_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, str]:
    item = db.get(KnowledgeItem, item_id)
    if item is None:
        raise HTTPException(404, "knowledge item not found")
    job = enqueue(
        db,
        "revalidate_item",
        {"item_id": str(item.id)},
        idempotency_key=f"revalidate:{item.id}",
        priority=40,
        max_attempts=2,
    )
    db.commit()
    if job is None:
        raise HTTPException(409, "revalidation already queued")
    return {"job_id": str(job.id)}


@router.post("/knowledge/revalidate-all")
def revalidate_all(domain: str | None = None, db: Session = Depends(get_db)) -> dict[str, int]:
    n = enqueue_revalidations(db, domain)
    db.commit()
    return {"queued": n}


# ----------------------------------------------------------------------------- human-authored knowledge


@router.post("/knowledge", response_model=KnowledgeDetail, status_code=201)
def create_knowledge_item(body: KnowledgeCreate, db: Session = Depends(get_db)) -> KnowledgeDetail:
    """Add USER or ORGANIZATION knowledge. It keeps its provenance through scoring, review and export."""
    reg = get_registry()
    if body.domain not in reg:
        raise HTTPException(404, f"unknown domain {body.domain}")
    entry = KnowledgeEntry(**{k: v for k, v in body.model_dump().items() if k != "domain"})
    try:
        item = create_knowledge(db, reg.get(body.domain), entry)
    except DuplicateKnowledge as exc:
        db.rollback()
        raise HTTPException(409, f"{exc}") from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return get_knowledge(item.id, db)


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
    # every human decision explicitly resolves an open review flag (audit P0.2); nothing automatic does
    resolve_review(item, resolved_by=body.reviewer, resolution=body.reason, action=body.action)
    try:
        if body.action == "approve":
            # replace an earlier approval only; a person's *provided* evidence (USER/ORGANIZATION items) stays
            item.evidence[:] = [
                e for e in item.evidence if not (e.evidence_type == "human" and e.details.get("approved"))
            ]
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
