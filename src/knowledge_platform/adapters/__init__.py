"""Provider adapters. Business code imports only the factories in this package."""

from __future__ import annotations

from functools import lru_cache

from ..config import get_settings
from .embeddings.base import EmbeddingProvider
from .llm.base import LLMProvider
from .search.base import SearchProvider
from .storage.base import ObjectStore


@lru_cache
def get_llm() -> LLMProvider:
    s = get_settings()
    if s.llm_provider == "ollama":
        from .llm.ollama import OllamaLLM

        return OllamaLLM(base_url=s.ollama_base_url, timeout=s.llm_timeout_seconds, num_ctx=s.llm_num_ctx)
    raise ValueError(f"Unknown LLM provider: {s.llm_provider}")


@lru_cache
def get_embedder() -> EmbeddingProvider:
    s = get_settings()
    if s.embedding_provider == "ollama":
        from .embeddings.ollama import OllamaEmbeddings

        return OllamaEmbeddings(base_url=s.ollama_base_url, model=s.embedding_model, dimension=s.embedding_dimension)
    raise ValueError(f"Unknown embedding provider: {s.embedding_provider}")


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
