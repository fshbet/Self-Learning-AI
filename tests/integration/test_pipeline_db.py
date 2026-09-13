"""End-to-end pipeline against a real PostgreSQL (skipped when the database is unreachable).

Uses recorded HTML fixtures and a fake LLM so the test is deterministic and offline.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import delete, select
from tests.conftest import requires_db

from knowledge_platform import adapters
from knowledge_platform.adapters.embeddings.base import EmbeddingProvider
from knowledge_platform.adapters.llm.base import LLMProvider, LLMResult
from knowledge_platform.core.collection.collector import crawl_source
from knowledge_platform.core.collection.fetcher import Fetcher
from knowledge_platform.core.domains import sync_domain
from knowledge_platform.core.pipeline import ingest_document
from knowledge_platform.core.plugins.registry import load_plugin_dir
from knowledge_platform.core.retrieval.search import hybrid_search
from knowledge_platform.db import session_scope
from knowledge_platform.models import Conflict, Document, Domain, ItemStatus, KnowledgeItem, Source

pytestmark = requires_db

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
DOMAIN_ID = "itest-" + uuid.uuid4().hex[:6]


class FakeLLM(LLMProvider):
    """Returns one item per chunk whose quote is the first sentence of the chunk."""

    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, *, system, user, model, temperature=0.0):
        if "swallow" in user:  # off-topic question: the answer model must abstain
            return LLMResult(text="The knowledge base does not contain information about this.", model=model)
        return LLMResult(text="CALCULATE modifies the filter context [1].", model=model)

    def generate_json(self, *, system, user, model, schema, temperature=0.0):
        self.calls += 1
        if "verdict" in json.dumps(schema):  # conflict adjudication
            return LLMResult(text=json.dumps({"verdict": "contradict", "rationale": "fixture"}), model=model)
        if "supported_by_citations" in json.dumps(schema):  # evaluation judge
            return LLMResult(
                text=json.dumps(
                    {
                        "correct": True,
                        "supported_by_citations": True,
                        "hallucinated_claims": [],
                        "missing_points": [],
                        "rationale": "fixture",
                    }
                ),
                model=model,
            )
        text = user.split("--- SECTION TEXT START ---")[1].split("--- SECTION TEXT END ---")[0].strip()
        first = text.split(". ")[0].strip()[:160]
        obj = "filter context" if "CALCULATE" in text else "something"
        if "CONFLICT" in text:
            obj = "row context"
        items = [
            {
                "knowledge_type": "fact",
                "subject": "CALCULATE",
                "predicate": "modifies",
                "object": obj,
                "statement": f"CALCULATE modifies the {obj}.",
                "explanation": "",
                "topic": "Concepts/Terminology",
                "tags": [],
                "code": None,
                "product_version": None,
                "evidence_quote": first,
            }
        ]
        return LLMResult(text=json.dumps({"items": items}), model=model)

    def available_models(self):
        return ["fake"]


class FakeEmbeddings(EmbeddingProvider):
    name = "fake"
    model = "fake"
    dimension = 768

    def embed(self, texts):
        out = []
        for t in texts:
            v = [0.0] * 768
            for i, ch in enumerate(t.encode("utf-8")[:768]):
                v[i] = (ch % 13) / 13.0
            out.append(v)
        return out


@pytest.fixture(scope="module")
def plugin(tmp_path_factory):
    d = tmp_path_factory.mktemp("domains") / DOMAIN_ID
    d.mkdir()
    (d / "plugin.yaml").write_text(
        f"api_version: '1.0'\nid: {DOMAIN_ID}\nname: ITest\n"
        "taxonomy:\n  - name: Concepts\n    children: [Terminology]\n",
        encoding="utf-8",
    )
    (d / "evaluation.yaml").write_text(
        "\n".join(
            [
                "version: '1'",
                "questions:",
                "  - id: q-calc",
                "    question: What does CALCULATE modify?",
                "    expected_answer: the filter context",
                "    required_concepts: [filter context]",
                "    topic: Concepts",
                "    authoritative_sources: [https://fixture.test/docs/calculate]",
                "  - id: q-abstain",
                "    question: What is the airspeed of a swallow?",
                "    expect_abstain: true",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (d / "sources.yaml").write_text(
        "sources:\n  - key: fx\n    name: Fixture site\n    url: https://fixture.test/docs/\n    authority: 95\n"
        "    max_depth: 1\n    max_pages: 10\n",
        encoding="utf-8",
    )
    return load_plugin_dir(d)


@pytest.fixture(autouse=True)
def fake_providers(monkeypatch):
    llm, emb = FakeLLM(), FakeEmbeddings()
    monkeypatch.setattr(adapters, "get_llm", lambda: llm)
    monkeypatch.setattr(adapters, "get_embedder", lambda: emb)
    monkeypatch.setattr("knowledge_platform.core.llm_service.get_llm", lambda: llm)
    monkeypatch.setattr("knowledge_platform.core.retrieval.embeddings.get_embedder", lambda: emb)
    monkeypatch.setattr("knowledge_platform.core.retrieval.search.get_embedder", lambda: emb)
    monkeypatch.setattr("knowledge_platform.core.evaluation.runner.get_embedder", lambda: emb)
    yield llm


def _mock_site(version: int = 1):
    index = FIXTURES / "index.html"
    page = FIXTURES / f"page_v{version}.html"
    respx.get("https://fixture.test/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nAllow: /\n")
    )
    respx.get("https://fixture.test/docs/").mock(
        return_value=httpx.Response(200, text=index.read_text(), headers={"Content-Type": "text/html"})
    )
    respx.get("https://fixture.test/docs/calculate").mock(
        return_value=httpx.Response(
            200, text=page.read_text(), headers={"Content-Type": "text/html", "ETag": f'"v{version}"'}
        )
    )


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield
    with session_scope() as s:  # DB-level ON DELETE CASCADE removes sources, documents, items, evidence
        s.execute(delete(Domain).where(Domain.id == DOMAIN_ID))


@respx.mock
def test_crawl_extract_dedup_stale_and_search(plugin, fake_providers):
    fetcher = Fetcher(default_delay=0, max_retries=1)
    _mock_site(1)
    with session_scope() as s:
        sync_domain(s, plugin)
        src = s.execute(select(Source).where(Source.domain_id == DOMAIN_ID)).scalar_one()
        stats = crawl_source(s, src, fetcher=fetcher)
        assert stats.new == 2 and stats.failed == 0, stats
        for doc in s.execute(select(Document).where(Document.domain_id == DOMAIN_ID)).scalars():
            ingest_document(s, doc, plugin)

    with session_scope() as s:
        items = s.execute(select(KnowledgeItem).where(KnowledgeItem.domain_id == DOMAIN_ID)).scalars().all()
        live = [i for i in items if i.status not in (ItemStatus.REJECTED,)]
        # identical statements across chunks/docs fold into one item with several evidence rows
        assert len(live) >= 1
        top = max(live, key=lambda i: len(i.evidence))
        assert len(top.evidence) >= 2
        assert top.status in (ItemStatus.VERIFIED, ItemStatus.SUPPORTED)  # authority 95 -> level 2
        assert top.verification_level >= 2
        assert all(e.verified for e in top.evidence if e.evidence_type == "extraction")
        assert top.quality_factors["rule_version"]
        # idempotency: crawling again with unchanged content creates nothing
        src = s.execute(select(Source).where(Source.domain_id == DOMAIN_ID)).scalar_one()
        stats2 = crawl_source(s, src, fetcher=Fetcher(default_delay=0, max_retries=1))
        assert stats2.new == 0 and stats2.changed == 0 and stats2.unchanged >= 1
        before = len(items)

    with session_scope() as s:
        n = s.execute(select(KnowledgeItem).where(KnowledgeItem.domain_id == DOMAIN_ID)).scalars().all()
        assert len(n) == before
        hits = hybrid_search(s, domain_id=DOMAIN_ID, query="CALCULATE filter context", limit=5)
        assert hits and hits[0].item.statement.startswith("CALCULATE")

    # source changes: page v2 contradicts v1 -> old evidence vanishes (STALE) and a conflict opens
    respx.reset()
    _mock_site(2)
    with session_scope() as s:
        src = s.execute(select(Source).where(Source.domain_id == DOMAIN_ID)).scalar_one()
        stats3 = crawl_source(s, src, fetcher=Fetcher(default_delay=0, max_retries=1))
        assert stats3.changed == 1
        changed = s.execute(
            select(Document).where(Document.domain_id == DOMAIN_ID, Document.url.like("%/calculate"))
        ).scalar_one()
        assert changed.version == 2
        result = ingest_document(s, changed, plugin)
        assert result.conflicts >= 1 or result.items_stale >= 1

    with session_scope() as s:
        conflicts = s.execute(select(Conflict).where(Conflict.domain_id == DOMAIN_ID)).scalars().all()
        statuses = {
            i.status for i in s.execute(select(KnowledgeItem).where(KnowledgeItem.domain_id == DOMAIN_ID)).scalars()
        }
        assert conflicts or ItemStatus.STALE in statuses


def test_evaluation_runner_records_metrics_and_regression(plugin, fake_providers):
    """Runs after the pipeline test: the fixture domain has a verified CALCULATE item and a changed page."""
    from knowledge_platform.core.evaluation.runner import run_evaluation
    from knowledge_platform.models import EvaluationRun

    with session_scope() as s:
        ev = run_evaluation(s, plugin, triggered_by="test")
        assert ev.status == "DONE", ev.error
        assert ev.metrics["questions"] == 2
        by_id = {r.question_id: r for r in ev.results}
        assert by_id["q-abstain"].checks["abstention"]["ok"]
        calc = by_id["q-calc"]
        assert calc.citations, calc.answer
        assert calc.checks["required_concepts"]["ok"]
        assert calc.checks["retrieval_recall"]["value"] == 1.0
        assert calc.judge["correct"] is True
        assert ev.config["prompt_version"] and ev.config["embedding"]
        assert ev.baseline_run_id is None and not ev.regression
        first_id = ev.id

    with session_scope() as s:
        ev2 = run_evaluation(s, plugin, triggered_by="test")
        assert ev2.baseline_run_id == first_id
        assert ev2.regression is False
        assert "accuracy" in ev2.regression_details["metrics"]
        assert s.get(EvaluationRun, first_id).metrics["accuracy"] == ev2.metrics["accuracy"]
