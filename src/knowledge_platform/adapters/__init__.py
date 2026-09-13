"""Provider adapters. Business code imports only the factories in this package.

LLM and embedding adapters are built from the *runtime* configuration (database overrides over
environment) and rebuilt whenever that configuration changes; object store and search come from
the environment only.
"""

from __future__ import annotations

import threading
from functools import lru_cache
from typing import Any

from ..config import get_settings
from .embeddings.base import EmbeddingProvider
from .llm.base import LLMProvider
from .search.base import SearchProvider
from .storage.base import ObjectStore

_lock = threading.Lock()
_llm: tuple[str, LLMProvider] | None = None
_embedder: tuple[str, EmbeddingProvider] | None = None


def build_llm(provider: str, base_url: str, api_key: str | None) -> LLMProvider:
    s = get_settings()
    if provider == "ollama":
        from .llm.ollama import OllamaLLM

        return OllamaLLM(base_url=base_url, timeout=s.llm_timeout_seconds, num_ctx=s.llm_num_ctx)
    if provider == "openai":
        from .llm.openai_compat import OpenAICompatLLM

        return OpenAICompatLLM(base_url=base_url, api_key=api_key, timeout=s.llm_timeout_seconds)
    if provider == "anthropic":
        from .llm.anthropic import AnthropicLLM

        return AnthropicLLM(base_url=base_url, api_key=api_key, timeout=s.llm_timeout_seconds)
    raise ValueError(f"Unknown LLM provider: {provider}")


def build_embedder(provider: str, base_url: str, api_key: str | None, model: str, dimension: int) -> EmbeddingProvider:
    if provider == "ollama":
        from .embeddings.ollama import OllamaEmbeddings

        return OllamaEmbeddings(base_url=base_url, model=model, dimension=dimension)
    if provider == "openai":
        from .embeddings.openai_compat import OpenAICompatEmbeddings

        return OpenAICompatEmbeddings(base_url=base_url, api_key=api_key, model=model, dimension=dimension)
    raise ValueError(f"Unknown embedding provider: {provider}")


def get_llm() -> LLMProvider:
    global _llm
    from ..core.runtime_config import effective_config

    cfg = effective_config()
    key = f"{cfg.llm.provider}|{cfg.llm.base_url}|{bool(cfg.llm.api_key)}|{cfg.llm.api_key or ''}"
    with _lock:
        if _llm is None or _llm[0] != key:
            _llm = (key, build_llm(cfg.llm.provider, cfg.llm.base_url, cfg.llm.api_key))
        return _llm[1]


def get_embedder() -> EmbeddingProvider:
    global _embedder
    from ..core.runtime_config import effective_config

    cfg = effective_config()
    e = cfg.embedding
    assert e is not None
    key = f"{e.provider}|{e.base_url}|{e.api_key or ''}|{cfg.embedding_model}|{cfg.embedding_dimension}"
    with _lock:
        if _embedder is None or _embedder[0] != key:
            _embedder = (
                key,
                build_embedder(e.provider, e.base_url, e.api_key, cfg.embedding_model, cfg.embedding_dimension),
            )
        return _embedder[1]


def reset_adapters() -> None:
    global _llm, _embedder
    with _lock:
        _llm = None
        _embedder = None


def probe_provider(provider: str, base_url: str, api_key: str | None) -> dict[str, Any]:
    """List models for a provider without changing the active configuration (Settings page)."""
    llm = build_llm(provider, base_url, api_key)
    models = llm.available_models()
    return {"ok": bool(models), "models": models, "provider": provider, "base_url": base_url}


@lru_cache
def get_object_store() -> ObjectStore:
    s = get_settings()
    if s.object_store == "local":
        from .storage.local import LocalObjectStore

        return LocalObjectStore(s.local_store_path)
    if s.object_store == "s3":
        from .storage.s3 import S3ObjectStore

        return S3ObjectStore(
            endpoint=s.s3_endpoint,
            bucket=s.s3_bucket,
            access_key=s.s3_access_key,
            secret_key=s.s3_secret_key,
            region=s.s3_region,
        )
    raise ValueError(f"Unknown object store: {s.object_store}")


@lru_cache
def get_search() -> SearchProvider | None:
    s = get_settings()
    if s.search_provider == "searxng":
        from .search.searxng import SearXNGSearch

        return SearXNGSearch(base_url=s.searxng_url)
    return None
