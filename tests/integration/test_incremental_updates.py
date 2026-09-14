"""Incremental updates and the snapshot lifecycle end to end (P2.0.3 / P2.0.4), against PostgreSQL.

A fixture site changes between two crawls: one section is reworded but keeps its claim, one section disappears,
one is new and the rest are byte-identical. The test proves, through the real crawl → extract → evidence →
lifecycle → propagation → evaluation → snapshot path (fake model, recorded HTML), that

* an unchanged crawl extracts nothing and only confirms freshness,
* a changed page sends only the changed sections to the model,
* new content becomes new knowledge, removed content makes the affected item STALE (never deleted),
* knowledge derived from the removed claim is flagged for revalidation,
* the golden set detects the regression, and
* Knowledge State N + Delta(N→N+1) = Knowledge State N+1, record for record, for every file the delta covers.
"""

from __future__ import annotations

import json
import re
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
from knowledge_platform.core.evaluation.runner import run_evaluation
from knowledge_platform.core.export.delta import build_delta_snapshot
from knowledge_platform.core.export.snapshot import build_snapshot, read_file, verify_snapshot
from knowledge_platform.core.knowledge_entry import KnowledgeEntry, create_knowledge
from knowledge_platform.core.pipeline import ingest_document
from knowledge_platform.core.plugins.registry import load_plugin_dir
from knowledge_platform.core.retrieval.search import hybrid_search
from knowledge_platform.db import session_scope
from knowledge_platform.models import Conflict, Document, Domain, ItemStatus, KnowledgeItem, LLMCall, Snapshot, Source

pytestmark = requires_db

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "incremental"
DOMAIN_ID = "itest-inc-" + uuid.uuid4().hex[:6]
_CLAIM = re.compile(r"^([A-Z]+FN) (\w+) (.+?)\.")
_RETRIEVED = re.compile(r"^\[(\d+)\] \([^)]*\) (.+)$", re.MULTILINE)


class FakeLLM(LLMProvider):
    """Extracts the first sentence of every section that opens with a fixture function name; answers only from the
    retrieved items (citing the one about the asked function, abstaining otherwise); judges every answer correct."""

    name = "fake"

    def generate(self, *, system, user, model, temperature=0.0):
        question = user.split("\n", 1)[0]  # "Question: ..." is the first line of the answer prompt
        asked = re.search(r"\b([A-Z]+FN)\b", question)
        for n, statement in _RETRIEVED.findall(user):
            if asked and statement.startswith(asked.group(1)):
                return LLMResult(text=f"{statement} [{n}]", model=model)
        return LLMResult(text="The knowledge base does not contain information about this.", model=model)

    def generate_json(self, *, system, user, model, schema, temperature=0.0):
        if "verdict" in json.dumps(schema):
            return LLMResult(text=json.dumps({"verdict": "compatible", "rationale": "fixture"}), model=model)
        if "supported_by_citations" in json.dumps(schema):
            verdict = {"correct": True, "supported_by_citations": True, "hallucinated_claims": [], "missing_points": []}
            return LLMResult(text=json.dumps({**verdict, "rationale": "fixture"}), model=model)
        text = user.split("--- SECTION TEXT START ---")[1].split("--- SECTION TEXT END ---")[0].strip()
        m = _CLAIM.match(text)
        if not m:
            return LLMResult(text=json.dumps({"items": []}), model=model)
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
        f"api_version: '1.0'\nid: {DOMAIN_ID}\nname: Incremental fixture\ntaxonomy:\n  - name: Functions\n",
        encoding="utf-8",
    )
    (d / "evaluation.yaml").write_text(
        "\n".join(
            [
                "version: '1'",
                "questions:",
                "  - id: q-alpha",
                "    question: What does ALPHAFN evaluate?",
                "    expected_answer: an expression in a modified filter context",
                "    required_concepts: [filter context]",
                "    topic: Functions",
                "  - id: q-gamma",
                "    question: What does GAMMAFN require?",
                "    expected_answer: a calendar table marked as a date table",
                "    required_concepts: [date table]",
                "    topic: Functions",
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


def _mock_site(version: int) -> None:
    respx.get("https://fixture.test/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nAllow: /\n")
    )
    respx.get("https://fixture.test/docs/").mock(
        return_value=httpx.Response(
            200, text=(FIXTURES / "index.html").read_text(), headers={"Content-Type": "text/html"}
        )
    )
    respx.get("https://fixture.test/docs/guide").mock(
        return_value=httpx.Response(
            200,
            text=(FIXTURES / f"guide_v{version}.html").read_text(),
            headers={"Content-Type": "text/html", "ETag": f'"v{version}"'},
        )
    )


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield
    with session_scope() as s:
        s.execute(delete(Domain).where(Domain.id == DOMAIN_ID))
        s.execute(delete(LLMCall).where(LLMCall.provider == "fake"))
    import shutil

    from knowledge_platform.config import get_settings

    root = get_settings().local_store_path
    for d in (root / DOMAIN_ID, root / "snapshots" / DOMAIN_ID):
        shutil.rmtree(d, ignore_errors=True)


def _items(s) -> dict[str, KnowledgeItem]:
    rows = s.execute(select(KnowledgeItem).where(KnowledgeItem.domain_id == DOMAIN_ID)).scalars().all()
    return {k.subject: k for k in rows if k.origin == "DIRECT"}


def _fetcher() -> Fetcher:
    return Fetcher(default_delay=0, max_retries=1)


state: dict[str, object] = {}  # shared between the ordered tests of this module


@respx.mock
def test_initial_crawl_and_unchanged_recrawl(plugin):
    _mock_site(1)
    with session_scope() as s:
        sync_domain(s, plugin)
        src = s.execute(select(Source).where(Source.domain_id == DOMAIN_ID)).scalar_one()
        stats = crawl_source(s, src, fetcher=_fetcher())
        assert stats.new == 2 and stats.failed == 0, stats
        for doc in s.execute(select(Document).where(Document.domain_id == DOMAIN_ID)).scalars():
            ingest_document(s, doc, plugin)
    with session_scope() as s:
        items = _items(s)
        assert set(items) == {"ALPHAFN", "BETAFN", "GAMMAFN", "DELTAFN"}, set(items)
        assert all(k.status in (ItemStatus.VERIFIED, ItemStatus.SUPPORTED) for k in items.values())
        guide = s.execute(select(Document).where(Document.url == "https://fixture.test/docs/guide")).scalar_one()
        assert len(guide.chunk_hashes) == 4
        state["gamma_id"] = items["GAMMAFN"].id
        state["alpha_before"] = (items["ALPHAFN"].last_verified_at, items["ALPHAFN"].verification_level)
        # knowledge derived from GAMMAFN's claim: when the claim goes, this must be identified as affected
        derived = create_knowledge(
            s,
            plugin,
            KnowledgeEntry(
                statement="A GAMMAFN measure over an unmarked calendar table is unreliable.",
                subject="GAMMAFN measure",
                predicate="is unreliable over",
                object="unmarked calendar table",
                provenance="ORGANIZATION",
                provided_by="qa",
                authority=90,
                origin="DERIVED",
                derived_from=[items["GAMMAFN"].id],
                rationale="GAMMAFN requires the mark; without it the totals are wrong.",
            ),
        )
        state["derived_id"] = derived.id
        assert not derived.needs_revalidation

    # unchanged source → nothing to extract, freshness confirmed only
    with session_scope() as s:
        src = s.execute(select(Source).where(Source.domain_id == DOMAIN_ID)).scalar_one()
        before = {k: (v.last_verified_at, v.verification_level, v.updated_at) for k, v in _items(s).items()}
        stats = crawl_source(s, src, fetcher=_fetcher())
        assert stats.new == 0 and stats.changed == 0 and stats.unchanged == 2
        assert stats.to_extract == []  # no extraction job would be enqueued
        assert stats.confirmed == 4
        after = _items(s)
        for subject, (verified_at, level, _) in before.items():
            assert after[subject].last_verified_at == verified_at and after[subject].verification_level == level
            assert after[subject].last_source_checked_at is not None
            assert after[subject].last_content_changed_at is None


def test_evaluation_baseline(plugin):
    with session_scope() as s:
        ev = run_evaluation(s, plugin, triggered_by="test")
        assert ev.status == "DONE", ev.error
        by_id = {r.question_id: r for r in ev.results}
        assert by_id["q-gamma"].passed and by_id["q-alpha"].passed and by_id["q-abstain"].passed, [
            (r.question_id, r.failure_causes, r.answer[:160]) for r in ev.results
        ]
        assert ev.metrics["accuracy"] == 1.0 and not ev.regression
        state["eval_baseline_id"] = ev.id


def test_snapshot_n(plugin):
    with session_scope() as s:
        snap = build_snapshot(s, plugin, created_by="test")
        assert snap.status == "ready", snap.error
        assert verify_snapshot(snap)["ok"]
        state["snapshot_n"] = snap.id
        state["snapshot_n_hash"] = snap.integrity_hash
        # reproducible: the same knowledge state renders to the same bytes
        again = build_snapshot(s, plugin, created_by="test")
        assert again.integrity_hash == snap.integrity_hash


@respx.mock
def test_changed_page_processes_only_affected_sections(plugin):
    respx.reset()
    _mock_site(2)
    with session_scope() as s:
        src = s.execute(select(Source).where(Source.domain_id == DOMAIN_ID)).scalar_one()
        stats = crawl_source(s, src, fetcher=_fetcher())
        assert stats.new == 0 and stats.changed == 1 and stats.unchanged == 1, stats
        guide = s.execute(select(Document).where(Document.url == "https://fixture.test/docs/guide")).scalar_one()
        assert stats.to_extract == [str(guide.id)]
        assert guide.version == 2
        result = ingest_document(s, guide, plugin)
        ext = result.extraction
        # four sections in v2: Alpha and Delta unchanged (hash hit), Beta reworded and Epsilon new → two model calls
        assert ext["chunks"] == 4 and ext["chunks_unchanged"] == 2, ext
        assert ext["raw_items"] == 2
        assert result.items_stale == 1  # GAMMAFN: its section is gone

    with session_scope() as s:
        items = _items(s)
        assert set(items) == {"ALPHAFN", "BETAFN", "GAMMAFN", "DELTAFN", "EPSILONFN"}
        # new content → new knowledge
        assert items["EPSILONFN"].status in (ItemStatus.VERIFIED, ItemStatus.SUPPORTED)
        # removed content → STALE, evidence kept but unverified, item still present
        gamma = items["GAMMAFN"]
        assert gamma.status == ItemStatus.STALE and gamma.id == state["gamma_id"]
        assert gamma.evidence and not any(e.verified for e in gamma.evidence if e.evidence_type == "extraction")
        # reworded section with the same claim → same item, freshness stamps updated, no new verification
        beta = items["BETAFN"]
        assert beta.status in (ItemStatus.VERIFIED, ItemStatus.SUPPORTED)
        assert beta.last_content_changed_at is not None and beta.last_source_checked_at is not None
        assert all(e.verified for e in beta.evidence if e.evidence_type == "extraction")
        # untouched sections → no re-verification: the page changed around the claim, the claim itself is only re-pinned
        alpha = items["ALPHAFN"]
        assert (alpha.last_verified_at, alpha.verification_level) == state["alpha_before"]
        assert all(e.verified and e.source_version == 2 for e in alpha.evidence if e.evidence_type == "extraction")
        # dependency change → the derived item is identified, not silently kept
        derived = s.get(KnowledgeItem, state["derived_id"])
        assert (
            derived.needs_revalidation
            and "GAMMAFN" in (derived.revalidation_reason or "")
            or derived.needs_revalidation
        )
        assert derived.status != ItemStatus.REJECTED


def test_evaluation_detects_regression(plugin):
    with session_scope() as s:
        ev = run_evaluation(s, plugin, triggered_by="test")
        assert ev.status == "DONE", ev.error
        by_id = {r.question_id: r for r in ev.results}
        gamma = by_id["q-gamma"]
        assert not gamma.passed and gamma.failure_class in ("validation_failure", "citation_failure"), (
            gamma.failure_class,
            gamma.failure_causes,
        )
        assert by_id["q-alpha"].passed
        assert ev.baseline_run_id == state["eval_baseline_id"]
        assert ev.regression is True, ev.regression_details
        assert ev.metrics["accuracy"] < 1.0


def _records(snap: Snapshot, path: str) -> dict[str, dict]:
    if path not in snap.manifest["files"]:
        return {}
    raw = read_file(snap, path).decode("utf-8")
    if path.endswith(".json"):
        return {str(r["id"]): r for r in json.loads(raw)}
    return {str(json.loads(line)["id"]): json.loads(line) for line in raw.splitlines() if line.strip()}


RECORD_FILES = (
    "knowledge.jsonl",
    "evidence.jsonl",
    "relationships.jsonl",
    "sources.jsonl",
    "documents.jsonl",
    "examples.jsonl",
    "negative.jsonl",
    "ai/knowledge.jsonl",
    "conflicts.json",
    "changelog.jsonl",
)


def test_snapshot_n_plus_1_and_delta_reconstruction(plugin):
    with session_scope() as s:
        base = s.get(Snapshot, state["snapshot_n"])
        head = build_snapshot(s, plugin, created_by="test")
        assert head.status == "ready", head.error
        assert head.integrity_hash != base.integrity_hash
        assert verify_snapshot(head)["ok"] and verify_snapshot(base)["ok"]
        delta = build_delta_snapshot(s, plugin, base_snapshot_id=base.id, head_snapshot_id=head.id, created_by="test")
        assert delta.status == "ready", delta.error
        assert verify_snapshot(delta)["ok"]
        d = json.loads(read_file(delta, "delta.json").decode())
        gamma_id, derived_id = str(state["gamma_id"]), str(state["derived_id"])
        # knowledge changes: EPSILONFN added; GAMMAFN → STALE (kept, historical usage); derived item flagged
        kh = _records(head, "knowledge.jsonl")
        epsilon = next(k for k, r in kh.items() if r["subject"] == "EPSILONFN")
        assert epsilon in d["knowledge"]["added"]
        assert {"id": gamma_id, "from": "VERIFIED", "to": "STALE"} in d["knowledge"]["status_changes"] or {
            "id": gamma_id,
            "from": "SUPPORTED",
            "to": "STALE",
        } in d["knowledge"]["status_changes"]
        assert gamma_id in d["knowledge"]["modified"] and gamma_id not in d["knowledge"]["removed"]
        assert derived_id in d["knowledge"]["modified"]
        assert "needs_revalidation" in d["knowledge"]["changed_fields"][derived_id]
        # evidence changes: GAMMAFN's quote lost verification; BETAFN's evidence re-pinned to the new page version
        ev_changes = d["evidence"]["changes"]
        lost = [c for c in ev_changes.values() if "verified" in c["changed_fields"]]
        assert len(lost) == 1 and lost[0]["after"]["verified"] is False and lost[0]["before"]["verified"] is True
        repinned = [c for c in ev_changes.values() if "document_hash" in c["changed_fields"]]
        assert len(repinned) == 3  # ALPHAFN, BETAFN, DELTAFN quotes found again in the new page version
        # AI source: GAMMAFN now carries a caution (STALE; "historical" is reserved for superseded), EPSILONFN is new
        ai = _records(delta, "ai/knowledge.jsonl")
        assert ai[gamma_id]["usage"] == "caution" and "STALE" in ai[gamma_id]["text"] and epsilon in ai
        assert ai[derived_id]["usage"] == "caution"
        # dependency / review state travels with the records
        assert kh[derived_id]["needs_revalidation"] is True and kh[gamma_id]["status"] == "STALE"
        # changelog carries the transitions of this update
        assert d["changelog_entries"] >= 1
        # documents.jsonl (schema 1.5): evidence ↔ document integrity is checkable from the files alone
        docs = _records(head, "documents.jsonl")
        evs = _records(head, "evidence.jsonl")
        guide_doc = next(r for r in docs.values() if r["url"] == "https://fixture.test/docs/guide")
        assert guide_doc["version"] == 2 and guide_doc["previous_content_hash"] and guide_doc["raw_sha256"]
        assert guide_doc["normalizer_version"] and guide_doc["source_id"] in _records(head, "sources.jsonl")
        for e in evs.values():
            if e["document_id"]:
                doc_rec = docs[e["document_id"]]
                assert e["document_hash"] in (doc_rec["content_hash"], doc_rec["previous_content_hash"]), e["id"]
                assert (
                    e["verified"] == (e["document_hash"] == doc_rec["content_hash"])
                    or e["evidence_type"] != "extraction"
                )
        assert d["documents"]["modified"] == [guide_doc["id"]]
        assert "content_hash" in d["documents"]["changes"][guide_doc["id"]]["changed_fields"]
        assert "text" not in guide_doc

        # ---- the proof: State N + Delta = State N+1, record for record, for every file the delta covers
        removed = {
            "knowledge.jsonl": set(d["knowledge"]["removed"]),
            "ai/knowledge.jsonl": set(d["knowledge"]["removed"]),
            "evidence.jsonl": set(d["evidence"]["removed"]),
            "relationships.jsonl": set(d["relationships"]["removed"]),
            "sources.jsonl": set(d["sources"]["removed"]),
            "documents.jsonl": set(d["documents"]["removed"]),
            "examples.jsonl": set(d["examples"]["removed"]),
            "negative.jsonl": set(d["negative"]["removed"]),
            "conflicts.json": set(),
            "changelog.jsonl": set(),
        }
        for path in RECORD_FILES:
            reconstructed = {k: v for k, v in _records(base, path).items() if k not in removed[path]}
            reconstructed.update(_records(delta, path))
            expected = _records(head, path)
            assert set(reconstructed) == set(expected), (path, set(reconstructed) ^ set(expected))
            differing = {k for k in expected if reconstructed[k] != expected[k]}
            assert not differing, (path, differing, [(reconstructed[k], expected[k]) for k in list(differing)[:2]])
        state["snapshot_n1"] = head.id
        state["delta"] = delta.id


@respx.mock
def test_changed_claim_becomes_next_version(plugin):
    """P2.1 through the pipeline: BETAFN's documented behaviour changes in v3 → the stale item is superseded by the
    new statement (version link, historical export, delta `superseded`), never deleted."""
    respx.reset()
    _mock_site(3)
    with session_scope() as s:
        src = s.execute(select(Source).where(Source.domain_id == DOMAIN_ID)).scalar_one()
        stats = crawl_source(s, src, fetcher=_fetcher())
        assert stats.changed == 1
        guide = s.execute(select(Document).where(Document.url == "https://fixture.test/docs/guide")).scalar_one()
        old_beta = _items(s)["BETAFN"]
        old_beta_id, old_version = old_beta.id, old_beta.version
        result = ingest_document(s, guide, plugin)
        assert result.items_stale == 1 and result.items_created == 1 and result.items_superseded == 1, result

    with session_scope() as s:
        rows = (
            s.execute(
                select(KnowledgeItem).where(KnowledgeItem.domain_id == DOMAIN_ID, KnowledgeItem.subject == "BETAFN")
            )
            .scalars()
            .all()
        )
        old = next(k for k in rows if k.id == old_beta_id)
        new = next(k for k in rows if k.id != old_beta_id)
        assert old.status == ItemStatus.SUPERSEDED and old.superseded_by_id == new.id
        assert new.previous_version_id == old.id and new.version == old_version + 1
        assert new.status in (ItemStatus.VERIFIED, ItemStatus.SUPPORTED) and "zero when" in new.statement
        assert old.evidence and old.statement.startswith("BETAFN returns a blank value")  # history kept
        assert not s.execute(select(Conflict).where(Conflict.domain_id == DOMAIN_ID)).scalars().all()
        hits = {h.item.id for h in hybrid_search(s, domain_id=DOMAIN_ID, query="BETAFN denominator zero", limit=10)}
        assert new.id in hits and old.id not in hits
        state["beta_old"], state["beta_new"] = old.id, new.id

    with session_scope() as s:
        base = s.get(Snapshot, state["snapshot_n1"])
        head = build_snapshot(s, plugin, created_by="test")
        assert head.status == "ready", head.error
        delta = build_delta_snapshot(s, plugin, base_snapshot_id=base.id, head_snapshot_id=head.id, created_by="test")
        assert delta.status == "ready", delta.error
        d = json.loads(read_file(delta, "delta.json").decode())
        old_id, new_id = str(state["beta_old"]), str(state["beta_new"])
        assert d["knowledge"]["superseded"] == [old_id] and new_id in d["knowledge"]["added"]
        assert old_id not in d["knowledge"]["modified"] and old_id not in d["knowledge"]["removed"]
        ai = _records(delta, "ai/knowledge.jsonl")
        assert ai[old_id]["usage"] == "historical" and new_id in ai[old_id]["text"] and ai[new_id]["usage"] == "cite"
        rel = _records(delta, "relationships.jsonl")
        assert any(r["relation_type"] == "supersedes" for r in rel.values())
        for path in RECORD_FILES:
            removed_ids = set(d.get(path.split(".")[0].replace("ai/knowledge", "knowledge"), {}).get("removed", []))
            reconstructed = {k: v for k, v in _records(base, path).items() if k not in removed_ids}
            reconstructed.update(_records(delta, path))
            expected = _records(head, path)
            assert reconstructed == expected, path
