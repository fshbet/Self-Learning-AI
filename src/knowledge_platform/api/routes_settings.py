"""Runtime settings: model providers, model listing, connection tests, re-embedding, evaluation schedule."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..adapters import build_embedder, build_llm, probe_provider
from ..core.orchestration.jobs import start_run
from ..core.orchestration.queue import enqueue
from ..core.runtime_config import (
    PROVIDERS,
    SETTING_KEYS,
    effective_config,
    eval_config,
    get_overrides,
    set_overrides,
    snapshot_config,
)
from ..db import get_db
from ..models import ItemStatus, KnowledgeItem

router = APIRouter(tags=["settings"])


class SettingsUpdate(BaseModel):
    values: dict[str, Any] = Field(description="partial update; null removes an override")


class ProbeRequest(BaseModel):
    provider: str
    base_url: str | None = None
    api_key: str | None = None  # omitted → use the stored key for that provider, if any


class EmbeddingTest(BaseModel):
    provider: str
    base_url: str | None = None
    api_key: str | None = None
    model: str


def _resolved_key(provider: str, api_key: str | None) -> str | None:
    if api_key:
        return api_key
    cfg = effective_config()
    if cfg.llm.provider == provider and cfg.llm.api_key:
        return cfg.llm.api_key
    if cfg.embedding and cfg.embedding.provider == provider and cfg.embedding.api_key:
        return cfg.embedding.api_key
    return None


def _embedding_stats(db: Session) -> dict[str, Any]:
    cfg = effective_config()
    ident = f"{cfg.embedding.provider}:{cfg.embedding_model}:{cfg.embedding_dimension}" if cfg.embedding else ""
    rows = db.execute(
        select(KnowledgeItem.embedding_model, func.count())
        .where(KnowledgeItem.status.notin_([ItemStatus.REJECTED]))
        .group_by(KnowledgeItem.embedding_model)
    ).all()
    by_model = {str(k): v for k, v in rows}
    return {
        "active_identity": ident,
        "by_model": by_model,
        "needs_reembed": sum(v for k, v in by_model.items() if k != ident),
    }


@router.get("/settings")
def get_settings_view(db: Session = Depends(get_db)) -> dict[str, Any]:
    cfg = effective_config()
    return {
        "effective": cfg.masked(),
        "overrides": {k: ("•••" if k.endswith(".api_key") else v) for k, v in get_overrides().items()},
        "providers": PROVIDERS,
        "keys": list(SETTING_KEYS),
        "evaluation": eval_config(),
        "snapshot": snapshot_config(),
        "embeddings": _embedding_stats(db),
    }


@router.put("/settings")
def update_settings(body: SettingsUpdate, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        set_overrides(body.values)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return get_settings_view(db)


@router.post("/settings/probe")
def probe(body: ProbeRequest) -> dict[str, Any]:
    """List the models a provider can serve (Ollama /api/tags, OpenAI /v1/models, Anthropic /v1/models)."""
    if body.provider not in PROVIDERS:
        raise HTTPException(404, f"unknown provider {body.provider}")
    base_url = body.base_url or PROVIDERS[body.provider]["default_base_url"]
    try:
        return probe_provider(body.provider, base_url, _resolved_key(body.provider, body.api_key))
    except Exception as exc:
        return {"ok": False, "models": [], "provider": body.provider, "base_url": base_url, "error": str(exc)[:300]}


@router.post("/settings/test-chat")
def test_chat(body: ProbeRequest, model: str) -> dict[str, Any]:
    """Send a one-line prompt to check a model actually answers (and returns JSON when asked)."""
    base_url = body.base_url or PROVIDERS[body.provider]["default_base_url"]
    llm = build_llm(body.provider, base_url, _resolved_key(body.provider, body.api_key))
    started = time.perf_counter()
    try:
        res = llm.generate_json(
            system="Reply with JSON.",
            user='Return {"ok": true, "model_family": "<your family name>"}.',
            model=model,
            schema={
                "type": "object",
                "properties": {"ok": {"type": "boolean"}, "model_family": {"type": "string"}},
                "required": ["ok"],
            },
        )
        return {
            "ok": True,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "text": res.text[:200],
            "tokens": res.prompt_tokens + res.completion_tokens,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:400]}


@router.post("/settings/test-embedding")
def test_embedding(body: EmbeddingTest) -> dict[str, Any]:
    """Embed a test string to detect the model's dimension before switching."""
    if body.provider not in PROVIDERS or not PROVIDERS[body.provider]["supports_embeddings"]:
        raise HTTPException(422, f"provider {body.provider} does not provide embeddings")
    base_url = body.base_url or PROVIDERS[body.provider]["default_base_url"]
    try:
        emb = build_embedder(
            body.provider, base_url, _resolved_key(body.provider, body.api_key), body.model, dimension=0
        )
        emb.dimension = 0
        # bypass the dimension check by asking the raw provider for one vector
        if body.provider == "ollama":
            resp = emb._client.post("/api/embed", json={"model": body.model, "input": ["dimension probe"]})  # noqa: SLF001
            resp.raise_for_status()
            dim = len(resp.json()["embeddings"][0])
        else:
            resp = emb._client.post("/embeddings", json={"model": body.model, "input": ["dimension probe"]})  # noqa: SLF001
            resp.raise_for_status()
            dim = len(resp.json()["data"][0]["embedding"])
        return {"ok": True, "dimension": dim}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:400]}


@router.post("/settings/reembed")
def reembed(domain: str | None = None, db: Session = Depends(get_db)) -> dict[str, str]:
    """Queue re-embedding of all live items with the active embedding model (required after switching models)."""
    run = start_run(db, domain_id=domain, kind="reembed", triggered_by="api")
    job = enqueue(
        db,
        "reembed",
        {"domain_id": domain},
        run_id=run.id,
        idempotency_key=f"reembed:{domain or 'all'}",
        priority=90,
        max_attempts=1,
    )
    db.commit()
    if job is None:
        raise HTTPException(409, "a re-embedding job is already queued or running")
    return {"run_id": str(run.id), "job_id": str(job.id)}
