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
app.add_typer(domains_app, name="domains")
app.add_typer(db_app, name="db")
app.add_typer(run_app, name="run")
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


# ----------------------------------------------------------------------------- serve / worker / search


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
