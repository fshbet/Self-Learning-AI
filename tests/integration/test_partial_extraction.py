"""A section the model cannot process is never silently lost (found by the P2.0 crawl: timeouts and malformed
JSON on large sections were recorded as extracted, so the next crawl skipped them as unchanged)."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import delete, select
from tests.conftest import jobs_since, requires_db

from knowledge_platform import adapters
from knowledge_platform.adapters.embeddings.base import EmbeddingProvider
from knowledge_platform.adapters.llm.base import LLMProvider, LLMResult
from knowledge_platform.core.collection.collector import crawl_source
from knowledge_platform.core.collection.fetcher import Fetcher
from knowledge_platform.core.domains import sync_domain
from knowledge_platform.core.orchestration.jobs import extract_document_job
from knowledge_platform.core.pipeline import ingest_document
from knowledge_platform.core.plugins.registry import load_plugin_dir
from knowledge_platform.db import session_scope
from knowledge_platform.models import Document, DocumentStatus, Domain, Job, KnowledgeItem, LLMCall, Source, utcnow

pytestmark = requires_db
T0 = utcnow()  # jobs the tests create are newer than this; cleanups never touch older ones
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "incremental"
DOMAIN_ID = "itest-partial-" + uuid.uuid4().hex[:6]
_CLAIM = re.compile(r"^([A-Z]+FN) (\w+) (.+?)\.")


class FlakyLLM(LLMProvider):
    """Fails on the Gamma section until told otherwise."""

    name = "fake"

    def __init__(self) -> None:
        self.fail_gamma = True
        self.calls: list[str] = []

    def generate(self, *, system, user, model, temperature=0.0):
        return LLMResult(text="n/a", model=model)

    def generate_json(self, *, system, user, model, schema, temperature=0.0):
        if "verdict" in json.dumps(schema):
            return LLMResult(text=json.dumps({"verdict": "compatible", "rationale": "fixture"}), model=model)
        text = user.split("--- SECTION TEXT START ---")[1].split("--- SECTION TEXT END ---")[0].strip()
        m = _CLAIM.match(text)
        if not m:
            return LLMResult(text=json.dumps({"items": []}), model=model)
        self.calls.append(m.group(1))
        if m.group(1) == "GAMMAFN" and self.fail_gamma:
            raise TimeoutError("timed out")
        subject, predicate, obj = m.groups()
        first = text[: m.end()]
        item = {
            "knowledge_type": "fact",
            "subject": subject,
            "predicate": predicate,
            "object": obj,
            "statement": first,
            "explanation": "",
            "topic": "Functions",
            "tags": [],
            "code": None,
            "product_version": None,
            "evidence_quote": first,
        }
        return LLMResult(text=json.dumps({"items": [item]}), model=model)

    def available_models(self):
        return ["fake"]


class FakeEmbeddings(EmbeddingProvider):
    name = "fake"
    model = "fake"
    dimension = 768

    def embed(self, texts):
        return [[(ch % 13) / 13.0 for ch in t.encode("utf-8")[:768]] + [0.0] * (768 - min(len(t), 768)) for t in texts]


@pytest.fixture(scope="module")
def plugin(tmp_path_factory):
    d = tmp_path_factory.mktemp("domains") / DOMAIN_ID
    d.mkdir()
    (d / "plugin.yaml").write_text(
        f"api_version: '1.0'\nid: {DOMAIN_ID}\nname: Partial fixture\ntaxonomy:\n  - name: Functions\n",
        encoding="utf-8",
    )
    (d / "sources.yaml").write_text(
        "sources:\n  - key: fx\n    name: Fixture site\n    url: https://fixture.test/docs/\n    authority: 95\n"
        "    source_class: official\n    max_depth: 1\n    max_pages: 10\n",
        encoding="utf-8",
    )
    return load_plugin_dir(d)


@pytest.fixture
def llm(monkeypatch):
    llm, emb = FlakyLLM(), FakeEmbeddings()
    monkeypatch.setattr(adapters, "get_llm", lambda: llm)
    monkeypatch.setattr(adapters, "get_embedder", lambda: emb)
    monkeypatch.setattr("knowledge_platform.core.llm_service.get_llm", lambda: llm)
    monkeypatch.setattr("knowledge_platform.core.retrieval.embeddings.get_embedder", lambda: emb)
    monkeypatch.setattr("knowledge_platform.core.retrieval.search.get_embedder", lambda: emb)
    return llm


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield
    with session_scope() as s:
        s.execute(delete(Domain).where(Domain.id == DOMAIN_ID))
        s.execute(delete(LLMCall).where(LLMCall.provider == "fake"))
        s.execute(delete(Job).where(Job.idempotency_key.like("extract:%:partial%"), jobs_since(T0)))


@respx.mock
def test_failed_sections_are_retried_not_forgotten(plugin, llm, monkeypatch):
    from types import SimpleNamespace

    from knowledge_platform.core.orchestration import jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "get_registry", lambda: SimpleNamespace(get=lambda _id: plugin))
    respx.get("https://fixture.test/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://fixture.test/docs/").mock(
        return_value=httpx.Response(
            200, text=(FIXTURES / "index.html").read_text(), headers={"Content-Type": "text/html"}
        )
    )
    respx.get("https://fixture.test/docs/guide").mock(
        return_value=httpx.Response(
            200, text=(FIXTURES / "guide_v1.html").read_text(), headers={"Content-Type": "text/html"}
        )
    )
    with session_scope() as s:
        sync_domain(s, plugin)
        src = s.execute(select(Source).where(Source.domain_id == DOMAIN_ID)).scalar_one()
        crawl_source(s, src, fetcher=Fetcher(default_delay=0, max_retries=1))
        guide = s.execute(select(Document).where(Document.url == "https://fixture.test/docs/guide")).scalar_one()
        result = ingest_document(s, guide, plugin)
        assert result.extraction["chunks"] == 4 and result.extraction["chunks_failed"] == 1
        assert result.items_created == 3  # the other sections' knowledge is kept
        # the document is not "done": the failed section is marked, not recorded as extracted
        assert guide.status == DocumentStatus.FETCHED and "1 section(s) failed" in (guide.error or "")
        failed = [h for h in guide.chunk_hashes if h.get("failed")]
        assert len(failed) == 1 and failed[0]["heading"].endswith("Gamma") and "timed out" in failed[0]["failed"]
        guide_id = guide.id
        # the job path enqueues a delayed retry of the same document
        job = Job(type="extract_document", payload={"document_id": str(guide_id)}, priority=100)
        s.add(job)
        s.flush()
        out = extract_document_job(s, job)
        assert out["extraction_chunks_unchanged"] == 3 and out["extraction_chunks_failed"] == 1
        assert out["partial_attempt"] == 1 and out["retry_enqueued"] is True
        retry = s.execute(select(Job).where(Job.idempotency_key == f"extract:{guide_id}:partial1")).scalar_one()
        assert retry.payload["partial_attempt"] == 1 and retry.run_at > job.created_at
        # the model recovers: only the failed section is sent, and the document completes
        llm.calls.clear()
        llm.fail_gamma = False
        again = ingest_document(s, guide, plugin)
        assert llm.calls == ["GAMMAFN"]
        assert again.extraction["chunks_unchanged"] == 3 and again.extraction["chunks_failed"] == 0
        assert again.items_created == 1
        assert guide.status == DocumentStatus.EXTRACTED and guide.error is None
        assert not any(h.get("failed") for h in guide.chunk_hashes)
        subjects = {
            k.subject for k in s.execute(select(KnowledgeItem).where(KnowledgeItem.domain_id == DOMAIN_ID)).scalars()
        }
        assert subjects == {"ALPHAFN", "BETAFN", "GAMMAFN", "DELTAFN"}
