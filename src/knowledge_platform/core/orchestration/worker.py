"""Worker loop: claims jobs, runs handlers, records results, finalizes runs, runs the scheduler."""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
import traceback

from ...config import get_settings
from ...db import session_scope
from . import jobs as _jobs  # noqa: F401  (registers handlers)
from .jobs import (
    HANDLERS,
    finalize_run_if_complete,
    schedule_due_evaluations,
    schedule_due_sources,
    schedule_revalidations,
)
from .queue import claim, complete, fail, requeue_stuck

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, *, name: str | None = None, scheduler: bool = True) -> None:
        self.id = name or f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"
        self.scheduler = scheduler
        self._stop = threading.Event()
        self._last_schedule = 0.0
        self._last_requeue = 0.0

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------ single step
    def run_once(self) -> bool:
        """Claim and execute one job. Returns False when the queue is empty."""
        with session_scope() as session:
            job = claim(session, self.id)
            if job is None:
                return False
            job_id, job_type, run_id = job.id, job.type, job.run_id
        log.info("job %s start type=%s", job_id, job_type)
        started = time.perf_counter()
        with session_scope() as session:
            job = session.get(type(job), job_id)
            assert job is not None
            fn = HANDLERS.get(job_type)
            try:
                if fn is None:
                    raise RuntimeError(f"no handler for job type {job_type!r}")
                result = fn(session, job)
                complete(session, job, result)
                log.info("job %s done in %.1fs: %s", job_id, time.perf_counter() - started, result)
            except Exception:
                session.rollback()
                err = traceback.format_exc()
                job = session.get(type(job), job_id)
                assert job is not None
                fail(session, job, err)
                log.warning("job %s failed (attempt %d): %s", job_id, job.attempts, err.strip().splitlines()[-1])
        if run_id is not None:
            with session_scope() as session:
                finalize_run_if_complete(session, run_id)
        return True

    def _housekeeping(self) -> None:
        now = time.monotonic()
        s = get_settings()
        if now - self._last_requeue > 300:
            self._last_requeue = now
            with session_scope() as session:
                n = requeue_stuck(session)
                if n:
                    log.warning("requeued %d stuck jobs", n)
        if self.scheduler and s.scheduler_enabled and now - self._last_schedule > s.scheduler_interval_seconds:
            self._last_schedule = now
            with session_scope() as session:
                n = schedule_due_sources(session)
                if n:
                    log.info("scheduler enqueued %d due sources", n)
                m = schedule_due_evaluations(session)
                if m:
                    log.info("scheduler enqueued %d evaluations", m)
                r = schedule_revalidations(session)
                if r:
                    log.info("scheduler enqueued %d revalidations", r)

    # ------------------------------------------------------------------ loop
    def run_forever(self) -> None:
        poll = get_settings().worker_poll_seconds
        log.info("worker %s started", self.id)
        while not self._stop.is_set():
            try:
                self._housekeeping()
                worked = self.run_once()
            except Exception:
                log.exception("worker loop error")
                worked = False
            if not worked:
                self._stop.wait(poll)
        log.info("worker %s stopped", self.id)

    def start_in_thread(self) -> threading.Thread:
        t = threading.Thread(target=self.run_forever, name="kp-worker", daemon=True)
        t.start()
        return t
