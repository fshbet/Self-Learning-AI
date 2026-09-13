from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.collection.normalize import canonicalize_url
from ..core.domains import sync_domain
from ..core.orchestration.jobs import start_run
from ..core.orchestration.queue import enqueue
from ..core.plugins.registry import get_registry
from ..db import get_db
from ..models import Document, Domain, DomainKeyword, Source, SourceStatus
from .schemas import DomainOut, KeywordCreate, KeywordOut, SourceCreate, SourceOut, SourcePatch

router = APIRouter(tags=["domains"])


def _decorate(db: Session, o: DomainOut) -> DomainOut:
    o.keywords = [
        KeywordOut.model_validate(k)
        for k in db.execute(
            select(DomainKeyword).where(DomainKeyword.domain_id == o.id).order_by(DomainKeyword.created_at)
        ).scalars()
    ]
    o.user_sources = db.execute(
        select(func.count()).select_from(Source).where(Source.domain_id == o.id, Source.origin == "user")
    ).scalar_one()
    return o


def _require_domain(db: Session, domain_id: str) -> Domain:
    if domain_id not in get_registry():
        raise HTTPException(404, f"unknown domain {domain_id}")
    d = db.get(Domain, domain_id)
    if d is None:
        raise HTTPException(409, f"domain {domain_id} is not synced yet (run: kp domains sync {domain_id})")
    return d


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
    return [_decorate(db, o) if o.loaded else o for o in out]


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
    return _decorate(db, o)


# ----------------------------------------------------------------------------- user-defined keywords


@router.get("/domains/{domain_id}/keywords", response_model=list[KeywordOut])
def list_keywords(domain_id: str, db: Session = Depends(get_db)) -> list[KeywordOut]:
    _require_domain(db, domain_id)
    rows = db.execute(
        select(DomainKeyword).where(DomainKeyword.domain_id == domain_id).order_by(DomainKeyword.created_at)
    ).scalars()
    return [KeywordOut.model_validate(k) for k in rows]


@router.post("/domains/{domain_id}/keywords", response_model=KeywordOut, status_code=201)
def add_keyword(domain_id: str, body: KeywordCreate, db: Session = Depends(get_db)) -> KeywordOut:
    _require_domain(db, domain_id)
    keyword = " ".join(body.keyword.split())
    existing = db.execute(
        select(DomainKeyword).where(DomainKeyword.domain_id == domain_id, DomainKeyword.keyword.ilike(keyword))
    ).scalar_one_or_none()
    if existing:
        existing.enabled = True
        db.commit()
        return KeywordOut.model_validate(existing)
    k = DomainKeyword(domain_id=domain_id, keyword=keyword)
    db.add(k)
    db.commit()
    return KeywordOut.model_validate(k)


@router.delete("/domains/{domain_id}/keywords/{keyword_id}", status_code=204)
def delete_keyword(domain_id: str, keyword_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    k = db.get(DomainKeyword, keyword_id)
    if k is None or k.domain_id != domain_id:
        raise HTTPException(404, "keyword not found")
    db.delete(k)
    db.commit()


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


@router.post("/sources", response_model=SourceOut, status_code=201)
def create_source(body: SourceCreate, db: Session = Depends(get_db)) -> SourceOut:
    """Add a user-defined URL to a domain. It is kept across plugin syncs (origin='user')."""
    _require_domain(db, body.domain)
    url = canonicalize_url(body.url)
    if not url:
        raise HTTPException(422, "url must be an absolute http(s) URL")
    dup = db.execute(select(Source).where(Source.domain_id == body.domain, Source.url == url)).scalar_one_or_none()
    if dup:
        raise HTTPException(409, f"this URL is already registered as source '{dup.name}'")
    host = url.split("/")[2]
    src = Source(
        domain_id=body.domain,
        key="user:" + uuid.uuid4().hex[:10],
        origin="user",
        name=body.name.strip() or host,
        url=url,
        publisher=body.publisher.strip() or host,
        source_type=body.source_type,
        authority=body.authority,
        license=body.license,
        permissions=body.permissions,
        crawl_frequency_hours=body.crawl_frequency_hours,
        max_depth=body.max_depth,
        max_pages=body.max_pages,
        allow_patterns=body.allow_patterns,
        deny_patterns=body.deny_patterns,
        notes=body.notes,
        status=SourceStatus.ACTIVE,
        enabled=True,
    )
    db.add(src)
    db.flush()
    if body.crawl_now:
        run = start_run(db, domain_id=src.domain_id, kind="crawl", triggered_by="api")
        enqueue(
            db,
            "crawl_source",
            {"source_id": str(src.id)},
            run_id=run.id,
            idempotency_key=f"crawl:{src.id}",
            priority=40,
        )
    db.commit()
    return _source_out(db, src, {})


@router.delete("/sources/{source_id}", status_code=204)
def delete_source(source_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    """Remove a user-defined or discovered source (plugin-defined ones are paused or edited in sources.yaml)."""
    src = db.get(Source, source_id)
    if src is None:
        raise HTTPException(404, "source not found")
    if src.origin == "plugin":
        raise HTTPException(409, "plugin-defined sources cannot be deleted; pause it or remove it from sources.yaml")
    db.delete(src)
    db.commit()


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
