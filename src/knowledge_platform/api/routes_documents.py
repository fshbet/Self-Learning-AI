from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..core.orchestration.jobs import start_run
from ..core.orchestration.queue import enqueue
from ..db import get_db
from ..models import Document, Evidence, Source
from .schemas import DocumentDetail, DocumentOut, Page

router = APIRouter(tags=["documents"])


def _item_counts(db: Session, doc_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not doc_ids:
        return {}
    rows = db.execute(
        select(Evidence.document_id, func.count(func.distinct(Evidence.knowledge_item_id)))
        .where(Evidence.document_id.in_(doc_ids), Evidence.evidence_type == "extraction")
        .group_by(Evidence.document_id)
    ).all()
    return {r[0]: r[1] for r in rows}


@router.get("/documents", response_model=Page[DocumentOut])
def list_documents(
    domain: str | None = None,
    source: uuid.UUID | None = None,
    status: str | None = None,
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    db: Session = Depends(get_db),
) -> Page[DocumentOut]:
    stmt = select(Document, Source.name).join(Source, Source.id == Document.source_id)
    if domain:
        stmt = stmt.where(Document.domain_id == domain)
    if source:
        stmt = stmt.where(Document.source_id == source)
    if status:
        stmt = stmt.where(Document.status == status)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Document.title.ilike(like), Document.url.ilike(like)))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(Document.fetched_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    counts = _item_counts(db, [r[0].id for r in rows])
    items = []
    for doc, source_name in rows:
        o = DocumentOut.model_validate(doc)
        o.item_count = counts.get(doc.id, 0)
        o.source_name = source_name
        items.append(o)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/documents/{doc_id}", response_model=DocumentDetail)
def get_document(doc_id: uuid.UUID, db: Session = Depends(get_db)) -> DocumentDetail:
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(404, "document not found")
    o = DocumentDetail.model_validate(doc)
    o.item_count = _item_counts(db, [doc.id]).get(doc.id, 0)
    o.source_name = doc.source.name if doc.source else None
    return o


@router.post("/documents/{doc_id}/extract")
def extract_now(doc_id: uuid.UUID, force: bool = False, db: Session = Depends(get_db)) -> dict[str, str]:
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(404, "document not found")
    run = start_run(db, domain_id=doc.domain_id, kind="extract", triggered_by="api")
    key = f"extract:{doc.id}:{doc.content_hash}" + (f":{uuid.uuid4().hex[:6]}" if force else "")
    job = enqueue(db, "extract_document", {"document_id": str(doc.id)}, run_id=run.id, idempotency_key=key, priority=40)
    db.commit()
    if job is None:
        raise HTTPException(409, "extraction for this document version is already queued or running")
    return {"run_id": str(run.id), "job_id": str(job.id)}
