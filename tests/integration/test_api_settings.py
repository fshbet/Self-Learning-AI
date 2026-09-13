"""Runtime settings API: overrides over env, masked keys, provider probing (needs PostgreSQL)."""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import delete
from tests.conftest import requires_db

from knowledge_platform.api.app import app
from knowledge_platform.core import runtime_config
from knowledge_platform.db import session_scope
from knowledge_platform.models import Setting

pytestmark = requires_db
client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_settings():
    yield
    with session_scope() as s:
        s.execute(delete(Setting))
    runtime_config.invalidate()


def test_overrides_layer_over_env_and_keys_are_masked():
    base = client.get("/api/settings").json()
    assert base["effective"]["source"]["llm.model.extract"] == "env"
    assert "ollama" in base["providers"] and "openai" in base["providers"] and "anthropic" in base["providers"]

    r = client.put(
        "/api/settings",
        json={
            "values": {
                "llm.model.extract": "qwen3:14b",
                "llm.api_key": "sk-secret-value-12345",
                "eval.interval_hours": 6,
                "snapshot.interval_hours": 12,
                "snapshot.after_pipeline": True,
            }
        },
    )
    assert r.status_code == 200, r.text
    eff = r.json()["effective"]
    assert eff["models"]["extract"] == "qwen3:14b" and eff["source"]["llm.model.extract"] == "db"
    assert eff["llm"]["has_key"] is True and "secret" not in eff["llm"]["api_key"]
    assert r.json()["overrides"]["llm.api_key"] == "•••"
    assert r.json()["evaluation"]["interval_hours"] == 6
    assert r.json()["snapshot"] == {"interval_hours": 12, "after_pipeline": True}
    # the operational view reflects the schedule
    ops = client.get("/api/stats").json()["ops"]
    assert ops["schedule"]["snapshot_interval_hours"] == 12 and "queue" in ops and "models" in ops
    assert {"queued", "running", "dead_letter", "jobs_retried"} <= set(ops["queue"])
    # the resolved model used by the pipeline follows the override
    assert runtime_config.effective_config().model_for("extract") == "qwen3:14b"

    # removing the override falls back to env
    r = client.put("/api/settings", json={"values": {"llm.model.extract": None}})
    assert r.json()["effective"]["source"]["llm.model.extract"] == "env"
    assert client.put("/api/settings", json={"values": {"nope": 1}}).status_code == 422


@respx.mock
def test_probe_lists_models_without_changing_config():
    respx.get("https://models.test/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "gpt-x"}, {"id": "gpt-a"}]})
    )
    r = client.post(
        "/api/settings/probe", json={"provider": "openai", "base_url": "https://models.test/v1", "api_key": "k"}
    )
    assert r.status_code == 200 and r.json()["ok"] and r.json()["models"] == ["gpt-a", "gpt-x"]
    assert runtime_config.effective_config().llm.provider == "ollama"  # unchanged
    r = client.post("/api/settings/probe", json={"provider": "nope"})
    assert r.status_code == 404
