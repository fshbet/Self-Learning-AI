"""Append one operational sample to docs/reports/p2/longrun.jsonl (P2.9 long-run observation).

Run periodically while the platform serves (e.g. every 10 minutes). Each line records queue state, corpus
growth, storage, model calls in the last hour, the server process's memory and any dead-letter / stuck jobs, so
churn, leaks, starvation and growth can be read off the file afterwards. Read-only: never changes anything.
"""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import httpx
from sqlalchemy import Integer, Text, func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from knowledge_platform.core.observability import ops_metrics  # noqa: E402
from knowledge_platform.db import session_scope  # noqa: E402
from knowledge_platform.models import Job, JobStatus, LLMCall, utcnow  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "docs" / "reports" / "p2" / "longrun.jsonl"


def server_memory_mb(port: int = 8010) -> float | None:
    """RSS of the process listening on the API port (Windows: netstat + tasklist; else psutil if present)."""
    try:
        import psutil  # type: ignore

        for c in psutil.net_connections(kind="tcp"):
            if c.laddr and c.laddr.port == port and c.status == "LISTEN" and c.pid:
                return round(psutil.Process(c.pid).memory_info().rss / 1e6, 1)
    except Exception:
        pass
    try:
        import subprocess

        out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, timeout=20).stdout
        pid = next((line.split()[-1] for line in out.splitlines() if f":{port}" in line and "LISTENING" in line), None)
        if not pid:
            return None
        tl = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"], capture_output=True, text=True).stdout
        row = tl.strip().splitlines()[-1].split('","')
        kb = row[-1].strip('"').replace(" K", "").replace(",", "").replace(".", "")
        return round(int(kb) / 1024, 1)
    except Exception:
        return None


def main() -> None:
    now = utcnow()
    with session_scope() as s:
        m = ops_metrics(s, "powerbi")
        hour_ago = now - timedelta(hours=1)
        calls = s.execute(
            select(
                LLMCall.purpose,
                func.count(),
                func.sum(LLMCall.latency_ms),
                func.sum(LLMCall.ok.is_(False).cast(Integer)),
            )
            .where(LLMCall.created_at >= hour_ago)
            .group_by(LLMCall.purpose)
        ).all()
        stuck = s.execute(
            select(func.count())
            .select_from(Job)
            .where(Job.status == JobStatus.RUNNING, Job.locked_at < now - timedelta(minutes=30))
        ).scalar_one()
        payload_text = Job.payload.cast(Text)
        dup = s.execute(
            select(Job.type, payload_text, func.count())
            .where(Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
            .group_by(Job.type, payload_text)
            .having(func.count() > 1)
        ).all()
        done_last_hour = s.execute(
            select(func.count()).select_from(Job).where(Job.status == JobStatus.DONE, Job.finished_at >= hour_ago)
        ).scalar_one()
    try:
        health = httpx.get("http://127.0.0.1:8010/api/health", timeout=5).status_code
    except Exception:
        health = None
    k = m["knowledge"]
    sample = {
        "at": now.isoformat(),
        "health": health,
        "server_rss_mb": server_memory_mb(),
        "queue": {
            kk: m["queue"][kk]
            for kk in (
                "queued",
                "running",
                "failed_awaiting_retry",
                "dead_letter",
                "jobs_retried",
                "oldest_queued_age_seconds",
            )
        },
        "jobs_done_last_hour": done_last_hour,
        "stuck_running_over_30min": stuck,
        "duplicate_live_jobs": len(dup),
        "documents": k["documents"]["total"],
        "documents_extracted": k["documents"]["by_status"].get("EXTRACTED", 0),
        "items": k["knowledge_items"]["total"],
        "items_by_status": k["knowledge_items"]["by_status"],
        "needs_review": k["knowledge_items"]["needs_review"],
        "needs_revalidation": k["knowledge_items"]["needs_revalidation"],
        "conflicts": k["conflicts"],
        "database_mb": round(m["storage"]["database_bytes"] / 1e6, 1),
        "document_mb": round(m["storage"]["document_bytes"] / 1e6, 1),
        "model_calls_last_hour": {
            p: {"calls": n, "seconds": round((lat or 0) / 1000), "failed": int(f or 0)} for p, n, lat, f in calls
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(sample, default=str) + "\n")
    print(json.dumps(sample, default=str))


if __name__ == "__main__":
    main()
