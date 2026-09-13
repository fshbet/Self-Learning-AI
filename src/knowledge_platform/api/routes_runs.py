from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.orchestration.jobs import start_extract_run, start_pipeline_run, start_run
from ..core.orchestration.queue import enqueue, retry_dead
from ..core.plugins.registry import get_registry
from ..db import get_db
from ..models import Job, JobStatus, Run
from .schemas import JobOut, Page, RunCreate, RunOut

router = APIRouter(tags=["runs"])


def _run_out(db: Session, runs: list[Run]) -> list[RunOut]:
    ids = [r.id for r in runs]
    counts: dict[uuid.UUID, dict[str, int]] = {}
    if ids:
        for run_id, status, n in db.execute(
            select(Job.run_id, Job.status, func.count()).where(Job.run_id.in_(ids)).group_by(Job.run_id, Job.status)
        ).all():
            counts.setdefault(run_id, {})[status] = n
    out = []
    for r in runs:
        o = RunOut.model_validate(r)
        c = counts.get(r.id, {})
        o.jobs_total = sum(c.values())
        o.jobs_done = c.get(JobStatus.DONE, 0)
        o.jobs_failed = c.get(JobStatus.DEAD, 0) + c.get(JobStatus.FAILED, 0)
        o.jobs_running = c.get(JobStatus.RUNNING, 0)
        o.jobs_queued = c.get(JobStatus.QUEUED, 0)
        out.append(o)
    return out


@router.get("/runs", response_model=list[RunOut])
def list_runs(
    domain: str | None = None, limit: int = Query(20, ge=1, le=200), db: Session = Depends(get_db)
) -> list[RunOut]:
    stmt = select(Run).order_by(Run.started_at.desc()).limit(limit)
    if domain:
        stmt = stmt.where(Run.domain_id == domain)
    return _run_out(db, db.execute(stmt).scalars().all())


@router.post("/runs", response_model=RunOut)
def create_run(body: RunCreate, db: Session = Depends(get_db)) -> RunOut:
    reg = get_registry()
    if body.domain not in reg:
        raise HTTPException(404, f"unknown domain {body.domain}")
    if body.kind == "pipeline":
        run, _ = start_pipeline_run(
            db, body.domain, triggered_by="api", source_keys=body.source_keys, max_pages=body.max_pages
        )
    elif body.kind == "extract":
        run, _ = start_extract_run(db, body.domain, triggered_by="api")
    else:
        run = start_run(db, domain_id=body.domain, kind="discover", triggered_by="api")
        enqueue(db, "discover", {"domain_id": body.domain}, run_id=run.id, idempotency_key=f"discover:{body.domain}")
    db.commit()
    return _run_out(db, [run])[0]


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> RunOut:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return _run_out(db, [run])[0]


@router.get("/jobs", response_model=Page[JobOut])
def list_jobs(
    run: uuid.UUID | None = None,
    status: str | None = None,
    type: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> Page[JobOut]:
    stmt = select(Job)
    if run:
        stmt = stmt.where(Job.run_id == run)
    if status:
        stmt = stmt.where(Job.status.in_(status.split(",")))
    if type:
        stmt = stmt.where(Job.type == type)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    jobs = (
        db.execute(stmt.order_by(Job.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).scalars().all()
    )
    return Page(items=[JobOut.model_validate(j) for j in jobs], total=total, page=page, page_size=page_size)


@router.post("/jobs/{job_id}/retry", response_model=JobOut)
def retry_job(job_id: uuid.UUID, db: Session = Depends(get_db)) -> JobOut:
    job = retry_dead(db, job_id)
    if job is None:
        raise HTTPException(409, "only DEAD jobs can be retried")
    db.commit()
    return JobOut.model_validate(job)
