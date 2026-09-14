"""Application settings.

Every value can be overridden with a ``KP_``-prefixed environment variable or a
``.env`` file in the working directory. Nothing provider-specific leaks out of
this module: business code asks for ``settings.llm_provider`` and the adapter
layer decides what that means.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = "postgresql+psycopg://kp:kp@localhost:5433/kp"

    # Object storage
    object_store: Literal["local", "s3"] = "local"
    local_store_path: Path = Path("./data/raw")
    s3_endpoint: str | None = None
    s3_bucket: str = "kp-raw"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "auto"

    # LLM (env defaults; the Settings page stores overrides in the database)
    llm_provider: Literal["ollama", "openai", "anthropic"] = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str | None = None
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_api_key: str | None = None
    settings_allow_keys: bool = True  # allow API keys to be stored (masked) in the local settings table
    llm_model_triage: str = "qwen3:8b"
    llm_model_extract: str = "qwen3:8b"
    llm_model_reason: str = "qwen3:8b"
    llm_timeout_seconds: float = 300.0
    llm_num_ctx: int = 8192

    # Embeddings
    embedding_provider: Literal["ollama", "openai"] = "ollama"
    embedding_model: str = "nomic-embed-text"
    embedding_dimension: int = 768

    # Web search
    search_provider: Literal["searxng", "none"] = "searxng"
    searxng_url: str = "http://localhost:8080"

    # Collector
    user_agent: str = "KnowledgePlatformBot/0.1 (+mailto:you@example.com)"
    crawl_default_delay_seconds: float = 1.5
    crawl_timeout_seconds: float = 30.0
    crawl_max_pages_default: int = 50
    fetch_allow_private: bool = False  # SSRF guard: refuse loopback/private/link-local targets and redirects

    # Extraction
    chunk_max_chars: int = 6000
    chunk_min_chars: int = 200
    near_duplicate_distance: float = 0.06  # cosine distance below which two statements are the same

    # Plugins
    domains_path: str = "./domains"

    # API / worker
    api_host: str = "127.0.0.1"
    api_port: int = 8010
    allowed_origins: str = ""  # extra browser origins allowed to use the API (comma-separated), see api/security.py
    embedded_worker: bool = True
    worker_poll_seconds: float = 2.0
    scheduler_enabled: bool = True
    scheduler_interval_seconds: int = 600
    eval_after_pipeline: bool = True  # run the golden set after every completed pipeline run
    eval_interval_hours: int = 24  # periodic evaluation while serving (0 disables)
    eval_regression_threshold: float = 0.05  # accuracy / citation drop that flags a regression
    eval_retrieval_k: int = 8
    snapshot_interval_hours: int = 0  # periodic full Canonical Knowledge Snapshot per domain (0 = off)
    snapshot_after_pipeline: bool = False  # export a snapshot after every pipeline run that added knowledge
    log_level: str = "INFO"

    # Derived helpers -----------------------------------------------------
    domain_dirs: list[Path] = Field(default_factory=list, exclude=True)

    def model_post_init(self, __context: object) -> None:  # noqa: D401
        self.domain_dirs = [Path(p.strip()) for p in self.domains_path.split(",") if p.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
