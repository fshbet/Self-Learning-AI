"""FastAPI application: REST API under /api, SPA frontend served from frontend/dist when built."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .. import __version__
from ..adapters import get_embedder, get_llm, get_object_store, get_search
from ..config import get_settings
from ..core.evaluation.runner import latest_evaluation
from ..core.observability import ops_metrics
from ..core.orchestration.worker import Worker
from ..core.plugins.registry import get_registry
from ..core.runtime_config import effective_config
from ..db import get_db, get_engine
from ..models import Conflict, Document, ItemStatus, Job, KnowledgeItem, LLMCall, Run, Source
from .routes_documents import router as documents_router
from .routes_domains import router as domains_router
from .routes_eval import router as eval_router
from .routes_knowledge import router as knowledge_router
from .routes_runs import _run_out
from .routes_runs import router as runs_router
from .routes_settings import router as settings_router
from .routes_snapshots import router as snapshots_router
from .schemas import HealthOut, StatsOut

log = logging.getLogger(__name__)

FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"
DOCS_DIR = Path(__file__).resolve().parents[3] / "docs"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    get_registry()
    worker: Worker | None = None
    if settings.embedded_worker:
        worker = Worker(name="embedded")
        worker.start_in_thread()
        log.info("embedded worker started")
    try:
        yield
    finally:
        if worker:
            worker.stop()


app = FastAPI(
    title="Knowledge Platform API",
    version=__version__,
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

for r in (
    domains_router,
    documents_router,
    knowledge_router,
    runs_router,
    eval_router,
    settings_router,
    snapshots_router,
):
    app.include_router(r, prefix="/api")


@app.get("/api/health", response_model=HealthOut, tags=["system"])
def health() -> HealthOut:
    reg = get_registry()
    try:
        with get_engine().connect() as conn:
            conn.execute(text("select 1"))
        db_ok = True
    except Exception:
        db_ok = False
    cfg = effective_config()
    try:
        llm = get_llm()
        models = llm.available_models()
        llm_name = llm.name
    except Exception:  # misconfigured provider must not break /health
        models, llm_name = [], cfg.llm.provider
    try:
        embedding = get_embedder().identity
    except Exception:
        embedding = f"{cfg.embedding.provider if cfg.embedding else '?'}:{cfg.embedding_model}"
    search = get_search()
    return HealthOut(
        ok=db_ok,
        database=db_ok,
        llm_provider=llm_name,
        llm_models_configured=dict(cfg.models),
        llm_models_available=models,
        embedding=embedding,
        object_store=get_object_store().name,
        search=(search.name if search and search.healthy() else None),
        domains=[p.id for p in reg.all()],
        plugin_errors=reg.errors,
        version=__version__,
    )


@app.get("/api/stats", response_model=StatsOut, tags=["system"])
def stats(domain: str | None = None, db: Session = Depends(get_db)) -> StatsOut:
    def by(col, model, label):
        stmt = select(label, func.count()).select_from(model).group_by(label)
        if domain and hasattr(model, "domain_id"):
            stmt = stmt.where(model.domain_id == domain)
        return {str(k): v for k, v in db.execute(stmt).all()}

    knowledge = by(None, KnowledgeItem, KnowledgeItem.status)
    for st in ItemStatus:
        knowledge.setdefault(st.value, 0)
    total = sum(knowledge.values())
    live = [ItemStatus.SUPPORTED, ItemStatus.VERIFIED, ItemStatus.CONFLICTED, ItemStatus.STALE, ItemStatus.CANDIDATE]
    live_stmt = select(func.count(), func.avg(KnowledgeItem.confidence)).where(KnowledgeItem.status.in_(live))
    if domain:
        live_stmt = live_stmt.where(KnowledgeItem.domain_id == domain)
    live_count, avg_conf = db.execute(live_stmt).one()
    reval_stmt = select(func.count()).select_from(KnowledgeItem).where(KnowledgeItem.needs_revalidation.is_(True))
    if domain:
        reval_stmt = reval_stmt.where(KnowledgeItem.domain_id == domain)
    conflicts_stmt = select(func.count()).select_from(Conflict).where(Conflict.status == "OPEN")
    if domain:
        conflicts_stmt = conflicts_stmt.where(Conflict.domain_id == domain)
    llm_stmt = select(
        func.count(),
        func.coalesce(func.sum(LLMCall.prompt_tokens), 0),
        func.coalesce(func.sum(LLMCall.completion_tokens), 0),
        func.coalesce(func.avg(LLMCall.latency_ms), 0),
    )
    calls, p_tok, c_tok, avg_lat = db.execute(llm_stmt).one()
    failed_calls = db.execute(select(func.count()).select_from(LLMCall).where(LLMCall.ok.is_(False))).scalar_one()
    topic_stmt = (
        select(KnowledgeItem.topic, func.count()).group_by(KnowledgeItem.topic).order_by(func.count().desc()).limit(12)
    )
    if domain:
        topic_stmt = topic_stmt.where(KnowledgeItem.domain_id == domain)
    topics = [{"topic": t or "(unclassified)", "count": n} for t, n in db.execute(topic_stmt).all()]
    runs_stmt = select(Run).order_by(Run.started_at.desc()).limit(5)
    if domain:
        runs_stmt = runs_stmt.where(Run.domain_id == domain)
    evaluation = None
    if domain:
        ev = latest_evaluation(db, domain)
        if ev:
            evaluation = {
                "id": str(ev.id),
                "finished_at": ev.finished_at.isoformat() if ev.finished_at else None,
                "metrics": ev.metrics,
                "regression": ev.regression,
                "regression_details": ev.regression_details,
                "dataset_version": ev.dataset_version,
            }
    return StatsOut(
        domain=domain,
        sources=by(None, Source, Source.status),
        documents=by(None, Document, Document.status),
        knowledge=knowledge,
        knowledge_total=total,
        verified_ratio=(knowledge.get("VERIFIED", 0) / live_count) if live_count else 0.0,
        avg_confidence=float(avg_conf or 0.0),
        conflicts_open=db.execute(conflicts_stmt).scalar_one(),
        needs_revalidation=db.execute(reval_stmt).scalar_one(),
        jobs={str(k): v for k, v in db.execute(select(Job.status, func.count()).group_by(Job.status)).all()},
        llm={
            "calls": calls,
            "prompt_tokens": int(p_tok),
            "completion_tokens": int(c_tok),
            "avg_latency_ms": int(avg_lat or 0),
            "failed": failed_calls,
            "cost_tokens_per_item": (int(p_tok + c_tok) // live_count) if live_count else 0,
        },
        topics=topics,
        recent_runs=_run_out(db, db.execute(runs_stmt).scalars().all()),
        evaluation=evaluation,
        ops=ops_metrics(db, domain),
    )


# ----------------------------------------------------------------------------- docs + frontend (SPA)

if DOCS_DIR.exists():
    # User guide and design notes, served in-app at /docs/<file>.html
    app.mount("/docs", StaticFiles(directory=DOCS_DIR, html=True), name="docs")

if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith(("api/", "docs/")):
            raise HTTPException(404)
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        # The SPA shell must never be cached: it references hashed asset names that change on every build.
        return FileResponse(FRONTEND_DIST / "index.html", headers={"Cache-Control": "no-cache, must-revalidate"})
else:

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        return {
            "message": "Knowledge Platform API. Build the frontend (cd frontend && npm run build) or open /api/docs."
        }
