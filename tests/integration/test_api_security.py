"""Allowed-origin mutations succeed end to end (needs PostgreSQL); refusals are covered by the unit test."""

from fastapi.testclient import TestClient
from sqlalchemy import delete
from tests.conftest import requires_db

from knowledge_platform.api.app import app
from knowledge_platform.core import runtime_config
from knowledge_platform.db import session_scope
from knowledge_platform.models import Setting

pytestmark = requires_db
client = TestClient(app)


def test_frontend_and_localhost_origins_can_mutate():
    try:
        for origin in ("http://127.0.0.1:8010", "http://localhost:8010", "http://localhost:5173"):
            r = client.put("/api/settings", json={"values": {"eval.interval_hours": 7}}, headers={"Origin": origin})
            assert r.status_code == 200, (origin, r.text)
            assert r.headers["access-control-allow-origin"] == origin
            assert r.json()["evaluation"]["interval_hours"] == 7
        # same request from a foreign page is refused before it reaches the database
        r = client.put(
            "/api/settings", json={"values": {"eval.interval_hours": 1}}, headers={"Origin": "https://evil.example"}
        )
        assert r.status_code == 403
        assert client.get("/api/settings").json()["evaluation"]["interval_hours"] == 7
        # the CLI / scripts (no Origin header) keep working
        assert client.put("/api/settings", json={"values": {"eval.interval_hours": 8}}).status_code == 200
    finally:
        with session_scope() as s:
            s.execute(delete(Setting))
        runtime_config.invalidate()
