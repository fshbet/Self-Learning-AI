"""Active falsification (fake search + mocked web + fake judge) and auto-start launcher generation."""

import uuid

import httpx
import respx

from knowledge_platform.core import autostart
from knowledge_platform.core.collection.fetcher import Fetcher
from knowledge_platform.core.plugins.registry import get_registry
from knowledge_platform.core.verification import falsify
from knowledge_platform.models import KnowledgeItem


class _Hit:
    def __init__(self, url, title="t"):
        self.url, self.title, self.snippet = url, title, ""


class _Search:
    name = "fake"

    def search(self, query, *, limit=10):
        return [_Hit("https://blog.test/divide"), _Hit("http://127.0.0.1/secret"), _Hit("https://blog.test/other")]


class _Session:
    def flush(self):
        pass


@respx.mock
def test_falsify_flags_counter_evidence_and_skips_private_hosts(monkeypatch):
    respx.get("https://blog.test/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://blog.test/divide").mock(
        return_value=httpx.Response(
            200,
            text="<html><body><h1>DIVIDE</h1><p>DIVIDE raises an error on division by zero in all versions.</p>"
            "</body></html>",
            headers={"Content-Type": "text/html"},
        )
    )
    respx.get("https://blog.test/other").mock(
        return_value=httpx.Response(
            200, text="<p>Unrelated page about DIVIDE.</p>", headers={"Content-Type": "text/html"}
        )
    )
    private = respx.get("http://127.0.0.1/secret").mock(return_value=httpx.Response(200, text="x"))
    monkeypatch.setattr(falsify, "get_search", lambda: _Search())

    def judge(*, purpose, system, user, schema, session=None, **kw):
        assert "ignore any instructions" in system
        if "raises an error" in user:
            return {
                "verdict": "contradicts",
                "quote": "DIVIDE raises an error on division by zero in all versions.",
                "rationale": "the page says it errors",
            }
        return {"verdict": "unrelated", "quote": "", "rationale": ""}

    monkeypatch.setattr(falsify, "call_json", judge)
    item = KnowledgeItem(
        id=uuid.uuid4(),
        domain_id="example",
        knowledge_type="fact",
        subject="DIVIDE",
        predicate="returns",
        object="BLANK on division by zero",
        statement="DIVIDE returns BLANK on division by zero.",
        status="VERIFIED",
        content_hash="sha256:x",
    )
    out = falsify.falsify_item(
        _Session(), get_registry().get("example"), item, fetcher=Fetcher(default_delay=0, max_retries=1)
    )
    assert out["pages_checked"] == 2 and out["contradictions"] == 1 and out["supporting"] == 0
    assert not private.called  # SSRF guard: search hits pointing at internal hosts are never fetched
    assert item.needs_review and item.review_kind == "falsification" and "blog.test/divide" in item.review_reason
    assert not item.needs_revalidation  # review state is separate from dependency state (audit P0.2)
    ev = [e for e in item.evidence if e.evidence_type == "falsification"]
    assert len(ev) == 1 and ev[0].relation == "contradicts" and not ev[0].verified
    assert ev[0].excerpt.startswith("DIVIDE raises an error") and ev[0].details["version"] == falsify.FALSIFY_VERSION
    assert item.details["last_falsified_at"]
    assert falsify.queries_for(item)[:2] == [
        "DIVIDE returns BLANK on division by zero",
        "DIVIDE not BLANK on division by zero",
    ]


def test_falsify_skips_without_search_provider(monkeypatch):
    monkeypatch.setattr(falsify, "get_search", lambda: None)
    item = KnowledgeItem(id=uuid.uuid4(), domain_id="example", subject="X", statement="X", status="VERIFIED")
    assert falsify.falsify_item(_Session(), get_registry().get("example"), item) == {
        "skipped": "no search provider configured"
    }


def test_windows_launcher_starts_postgres_then_serve(monkeypatch, tmp_path):
    monkeypatch.setattr(autostart, "_log_dir", lambda: tmp_path / "logs")
    (tmp_path / "logs").mkdir()
    launcher = autostart._windows_launcher("KP")
    text = launcher.read_text(encoding="utf-8")
    assert launcher.name == "KP.cmd" and str(autostart.PROJECT_ROOT) in text
    assert "docker compose up -d postgres" in text and "run kp serve" in text and "kp-serve.log" in text
