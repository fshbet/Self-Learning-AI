"""Command-line interface: ``kp --help``."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import get_settings

app = typer.Typer(help="Modular self-updating knowledge platform", no_args_is_help=True)
domains_app = typer.Typer(help="Domain plugins", no_args_is_help=True)
db_app = typer.Typer(help="Database", no_args_is_help=True)
run_app = typer.Typer(help="Pipeline runs", no_args_is_help=True)
eval_app = typer.Typer(help="Golden-set evaluation", no_args_is_help=True)
export_app = typer.Typer(help="Canonical Knowledge Snapshots", no_args_is_help=True)
app.add_typer(domains_app, name="domains")
app.add_typer(db_app, name="db")
app.add_typer(run_app, name="run")
app.add_typer(eval_app, name="eval")
app.add_typer(export_app, name="export")
console = Console()


def _setup_logging() -> None:
    logging.basicConfig(level=get_settings().log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@app.callback()
def _main() -> None:
    _setup_logging()


@app.command()
def version() -> None:
    console.print(f"knowledge-platform {__version__}")


# ----------------------------------------------------------------------------- db


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Apply migrations (alembic upgrade head)."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    command.upgrade(cfg, "head")
    console.print("[green]database up to date[/green]")


# ----------------------------------------------------------------------------- domains


@domains_app.command("list")
def domains_list() -> None:
    from .core.plugins.registry import get_registry

    reg = get_registry()
    table = Table("id", "name", "version", "sources", "taxonomy paths", "validators", "skills", "eval Qs")
    for p in reg.all():
        s = p.summary()
        table.add_row(
            p.id,
            p.name,
            s["version"],
            str(s["sources_count"]),
            str(len(s["taxonomy_paths"])),
            ", ".join(s["validators"]) or "-",
            ", ".join(s["skills"]) or "-",
            str(s["evaluation_questions"]),
        )
    console.print(table)
    for name, err in reg.errors.items():
        console.print(f"[red]failed to load {name}: {err}[/red]")


@domains_app.command("sync")
def domains_sync(domain: str | None = typer.Argument(None, help="domain id; default: all")) -> None:
    """Load plugin manifests and source catalogs into the database."""
    from .core.domains import sync_domain
    from .core.plugins.registry import get_registry
    from .db import session_scope

    reg = get_registry(reload=True)
    plugins = [reg.get(domain)] if domain else reg.all()
    with session_scope() as session:
        for p in plugins:
            res = sync_domain(session, p)
            console.print(f"[green]{p.id}[/green]: {res}")


@domains_app.command("new")
def domains_new(slug: str, name: str = typer.Option(None, help="Display name")) -> None:
    """Scaffold a new domain plugin from the example template."""
    from .core.plugins.registry import get_registry

    base = get_settings().domain_dirs[0]
    target = base / slug
    if target.exists():
        raise typer.BadParameter(f"{target} already exists")
    template = base / "example"
    if not template.exists():
        raise typer.BadParameter(f"template {template} not found")
    shutil.copytree(template, target, ignore=shutil.ignore_patterns("__pycache__"))
    display = name or slug.replace("-", " ").replace("_", " ").title()
    for f in ("plugin.yaml", "sources.yaml", "evaluation.yaml"):
        p = target / f
        if p.exists():
            p.write_text(
                p.read_text(encoding="utf-8").replace("example", slug).replace("Example Domain", display),
                encoding="utf-8",
            )
    get_registry(reload=True)
    console.print(f"[green]created {target}[/green] — edit plugin.yaml and sources.yaml, then `kp domains sync {slug}`")


@domains_app.command("check")
def domains_check(path: Path) -> None:
    """Validate a plugin directory against the contract without loading it into the registry."""
    from .core.plugins.registry import load_plugin_dir

    p = load_plugin_dir(path)
    console.print_json(data=p.summary())


# ----------------------------------------------------------------------------- runs


@run_app.command("pipeline")
def run_pipeline(
    domain: str,
    source: list[str] = typer.Option(None, "--source", "-s", help="source key(s) to limit to"),
    max_pages: int | None = typer.Option(None, help="override max pages per source"),
    wait: bool = typer.Option(True, help="run the worker inline until the run completes"),
) -> None:
    """Crawl the domain's sources and extract knowledge."""
    from .core.orchestration.jobs import start_pipeline_run
    from .db import session_scope

    with session_scope() as session:
        run, n = start_pipeline_run(
            session, domain, triggered_by="cli", source_keys=source or None, max_pages=max_pages
        )
        run_id = run.id
    console.print(f"run [bold]{run_id}[/bold]: {n} source crawl(s) enqueued")
    if wait:
        _drain(run_id)


@run_app.command("extract")
def run_extract(domain: str, wait: bool = True) -> None:
    """Extract knowledge from documents that have not been extracted yet."""
    from .core.orchestration.jobs import start_extract_run
    from .db import session_scope

    with session_scope() as session:
        run, n = start_extract_run(session, domain, triggered_by="cli")
        run_id = run.id
    console.print(f"run [bold]{run_id}[/bold]: {n} document(s) enqueued")
    if wait:
        _drain(run_id)


@run_app.command("discover")
def run_discover(domain: str, wait: bool = True) -> None:
    """Search the web for candidate sources (requires a search provider)."""
    from .core.orchestration.jobs import start_run
    from .core.orchestration.queue import enqueue
    from .db import session_scope

    with session_scope() as session:
        run = start_run(session, domain_id=domain, kind="discover", triggered_by="cli")
        enqueue(session, "discover", {"domain_id": domain}, run_id=run.id, idempotency_key=f"discover:{domain}")
        run_id = run.id
    if wait:
        _drain(run_id)


def _drain(run_id) -> None:
    from sqlalchemy import select

    from .core.orchestration.worker import Worker
    from .db import session_scope
    from .models import Job, JobStatus, Run

    worker = Worker(name="cli", scheduler=False)
    while True:
        while worker.run_once():
            pass
        with session_scope() as session:
            pending = session.execute(
                select(Job).where(Job.run_id == run_id, Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
            ).first()
            if pending is None:
                run = session.get(Run, run_id)
                console.print(f"run [bold]{run_id}[/bold] {run.status}")
                console.print_json(data=run.stats)
                return
        import time

        time.sleep(1)


# ----------------------------------------------------------------------------- evaluation


@eval_app.command("run")
def eval_run(
    domain: str,
    question: list[str] = typer.Option(None, "--question", "-q", help="limit to question id(s)"),
    fail_on_regression: bool = typer.Option(False, help="exit with code 2 when a regression is detected"),
) -> None:
    """Run the domain's golden questions against the current knowledge base and store the results."""
    from .core.evaluation.runner import METRIC_KEYS, run_evaluation
    from .core.plugins.registry import get_registry
    from .db import session_scope

    with session_scope() as session:
        ev = run_evaluation(session, get_registry().get(domain), triggered_by="cli", question_ids=question or None)
        session.flush()
        console.print(f"evaluation [bold]{ev.id}[/bold] {ev.status}  dataset v{ev.dataset_version}")
        if ev.error:
            console.print(f"[red]{ev.error}[/red]")
        table = Table("metric", "value")
        for k in ("questions", "passed", *METRIC_KEYS, "mean_latency_ms"):
            v = ev.metrics.get(k)
            table.add_row(
                k,
                "—"
                if v is None
                else (f"{v:.1%}" if isinstance(v, float) and k not in ("mean_latency_ms",) else str(v)),
            )
        console.print(table)
        for r in ev.results:
            mark = "[green]PASS[/green]" if r.passed else "[red]FAIL[/red]"
            console.print(f"  {mark} {r.question_id}: {', '.join(r.failure_causes) or 'ok'}")
        if ev.regression:
            console.print(f"[red bold]REGRESSION detected[/red bold]: {ev.regression_details.get('metrics')}")
        for f in ev.findings:
            console.print(f"  [yellow]{f['cause']}[/yellow] ({f['count']}): {f['action']}")
        regressed = ev.regression
    if fail_on_regression and regressed:
        raise typer.Exit(code=2)


@eval_app.command("list")
def eval_list(domain: str | None = typer.Argument(None), limit: int = 10) -> None:
    """Show recent evaluation runs and their headline metrics."""
    from sqlalchemy import select

    from .db import session_scope
    from .models import EvaluationRun

    with session_scope() as session:
        stmt = select(EvaluationRun).order_by(EvaluationRun.started_at.desc()).limit(limit)
        if domain:
            stmt = stmt.where(EvaluationRun.domain_id == domain)
        table = Table("id", "domain", "dataset", "status", "accuracy", "citations", "halluc.", "regression", "when")
        for ev in session.execute(stmt).scalars():
            m = ev.metrics or {}
            fmt = lambda v: "—" if v is None else f"{v:.0%}"  # noqa: E731
            table.add_row(
                str(ev.id)[:8],
                ev.domain_id,
                ev.dataset_version,
                ev.status,
                fmt(m.get("accuracy")),
                fmt(m.get("citation_correctness")),
                fmt(m.get("hallucination_rate")),
                "[red]yes[/red]" if ev.regression else "no",
                ev.started_at.strftime("%Y-%m-%d %H:%M"),
            )
        console.print(table)


# ----------------------------------------------------------------------------- export


@export_app.command("snapshot")
def export_snapshot(
    domain: str,
    out: Path | None = typer.Option(None, help="write a zip of the snapshot to this path"),
) -> None:
    """Build a full Canonical Knowledge Snapshot for a domain."""
    from .core.export.snapshot import build_snapshot, zip_snapshot
    from .core.plugins.registry import get_registry
    from .db import session_scope

    with session_scope() as session:
        snap = build_snapshot(session, get_registry().get(domain), created_by="cli")
        session.flush()
        console.print(f"snapshot [bold]{snap.id}[/bold] v{snap.version} {snap.status}")
        if snap.status != "ready":
            console.print(f"[red]{snap.error}[/red]")
            raise typer.Exit(code=1)
        console.print(f"integrity {snap.integrity_hash} · {snap.size_bytes:,} bytes · {snap.object_prefix}")
        console.print_json(data=snap.manifest.get("counts", {}))
        if out:
            out.write_bytes(zip_snapshot(snap))
            console.print(f"[green]written {out}[/green]")


@export_app.command("delta")
def export_delta(
    domain: str,
    base: str | None = typer.Option(None, help="base snapshot id (default: latest ready full snapshot)"),
    out: Path | None = typer.Option(None, help="write a zip of the delta to this path"),
) -> None:
    """Build a fresh full snapshot and a delta against a base snapshot."""
    import uuid as _uuid

    from .core.export.delta import build_delta_snapshot
    from .core.export.snapshot import zip_snapshot
    from .core.plugins.registry import get_registry
    from .db import session_scope

    with session_scope() as session:
        snap = build_delta_snapshot(
            session, get_registry().get(domain), base_snapshot_id=_uuid.UUID(base) if base else None, created_by="cli"
        )
        session.flush()
        console.print(f"snapshot [bold]{snap.id}[/bold] v{snap.version} {snap.kind} {snap.status}")
        if snap.status != "ready":
            console.print(f"[red]{snap.error}[/red]")
            raise typer.Exit(code=1)
        console.print_json(data=snap.manifest.get("counts", {}))
        if out:
            out.write_bytes(zip_snapshot(snap))
            console.print(f"[green]written {out}[/green]")


@export_app.command("list")
def export_list(domain: str | None = typer.Argument(None)) -> None:
    from sqlalchemy import select

    from .db import session_scope
    from .models import Snapshot

    with session_scope() as session:
        stmt = select(Snapshot).order_by(Snapshot.created_at.desc()).limit(20)
        if domain:
            stmt = stmt.where(Snapshot.domain_id == domain)
        table = Table("id", "domain", "version", "kind", "status", "items", "bytes", "integrity", "created")
        for s in session.execute(stmt).scalars():
            table.add_row(
                str(s.id)[:8],
                s.domain_id,
                str(s.version),
                s.kind,
                s.status,
                str((s.manifest or {}).get("counts", {}).get("knowledge", "—")),
                f"{s.size_bytes:,}",
                (s.integrity_hash or "—")[:20],
                s.created_at.strftime("%Y-%m-%d %H:%M"),
            )
        console.print(table)


@export_app.command("verify")
def export_verify(snapshot_id: str) -> None:
    """Recompute file hashes and the integrity hash of a stored snapshot."""
    import uuid as _uuid

    from .core.export.snapshot import verify_snapshot
    from .db import session_scope
    from .models import Snapshot

    with session_scope() as session:
        snap = session.get(Snapshot, _uuid.UUID(snapshot_id))
        if snap is None:
            raise typer.BadParameter("snapshot not found")
        console.print_json(data=verify_snapshot(snap))


# ----------------------------------------------------------------------------- serve / worker / search


@app.command()
def ops(domain: str | None = typer.Argument(None)) -> None:
    """Operational metrics: queue health, dead letters, model latency, storage, schedule."""
    from .core.observability import ops_metrics
    from .db import session_scope

    with session_scope() as session:
        m = ops_metrics(session, domain)
    q, models, st, sched = m["queue"], m["models"], m["storage"], m["schedule"]
    table = Table("area", "metric", "value", title=f"Operations{' · ' + domain if domain else ''}")
    table.add_row("queue", "queued / running", f"{q['queued']} / {q['running']}")
    table.add_row("queue", "retrying / dead-letter", f"{q['failed_awaiting_retry']} / {q['dead_letter']}")
    table.add_row("queue", "jobs that needed retries", str(q["jobs_retried"]))
    for t, d in q["job_duration_24h"].items():
        table.add_row("jobs 24h", t, f"{d['count']} × avg {d['avg_ms'] / 1000:.1f}s (max {d['max_ms'] / 1000:.1f}s)")
    for p, v in models["by_purpose"].items():
        table.add_row(
            "models 24h", p, f"{v['calls']} calls · avg {v['avg_ms']} ms · p95 {v['p95_ms']} ms · {v['failed']} failed"
        )
    table.add_row(
        "storage",
        "documents",
        f"{st['documents']} ({st['document_bytes'] / 1e6:.1f} MB, {st['mirror_documents']} mirrors)",
    )
    table.add_row("storage", "snapshots", f"{st['snapshots']} ({st['snapshot_bytes'] / 1e6:.1f} MB)")
    table.add_row("storage", "evidence / embedded items", f"{st['evidence_records']} / {st['items_embedded']}")
    table.add_row(
        "schedule", "next source check", f"{sched['next_source_check'] or '—'} ({sched['sources_overdue']} overdue)"
    )
    table.add_row(
        "schedule",
        "evaluation",
        f"every {sched['evaluation_interval_hours']}h · next {sched['next_evaluation'] or '—'}",
    )
    table.add_row(
        "schedule", "snapshot", f"every {sched['snapshot_interval_hours']}h · next {sched['next_snapshot'] or '—'}"
    )
    console.print(table)
    if q["dead_letter"]:
        console.print(
            f"[red]{q['dead_letter']} dead-letter job(s): {q['dead_by_type']} — "
            "inspect on the Pipeline page or retry via POST /api/jobs/{id}/retry[/red]"
        )


@app.command()
def worker() -> None:
    """Run a standalone worker (use when KP_EMBEDDED_WORKER=false)."""
    from .core.orchestration.worker import Worker

    Worker().run_forever()


@app.command()
def serve(host: str | None = None, port: int | None = None, reload: bool = False) -> None:
    """Start the API (and the embedded worker unless disabled)."""
    s = get_settings()
    uvicorn.run(
        "knowledge_platform.api.app:app",
        host=host or s.api_host,
        port=port or s.api_port,
        reload=reload,
        log_level=s.log_level.lower(),
    )


@app.command()
def search(domain: str, query: str, limit: int = 8) -> None:
    """Hybrid search from the terminal."""
    from .core.retrieval.search import hybrid_search
    from .db import session_scope

    with session_scope() as session:
        table = Table("score", "status", "conf", "statement", "topic")
        for r in hybrid_search(session, domain_id=domain, query=query, limit=limit):
            table.add_row(
                f"{r.score:.3f}", r.item.status, f"{r.item.confidence:.2f}", r.item.statement[:110], r.item.topic
            )
        console.print(table)


@app.command()
def ask(domain: str, question: str) -> None:
    """Grounded answer with citations."""
    from .core.plugins.registry import get_registry
    from .core.retrieval.answer import answer_question
    from .db import session_scope

    with session_scope() as session:
        ans = answer_question(session, get_registry().get(domain), question)
    console.print(ans.answer)
    for c in ans.citations:
        console.print(f"  [{c['n']}] ({c['status']}, {c['confidence']:.2f}) {c['statement'][:120]}")


if __name__ == "__main__":
    app()
