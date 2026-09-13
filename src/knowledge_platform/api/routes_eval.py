from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..core.evaluation.runner import METRIC_KEYS, run_evaluation
from ..core.orchestration.jobs import enqueue_evaluation
from ..core.plugins.registry import get_registry
from ..db import get_db
from ..models import EvaluationRun
from .schemas import EvaluationCompare, EvaluationCreate, EvaluationRunDetail, EvaluationRunOut

router = APIRouter(tags=["evaluation"])


@router.get("/evaluations", response_model=list[EvaluationRunOut])
def list_evaluations(
    domain: str | None = None, limit: int = Query(30, ge=1, le=200), db: Session = Depends(get_db)
) -> list[EvaluationRunOut]:
    stmt = select(EvaluationRun).order_by(EvaluationRun.started_at.desc()).limit(limit)
    if domain:
        stmt = stmt.where(EvaluationRun.domain_id == domain)
    return [EvaluationRunOut.model_validate(r) for r in db.execute(stmt).scalars().all()]


@router.post("/evaluations", response_model=EvaluationRunOut, status_code=201)
def create_evaluation(body: EvaluationCreate, db: Session = Depends(get_db)) -> EvaluationRunOut:
    reg = get_registry()
    if body.domain not in reg:
        raise HTTPException(404, f"unknown domain {body.domain}")
    plugin = reg.get(body.domain)
    if not plugin.evaluation_set():
        raise HTTPException(409, f"domain {body.domain} has no evaluation.yaml questions")
    if body.wait:
        ev = run_evaluation(db, plugin, triggered_by="api", question_ids=body.question_ids)
        db.commit()
        return EvaluationRunOut.model_validate(ev)
    run, job = enqueue_evaluation(db, body.domain, triggered_by="api", question_ids=body.question_ids)
    db.commit()
    if job is None:
        raise HTTPException(409, "an evaluation for this domain is already queued or running")
    # return a placeholder describing the queued job; the real EvaluationRun appears once the worker starts it
    return EvaluationRunOut(
        id=run.id,
        domain_id=body.domain,
        dataset_version=plugin.evaluation_version(),
        status="QUEUED",
        config={"job_id": str(job.id), "run_id": str(run.id)},
        metrics={},
        baseline_run_id=None,
        regression=False,
        regression_details={},
        findings=[],
        triggered_by="api",
        error=None,
        started_at=run.started_at,
        finished_at=None,
    )


@router.get("/evaluations/{eval_id}", response_model=EvaluationRunDetail)
def get_evaluation(eval_id: uuid.UUID, db: Session = Depends(get_db)) -> EvaluationRunDetail:
    ev = db.execute(
        select(EvaluationRun).where(EvaluationRun.id == eval_id).options(selectinload(EvaluationRun.results))
    ).scalar_one_or_none()
    if ev is None:
        raise HTTPException(404, "evaluation run not found")
    return EvaluationRunDetail.model_validate(ev)


@router.get("/evaluations/{eval_id}/compare/{other_id}", response_model=EvaluationCompare)
def compare_evaluations(eval_id: uuid.UUID, other_id: uuid.UUID, db: Session = Depends(get_db)) -> EvaluationCompare:
    a = db.execute(
        select(EvaluationRun).where(EvaluationRun.id == eval_id).options(selectinload(EvaluationRun.results))
    ).scalar_one_or_none()
    b = db.execute(
        select(EvaluationRun).where(EvaluationRun.id == other_id).options(selectinload(EvaluationRun.results))
    ).scalar_one_or_none()
    if a is None or b is None:
        raise HTTPException(404, "evaluation run not found")
    deltas = {}
    for k in METRIC_KEYS:
        va, vb = a.metrics.get(k), b.metrics.get(k)
        deltas[k] = {"a": va, "b": vb, "delta": round(va - vb, 4) if va is not None and vb is not None else None}
    pa = {r.question_id: r for r in a.results}
    pb = {r.question_id: r for r in b.results}
    changes = []
    for qid in sorted(set(pa) | set(pb)):
        ra, rb = pa.get(qid), pb.get(qid)
        if ra is None or rb is None or ra.passed != rb.passed:
            changes.append(
                {
                    "question_id": qid,
                    "a_passed": ra.passed if ra else None,
                    "b_passed": rb.passed if rb else None,
                    "a_causes": ra.failure_causes if ra else [],
                    "b_causes": rb.failure_causes if rb else [],
                }
            )
    return EvaluationCompare(
        a=EvaluationRunOut.model_validate(a),
        b=EvaluationRunOut.model_validate(b),
        metric_deltas=deltas,
        question_changes=changes,
    )
