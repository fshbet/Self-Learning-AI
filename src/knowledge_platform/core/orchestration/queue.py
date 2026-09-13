"""PostgreSQL-backed job queue (§28): idempotent enqueue, SKIP LOCKED claim, retries, dead-letter."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ...models import Job, JobStatus, utcnow

log = logging.getLogger(__name__)


def enqueue(
    session: Session,
    job_type: str,
    payload: dict[str, Any],
    *,
    run_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
    priority: int = 100,
    max_attempts: int = 3,
) -> Job | None:
    """Insert a job; returns None when an identical idempotency key is already queued/running."""
    if idempotency_key:
        existing = session.execute(select(Job).where(Job.idempotency_key == idempotency_key)).scalar_one_or_none()
        if existing is not None:
            if existing.status in (JobStatus.QUEUED, JobStatus.RUNNING):
                return None
            # finished earlier: free the key so the new job can be inserted
            existing.idempotency_key = None
            session.flush()
    stmt = (
        pg_insert(Job)
        .values(
            id=uuid.uuid4(),
            run_id=run_id,
            type=job_type,
            payload=payload,
            idempotency_key=idempotency_key,
            priority=priority,
            max_attempts=max_attempts,
            status=JobStatus.QUEUED,
            run_at=utcnow(),
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(Job.id)
    )
    job_id = session.execute(stmt).scalar_one_or_none()
    session.flush()
    return session.get(Job, job_id) if job_id else None


def claim(session: Session, worker_id: str, job_types: list[str] | None = None) -> Job | None:
    """Atomically claim the next runnable job (lowest priority number, oldest first)."""
    stmt = (
        select(Job)
        .where(Job.status == JobStatus.QUEUED, Job.run_at <= utcnow())
        .order_by(Job.priority, Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job_types:
        stmt = stmt.where(Job.type.in_(job_types))
    job = session.execute(stmt).scalar_one_or_none()
    if job is None:
        return None
    job.status = JobStatus.RUNNING
    job.locked_by = worker_id
    job.locked_at = utcnow()
    job.attempts += 1
    session.flush()
    return job


def complete(session: Session, job: Job, result: dict[str, Any] | None = None) -> None:
    job.status = JobStatus.DONE
    job.result = result or {}
    job.finished_at = utcnow()
    job.locked_by = None
    session.flush()


def fail(session: Session, job: Job, error: str) -> None:
    job.last_error = error[:4000]
    job.locked_by = None
    if job.attempts >= job.max_attempts:
        job.status = JobStatus.DEAD
        job.finished_at = utcnow()
        log.error("job %s (%s) moved to dead-letter: %s", job.id, job.type, error[:200])
    else:
        job.status = JobStatus.QUEUED
        job.run_at = utcnow() + timedelta(seconds=30 * (2 ** (job.attempts - 1)))
    session.flush()


def requeue_stuck(session: Session, older_than_minutes: int = 60) -> int:
    """Return RUNNING jobs whose worker died back to QUEUED."""
    cutoff = utcnow() - timedelta(minutes=older_than_minutes)
    res = session.execute(
        update(Job)
        .where(Job.status == JobStatus.RUNNING, Job.locked_at < cutoff)
        .values(status=JobStatus.QUEUED, locked_by=None, last_error="requeued: worker lock expired")
    )
    return res.rowcount or 0


def retry_dead(session: Session, job_id: uuid.UUID) -> Job | None:
    job = session.get(Job, job_id)
    if job is None or job.status != JobStatus.DEAD:
        return None
    job.status = JobStatus.QUEUED
    job.attempts = 0
    job.run_at = utcnow()
    job.finished_at = None
    session.flush()
    return job
