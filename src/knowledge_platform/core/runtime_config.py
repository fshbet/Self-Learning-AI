"""Runtime model configuration: database overrides layered over environment settings (req. UI model selection).

The ``settings`` table holds user choices made in the UI (provider, base URL, API key, per-purpose models,
embedding model). Environment variables remain the fallback, so ``.env``-only deployments keep working.

API keys are stored in the local database for the single-user deployment; they are masked in every API
response and never logged. Set ``KP_SETTINGS_ALLOW_KEYS=false`` to require keys from the environment only.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import select

from ..config import get_settings
from ..db import session_scope
from ..models import Setting

log = logging.getLogger(__name__)

PURPOSES = ("triage", "extract", "reason")  # "answer" uses the reason model

# provider descriptors — the UI renders these; adapters are selected by id
PROVIDERS: dict[str, dict[str, Any]] = {
    "ollama": {
        "label": "Ollama (local)",
        "kind": "local",
        "needs_key": False,
        "default_base_url": "http://localhost:11434",
        "supports_embeddings": True,
        "json_mode": "schema",
    },
    "openai": {
        "label": "OpenAI-compatible API",
        "kind": "api",
        "needs_key": True,
        "default_base_url": "https://api.openai.com/v1",
        "supports_embeddings": True,
        "json_mode": "schema",
        "hint": "Works with OpenAI, Gemini (OpenAI-compatible endpoint), Groq, OpenRouter, Mistral, LM Studio, vLLM…",
    },
    "anthropic": {
        "label": "Anthropic API",
        "kind": "api",
        "needs_key": True,
        "default_base_url": "https://api.anthropic.com",
        "supports_embeddings": False,
        "json_mode": "tool",
    },
}


@dataclass
class ProviderConfig:
    provider: str
    base_url: str
    api_key: str | None = None


@dataclass
class ModelConfig:
    llm: ProviderConfig
    models: dict[str, str] = field(default_factory=dict)  # purpose -> model id
    embedding: ProviderConfig | None = None
    embedding_model: str = ""
    embedding_dimension: int = 768
    source: dict[str, str] = field(default_factory=dict)  # which keys came from the DB

    def model_for(self, purpose: str) -> str:
        return self.models.get(purpose) or self.models.get("reason", "")

    def masked(self) -> dict[str, Any]:
        d = asdict(self)
        for pc in (d["llm"], d["embedding"]):
            if pc and pc.get("api_key"):
                k = pc["api_key"]
                pc["api_key"] = f"{k[:4]}…{k[-4:]}" if len(k) > 10 else "•••"
                pc["has_key"] = True
            elif pc:
                pc["has_key"] = False
        return d


SETTING_KEYS = (
    "llm.provider",
    "llm.base_url",
    "llm.api_key",
    "llm.model.triage",
    "llm.model.extract",
    "llm.model.reason",
    "embedding.provider",
    "embedding.base_url",
    "embedding.api_key",
    "embedding.model",
    "embedding.dimension",
    "eval.after_pipeline",
    "eval.interval_hours",
    "eval.regression_threshold",
    "snapshot.interval_hours",
    "snapshot.after_pipeline",
)

_cache: dict[str, Any] = {"at": 0.0, "values": {}}
_lock = threading.Lock()
_TTL = 10.0


def _load_overrides(force: bool = False) -> dict[str, Any]:
    now = time.monotonic()
    with _lock:
        if not force and now - _cache["at"] < _TTL:
            return _cache["values"]
    try:
        with session_scope() as s:
            values = {row.key: row.value for row in s.execute(select(Setting)).scalars()}
    except Exception as exc:  # database down: fall back to env only
        log.debug("settings unavailable, using env: %s", exc)
        values = {}
    with _lock:
        _cache.update(at=now, values=values)
    return values


def invalidate() -> None:
    with _lock:
        _cache["at"] = 0.0
    from .. import adapters

    adapters.reset_adapters()


def get_overrides() -> dict[str, Any]:
    return dict(_load_overrides())


def set_overrides(values: dict[str, Any]) -> dict[str, Any]:
    """Persist a partial update; ``None`` deletes a key. Returns the stored overrides."""
    unknown = [k for k in values if k not in SETTING_KEYS]
    if unknown:
        raise ValueError(f"unknown setting keys: {unknown}")
    if not get_settings().settings_allow_keys and any(k.endswith(".api_key") and v for k, v in values.items()):
        raise ValueError("storing API keys in the database is disabled (KP_SETTINGS_ALLOW_KEYS=false)")
    with session_scope() as s:
        existing = {row.key: row for row in s.execute(select(Setting)).scalars()}
        for k, v in values.items():
            if v is None or v == "":
                if k in existing:
                    s.delete(existing[k])
                continue
            if k in existing:
                existing[k].value = v
            else:
                s.add(Setting(key=k, value=v))
    invalidate()
    return get_overrides()


def effective_config() -> ModelConfig:
    env = get_settings()
    o = _load_overrides()
    src: dict[str, str] = {}

    def pick(key: str, env_value: Any) -> Any:
        if key in o and o[key] not in (None, ""):
            src[key] = "db"
            return o[key]
        src[key] = "env"
        return env_value

    provider = str(pick("llm.provider", env.llm_provider))
    base_url = str(pick("llm.base_url", _env_base_url(env, provider)))
    api_key = pick("llm.api_key", _env_api_key(env, provider))
    models = {
        "triage": str(pick("llm.model.triage", env.llm_model_triage)),
        "extract": str(pick("llm.model.extract", env.llm_model_extract)),
        "reason": str(pick("llm.model.reason", env.llm_model_reason)),
    }
    emb_provider = str(pick("embedding.provider", env.embedding_provider))
    emb_base = str(pick("embedding.base_url", _env_base_url(env, emb_provider)))
    emb_key = pick("embedding.api_key", _env_api_key(env, emb_provider))
    return ModelConfig(
        llm=ProviderConfig(provider=provider, base_url=base_url, api_key=api_key or None),
        models=models,
        embedding=ProviderConfig(provider=emb_provider, base_url=emb_base, api_key=emb_key or None),
        embedding_model=str(pick("embedding.model", env.embedding_model)),
        embedding_dimension=int(pick("embedding.dimension", env.embedding_dimension)),
        source=src,
    )


def eval_config() -> dict[str, Any]:
    env = get_settings()
    o = _load_overrides()
    return {
        "after_pipeline": bool(o.get("eval.after_pipeline", env.eval_after_pipeline)),
        "interval_hours": int(o.get("eval.interval_hours", env.eval_interval_hours)),
        "regression_threshold": float(o.get("eval.regression_threshold", env.eval_regression_threshold)),
    }


def snapshot_config() -> dict[str, Any]:
    env = get_settings()
    o = _load_overrides()
    return {
        "interval_hours": int(o.get("snapshot.interval_hours", env.snapshot_interval_hours)),
        "after_pipeline": bool(o.get("snapshot.after_pipeline", env.snapshot_after_pipeline)),
    }


def discovery_config() -> dict[str, Any]:
    env = get_settings()
    o = _load_overrides()
    return {"interval_hours": int(o.get("discovery.interval_hours", env.discovery_interval_hours))}


def _env_base_url(env: Any, provider: str) -> str:
    return {
        "ollama": env.ollama_base_url,
        "openai": env.openai_base_url,
        "anthropic": env.anthropic_base_url,
    }.get(provider, PROVIDERS.get(provider, {}).get("default_base_url", ""))


def _env_api_key(env: Any, provider: str) -> str | None:
    return {"openai": env.openai_api_key, "anthropic": env.anthropic_api_key}.get(provider)


def config_fingerprint(cfg: ModelConfig) -> str:
    return json.dumps(asdict(cfg), sort_keys=True)
