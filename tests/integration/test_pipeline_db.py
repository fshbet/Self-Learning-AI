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
from knowledge_platform.core.export.schema import SCHEMA_VERSION
from knowledge_platform.core.pipeline import ingest_document
from knowledge_platform.core.plugins.registry import load_plugin_dir
from knowledge_platform.core.retrieval.search import hybrid_search
from knowledge_platform.db import session_scope
from knowledge_platform.models import Conflict, Document, Domain, Evidence, ItemStatus, KnowledgeItem, LLMCall, Source

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
        "    source_class: official\n    max_depth: 1\n    max_pages: 10\n",
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
        s.execute(delete(LLMCall).where(LLMCall.provider == "fake"))  # accounting rows from the fake provider
    # raw documents and snapshot files written to the local object store
    import shutil

    from knowledge_platform.config import get_settings

    root = get_settings().local_store_path
    for d in (root / DOMAIN_ID, root / "snapshots" / DOMAIN_ID):
        shutil.rmtree(d, ignore_errors=True)


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
        # idempotency: crawling again with unchanged content creates nothing ...
        src = s.execute(select(Source).where(Source.domain_id == DOMAIN_ID)).scalar_one()
        verified_before = top.last_verified_at
        checked_before = top.last_source_checked_at
        level_before = top.verification_level
        stats2 = crawl_source(s, src, fetcher=Fetcher(default_delay=0, max_retries=1))
        assert stats2.new == 0 and stats2.changed == 0 and stats2.unchanged >= 1
        # ... but confirms that the source still states the claims (audit P1.4): freshness only, no new verification
        assert stats2.confirmed >= 1
        s.refresh(top)
        assert top.last_source_checked_at is not None and top.last_source_checked_at != checked_before
        assert top.last_verified_at == verified_before and top.verification_level == level_before
        assert top.last_content_changed_at is None and top.quality_factors["freshness"] == 1.0
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
        assert changed.version == 2 and changed.content_changed_at is not None
        assert changed.chunk_hashes and all(h["sha256"] for h in changed.chunk_hashes)
        result = ingest_document(s, changed, plugin)
        assert result.conflicts >= 1 or result.items_stale >= 1
        # items whose quote survived the change are stamped as "content changed, still stated"; stale ones are not
        survivors = [
            k
            for k in s.execute(select(KnowledgeItem).where(KnowledgeItem.domain_id == DOMAIN_ID)).scalars()
            if k.first_discovered_at < changed.content_changed_at
            and any(e.document_id == changed.id and e.verified for e in k.evidence if e.evidence_type == "extraction")
        ]
        assert all(k.last_content_changed_at is not None for k in survivors)
        assert all(
            k.last_content_changed_at is None
            for k in s.execute(select(KnowledgeItem).where(KnowledgeItem.domain_id == DOMAIN_ID)).scalars()
            if k.status == ItemStatus.STALE
        )
        # section-level delta: the chunk hashes were re-recorded for version 2
        assert result.extraction["chunks"] == len(changed.chunk_hashes)
        # re-ingesting the *same* version sends nothing to the model: every chunk hash is unchanged
        again = ingest_document(s, changed, plugin)
        assert again.extraction["chunks_unchanged"] == again.extraction["chunks"] - again.extraction["chunks_skipped"]
        assert again.extraction["raw_items"] == 0

    with session_scope() as s:
        conflicts = s.execute(select(Conflict).where(Conflict.domain_id == DOMAIN_ID)).scalars().all()
        statuses = {
            i.status for i in s.execute(select(KnowledgeItem).where(KnowledgeItem.domain_id == DOMAIN_ID)).scalars()
        }
        assert conflicts or ItemStatus.STALE in statuses


@respx.mock
def test_mirror_source_is_not_an_independent_confirmation(plugin, fake_providers):
    """The same page served by a second source is linked as a mirror and does not raise the source count."""
    _mock_site(2)
    page = (FIXTURES / "page_v2.html").read_text()
    respx.get("https://mirror.test/robots.txt").mock(return_value=httpx.Response(200, text="User-agent: *\nAllow: /\n"))
    respx.get("https://mirror.test/docs/").mock(
        return_value=httpx.Response(200, text=page, headers={"Content-Type": "text/html"})
    )
    with session_scope() as s:
        mirror = Source(
            domain_id=DOMAIN_ID,
            key="mirror",
            origin="user",
            name="Mirror site",
            url="https://mirror.test/docs/",
            publisher="mirror",
            source_type="docs",
            authority=80,
            access_type="public",
            license="unknown",
            permissions={},
            crawl_frequency_hours=24,
            max_depth=0,
            max_pages=5,
            status="ACTIVE",
            enabled=True,
        )
        s.add(mirror)
        s.flush()
        stats = crawl_source(s, mirror, fetcher=Fetcher(default_delay=0, max_retries=1))
        assert stats.new == 1
        doc = s.execute(select(Document).where(Document.source_id == mirror.id)).scalar_one()
        original = s.execute(
            select(Document).where(Document.domain_id == DOMAIN_ID, Document.url.like("%/calculate"))
        ).scalar_one()
        assert doc.canonical_document_id == original.id and doc.meta["mirror_of"] == original.url
        # extraction from the mirror attaches evidence, but the item still has one *independent* source
        result = ingest_document(s, doc, plugin)
        assert result.items_merged + result.items_near_duplicate + result.items_created >= 1
        s.expire_all()
        item = (
            s.execute(
                select(KnowledgeItem).where(
                    KnowledgeItem.id.in_(select(Evidence.knowledge_item_id).where(Evidence.document_id == doc.id)),
                    KnowledgeItem.status != ItemStatus.REJECTED,
                )
            )
            .scalars()
            .first()
        )
        assert item is not None
        sources = {e.source_id for e in item.evidence if e.evidence_type == "extraction" and e.verified}
        assert len(sources) == 2
        assert item.quality_factors["source_agreement"] == 1 / 3  # one independent source, not two
        # leave the fixture domain as it was for the snapshot tests that follow
        s.execute(delete(Evidence).where(Evidence.document_id == doc.id))
        s.delete(mirror)


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
        assert m["schema_version"] == SCHEMA_VERSION and m["counts"]["knowledge"] >= 1 and m["gate"]["provenance_ok"]
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
            "ai/index.json",
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
        assert ai_first["usage"] in ("cite", "caution") and "Sources:\n[1] " in ai_first["text"]
        index = json.loads(read_file(a, "ai/index.json").decode())
        assert ai_first["id"] in index["topics"][ai_first["topic"] or "(unclassified)"]["ids"]
        assert "CALCULATE" in read_file(a, "ai/knowledge.md").decode()
        # conflicts from the earlier test are exported, with both sides retained
        conflicts = json.loads(read_file(a, "conflicts.json").decode())
        assert conflicts and conflicts[0]["status"] == "OPEN"
        # the contract ships with the data and the stored files satisfy it (audit P1.1)
        assert m["export_schema_version"] == SCHEMA_VERSION and m["database_schema_version"]
        assert {"schema/knowledge.schema.json", "schema/ai_knowledge.schema.json", "schema/vocabulary.json"} <= set(
            m["files"]
        )
        assert m["gate"]["schema_files_ok"] is True
        shipped = json.loads(read_file(a, "schema/knowledge.schema.json").decode())
        assert shipped["x-export-schema-version"] == SCHEMA_VERSION and shipped["x-snapshot-file"] == "knowledge.jsonl"
        # integrity verification (hashes + schema) and zip packaging
        v = verify_snapshot(a)
        assert v["ok"] and v["schema_checked"] and v["schema_problems"] == [], v
        # a stored record that no longer satisfies the shipped schema is reported, and the hash mismatch too
        from knowledge_platform.adapters import get_object_store

        store = get_object_store()
        key = f"{a.object_prefix}/ai/knowledge.jsonl"
        original = store.get(key)
        assert b'"usage":"' in original
        store.put(key, original.replace(b'"usage":"', b'"usage":"trust-', 1), "application/json")
        broken = verify_snapshot(a)
        assert not broken["ok"] and "ai/knowledge.jsonl" in broken["mismatched"]
        assert any("usage" in p for p in broken["schema_problems"])
        store.put(key, original, "application/json")
        assert verify_snapshot(a)["ok"]
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
        # an evidence *modification*: a verified quote is no longer found (the page changed). A reviewer-provided
        # evidence keeps the item exportable, so the delta must show the flip rather than a removal
        live = next(
            k
            for k in s.execute(
                select(KnowledgeItem).where(
                    KnowledgeItem.domain_id == plugin.id,
                    KnowledgeItem.status.in_([ItemStatus.SUPPORTED, ItemStatus.VERIFIED, ItemStatus.CONFLICTED]),
                )
            ).scalars()
            if any(e.evidence_type == "extraction" and e.verified for e in k.evidence)
        )
        live.evidence.append(
            Evidence(
                knowledge_item_id=live.id,
                evidence_type="human",
                excerpt="confirmed by reviewer",
                verified=True,
                details={"provided": True, "provided_by": "qa", "authority": 80},
            )
        )
        flipped = next(e for e in live.evidence if e.evidence_type == "extraction" and e.verified)
        flipped.verified = False
        flipped_id = str(flipped.id)

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
        # evidence modification (audit P0.4): counted, explained field by field, and shipped as the head record
        assert flipped_id in d["evidence"]["modified"] and m["counts"]["evidence_modified"] >= 1
        change = d["evidence"]["changes"][flipped_id]
        assert change["changed_fields"] == ["verified"]
        assert change["before"] == {"verified": True} and change["after"] == {"verified": False}
        ev_delta = {
            json.loads(line)["id"]: json.loads(line)
            for line in read_file(delta, "evidence.jsonl").decode().splitlines()
            if line
        }
        assert ev_delta[flipped_id]["verified"] is False
        # applying the delta to the base reproduces the head evidence file exactly
        head = s.get(type(delta), uuid.UUID(m["head_snapshot_id"]))
        base = s.get(type(delta), base_id)
        ev_base = {
            json.loads(line)["id"]: json.loads(line)
            for line in read_file(base, "evidence.jsonl").decode().splitlines()
            if line
        }
        ev_head = {
            json.loads(line)["id"]: json.loads(line)
            for line in read_file(head, "evidence.jsonl").decode().splitlines()
            if line
        }
        applied = {k: v for k, v in ev_base.items() if k not in set(d["evidence"]["removed"])}
        applied.update(ev_delta)
        assert applied == ev_head
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
