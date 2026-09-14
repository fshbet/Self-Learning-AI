"""Browser-origin boundary of the local API (audit P0.1). No database needed: refusals happen before any route."""

from fastapi.testclient import TestClient

from knowledge_platform.api.app import app
from knowledge_platform.api.security import allowed_origins, origin_allowed

client = TestClient(app)
EVIL = "https://evil.example"
GOOD = "http://127.0.0.1:8010"
MUTATING = [
    ("PUT", "/api/settings", {"values": {}}),
    ("POST", "/api/sources", {"domain": "x", "url": "https://x.test/"}),
    ("POST", "/api/runs", {"domain": "x", "kind": "pipeline"}),
    ("POST", "/api/snapshots", {"domain": "x"}),
    ("DELETE", "/api/sources/00000000-0000-0000-0000-000000000000", None),
    ("PATCH", "/api/sources/00000000-0000-0000-0000-000000000000", {"enabled": False}),
    ("POST", "/api/knowledge/00000000-0000-0000-0000-000000000000/falsify", None),
    ("POST", "/api/domains/reload", None),
]


def test_allowed_origins_cover_app_and_dev_server_only():
    origins = allowed_origins()
    assert GOOD in origins and "http://localhost:8010" in origins and "http://localhost:5173" in origins
    assert not origin_allowed(EVIL) and not origin_allowed("null") and not origin_allowed(None)
    assert origin_allowed(GOOD + "/")


def test_untrusted_origin_cannot_mutate_any_endpoint():
    for method, path, body in MUTATING:
        r = client.request(method, path, json=body, headers={"Origin": EVIL})
        assert r.status_code == 403, (method, path, r.status_code)
        assert "cross-origin" in r.json()["detail"]
        assert "access-control-allow-origin" not in r.headers


def test_sec_fetch_site_cross_site_is_refused_even_without_origin():
    r = client.put("/api/settings", json={"values": {}}, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_preflight_from_untrusted_origin_is_refused_and_trusted_is_answered():
    headers = {"Access-Control-Request-Method": "PUT", "Access-Control-Request-Headers": "content-type"}
    bad = client.options("/api/settings", headers={"Origin": EVIL, **headers})
    assert bad.status_code == 400 and "access-control-allow-origin" not in bad.headers
    good = client.options("/api/settings", headers={"Origin": GOOD, **headers})
    assert good.status_code == 200 and good.headers["access-control-allow-origin"] == GOOD
    assert "PUT" in good.headers["access-control-allow-methods"]


def test_reads_and_non_browser_clients_are_unaffected():
    # GET from anywhere still answers (a foreign page cannot *read* it: no CORS header is granted)
    r = client.get("/api/health", headers={"Origin": EVIL})
    assert r.status_code == 200 and "access-control-allow-origin" not in r.headers
    r = client.get("/api/health", headers={"Origin": GOOD})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == GOOD
    # no Origin header at all (curl, kp CLI, scripts): the guard does not interfere — the route decides
    r = client.post("/api/domains/reload")
    assert r.status_code != 403
