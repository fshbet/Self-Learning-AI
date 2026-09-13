from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.domains import sync_domain
from ..core.orchestration.jobs import start_run
from ..core.orchestration.queue import enqueue
from ..core.plugins.registry import get_registry
from ..db import get_db
from ..models import Document, Domain, Source
from .schemas import DomainOut, SourceOut, SourcePatch

router = APIRouter(tags=["domains"])


@router.get("/domains", response_model=list[DomainOut])
def list_domains(db: Session = Depends(get_db)) -> list[DomainOut]:
    reg = get_registry()
    rows = {d.id: d for d in db.execute(select(Domain)).scalars()}
    out: list[DomainOut] = []
    for p in reg.all():
        d = rows.get(p.id)
        if d is None:
            out.append(
                DomainOut(
                    id=p.id,
                    name=p.name,
                    description=p.manifest.description,
                    version=p.manifest.version,
                    enabled=True,
                    manifest=p.summary(),
                    loaded=True,
                )
            )
        else:
            o = DomainOut.model_validate(d)
            o.manifest = p.summary()
            out.append(o)
    for name, err in reg.errors.items():
        out.append(
            DomainOut(id=name, name=name, description="", version="", enabled=False, loaded=False, load_error=err)
        )
    for d in rows.values():
        if d.id not in reg and d.id not in reg.errors:
            o = DomainOut.model_validate(d)
            o.loaded = False
            o.load_error = "plugin directory not found"
            out.append(o)
    return out


@router.get("/domains/{domain_id}", response_model=DomainOut)
def get_domain(domain_id: str, db: Session = Depends(get_db)) -> DomainOut:
    reg = get_registry()
    if domain_id not in reg:
        raise HTTPException(404, f"unknown domain {domain_id}")
    p = reg.get(domain_id)
    d = db.get(Domain, domain_id)
    if d is None:
        return DomainOut(
            id=p.id,
            name=p.name,
            description=p.manifest.description,
            version=p.manifest.version,
            enabled=True,
            manifest=p.summary(),
        )
    o = DomainOut.model_validate(d)
    o.manifest = p.summary()
    return o


@router.post("/domains/reload", response_model=list[DomainOut])
def reload_domains(db: Session = Depends(get_db)) -> list[DomainOut]:
    get_registry(reload=True)
    return list_domains(db)


@router.post("/domains/{domain_id}/sync")
def sync(domain_id: str, db: Session = Depends(get_db)) -> dict[str, int]:
    reg = get_registry(reload=True)
    if domain_id not in reg:
        raise HTTPException(404, f"unknown domain {domain_id}")
    result = sync_domain(db, reg.get(domain_id))
    db.commit()
    return result


# ----------------------------------------------------------------------------- sources


def _source_out(db: Session, src: Source, counts: dict[uuid.UUID, int]) -> SourceOut:
    o = SourceOut.model_validate(src)
    o.document_count = counts.get(src.id, 0)
    return o


@router.get("/sources", response_model=list[SourceOut])
def list_sources(domain: str | None = None, db: Session = Depends(get_db)) -> list[SourceOut]:
    stmt = select(Source).order_by(Source.authority.desc(), Source.name)
    if domain:
        stmt = stmt.where(Source.domain_id == domain)
    sources = db.execute(stmt).scalars().all()
    counts = dict(db.execute(select(Document.source_id, func.count()).group_by(Document.source_id)).all())
    return [_source_out(db, s, counts) for s in sources]


@router.patch("/sources/{source_id}", response_model=SourceOut)
def patch_source(source_id: uuid.UUID, body: SourcePatch, db: Session = Depends(get_db)) -> SourceOut:
    src = db.get(Source, source_id)
    if src is None:
        raise HTTPException(404, "source not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(src, k, v)
    db.commit()
    counts = {
        source_id: db.execute(
            select(func.count()).select_from(Document).where(Document.source_id == source_id)
        ).scalar_one()
    }
    return _source_out(db, src, counts)


@router.post("/sources/{source_id}/crawl")
def crawl_source_now(
    source_id: uuid.UUID, max_pages: int | None = None, db: Session = Depends(get_db)
) -> dict[str, str]:
    src = db.get(Source, source_id)
    if src is None:
        raise HTTPException(404, "source not found")
    run = start_run(db, domain_id=src.domain_id, kind="crawl", triggered_by="api")
    payload: dict[str, object] = {"source_id": str(src.id)}
    if max_pages:
        payload["max_pages"] = max_pages
    job = enqueue(db, "crawl_source", payload, run_id=run.id, idempotency_key=f"crawl:{src.id}", priority=40)
    db.commit()
    if job is None:
        raise HTTPException(409, "a crawl for this source is already queued or running")
    return {"run_id": str(run.id), "job_id": str(job.id)}
