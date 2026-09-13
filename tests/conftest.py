from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("KP_DOMAINS_PATH", str(ROOT / "domains"))


@pytest.fixture(scope="session")
def domains_dir() -> Path:
    return ROOT / "domains"


def _db_available() -> bool:
    try:
        from sqlalchemy import create_engine, text

        from knowledge_platform.config import get_settings

        engine = create_engine(get_settings().database_url, connect_args={"connect_timeout": 3})
        with engine.connect() as c:
            c.execute(text("select 1"))
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(not _db_available(), reason="PostgreSQL not reachable (KP_DATABASE_URL)")
