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


def test_snapshot_is_reproducible_verifiable_and_gated(plugin, fake_providers):
    """Runs after the pipeline test: builds two full snapshots of unchanged knowledge."""
    import io
    import json
    import zipfile

    from knowledge_platform.core.export.snapshot import build_snapshot, read_file, verify_snapshot, zip_snapshot

    with session_scope() as s:
        a = build_snapshot(s, plugin, created_by="test")
        assert a.status == "ready", a.error
        b = build_snapshot(s, plugin, created_by="test")
        assert b.status == "ready" and b.version == a.version + 1
        assert a.integrity_hash == b.integrity_hash  # identical knowledge → identical bytes → identical hash
        m = a.manifest
        assert m["schema_version"] == "1.0" and m["counts"]["knowledge"] >= 1 and m["gate"]["provenance_ok"]
        assert set(m["files"]) >= {
            "knowledge.jsonl",
            "evidence.jsonl",
            "sources.jsonl",
            "relationships.jsonl",
            "examples.jsonl",
            "negative.jsonl",
            "glossary.json",
            "conflicts.json",
            "changelog.jsonl",
            "ai/knowledge.jsonl",
            "ai/knowledge.md",
            "knowledge.html",
            "README.md",
        }
        assert m["generation"]["extractor_version"] and m["generation"]["embedding"]
        # records carry provenance and citations
        first = json.loads(read_file(a, "knowledge.jsonl").decode().splitlines()[0])
        assert first["origin"] == "DIRECT" and first["provenance"] == "OFFICIAL" and first["evidence_ids"]
        ai_first = json.loads(read_file(a, "ai/knowledge.jsonl").decode().splitlines()[0])
        assert ai_first["citations"] and ai_first["citations"][0]["url"].startswith("https://fixture.test")
        assert "CALCULATE" in read_file(a, "ai/knowledge.md").decode()
        # conflicts from the earlier test are exported, with both sides retained
        conflicts = json.loads(read_file(a, "conflicts.json").decode())
        assert conflicts and conflicts[0]["status"] == "OPEN"
        # integrity verification and zip packaging
        v = verify_snapshot(a)
        assert v["ok"], v
        with zipfile.ZipFile(io.BytesIO(zip_snapshot(a))) as zf:
            names = zf.namelist()
            assert any(n.endswith("/manifest.json") for n in names) and any(
                n.endswith("/ai/knowledge.jsonl") for n in names
            )


def test_delta_snapshot_between_two_full_snapshots(plugin, fake_providers):
    """Runs after the snapshot test: change knowledge, build a delta, check it describes exactly the change."""
    import json

    from knowledge_platform.core.export.delta import build_delta_snapshot
    from knowledge_platform.core.export.snapshot import latest_ready, read_file, verify_snapshot
    from knowledge_platform.core.knowledge_entry import KnowledgeEntry, create_knowledge
    from knowledge_platform.core.versioning.lifecycle import transition

    with session_scope() as s:
        base = latest_ready(s, plugin.id)
        assert base is not None and base.kind == "full"
        base_id, base_version = base.id, base.version
        # one exported item is rejected (drops out of the snapshot), one human-authored item is added
        exported = (
            s.execute(
                select(KnowledgeItem).where(
                    KnowledgeItem.domain_id == plugin.id,
                    KnowledgeItem.status.in_(
                        [ItemStatus.SUPPORTED, ItemStatus.VERIFIED, ItemStatus.CONFLICTED, ItemStatus.STALE]
                    ),
                )
            )
            .scalars()
            .first()
        )
        assert exported is not None
        removed_id = str(exported.id)
        transition(s, exported, ItemStatus.REJECTED, actor="test", reason="delta test")
        added = create_knowledge(
            s,
            plugin,
            KnowledgeEntry(
                statement="DELTA-WIDGET reports the snapshot delta as a fixture statement.",
                subject="DELTA-WIDGET",
                predicate="reports",
                object="snapshot delta",
                knowledge_type="fact",
                provenance="ORGANIZATION",
                provided_by="qa",
                authority=90,
                evidence_text="internal delta fixture",
            ),
        )
        added_id = str(added.id)

    with session_scope() as s:
        delta = build_delta_snapshot(s, plugin, base_snapshot_id=base_id, created_by="test")
        assert delta.status == "ready", delta.error
        assert delta.kind == "delta" and delta.base_snapshot_id == base_id
        m = delta.manifest
        assert m["base_version"] == base_version and m["head_version"] == delta.version - 1
        d = json.loads(read_file(delta, "delta.json").decode())
        assert d["knowledge"]["added"] == [added_id]
        assert d["knowledge"]["removed"] == [removed_id] and d["changelog_entries"] >= 1
        assert m["counts"]["knowledge_added"] == 1 and m["counts"]["knowledge_removed"] == 1
        # delta files carry the full head records of changed items and the AI Knowledge Source for them
        recs = {json.loads(line)["id"] for line in read_file(delta, "knowledge.jsonl").decode().splitlines() if line}
        assert added_id in recs and removed_id not in recs
        gone = {json.loads(line)["id"] for line in read_file(delta, "removed.jsonl").decode().splitlines() if line}
        assert gone == {removed_id}
        ai = {json.loads(line)["id"] for line in read_file(delta, "ai/knowledge.jsonl").decode().splitlines() if line}
        assert added_id in ai
        assert f"v{base_version} to v{delta.version - 1}" in read_file(delta, "README.md").decode()
        assert verify_snapshot(delta)["ok"]
        # the delta is derived only from stored files: rebuilding it between the same pair is byte-identical
        again = build_delta_snapshot(
            s, plugin, base_snapshot_id=base_id, head_snapshot_id=uuid.UUID(m["head_snapshot_id"]), created_by="test"
        )
        assert again.integrity_hash == delta.integrity_hash
    with session_scope() as s:
        s.execute(delete(KnowledgeItem).where(KnowledgeItem.id == uuid.UUID(added_id)))
