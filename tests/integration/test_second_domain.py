"""Plugin contract through the real pipeline with a synthetic second domain (P2.10).

The fixture plugin ``roboticslab`` declares its own knowledge types (spec / safety_rule / hazard / demo / note),
polarity, roles, source classes, sample questions, discovery settings and validators. No real robotics knowledge
is involved — the pages are invented — and the fake model only maps a page's ``SPEC:`` / ``RULE:`` / ``HAZARD:`` /
``DEMO:`` sentences to those types. The assertions are about the *core*: it must derive polarity, dependencies,
provenance, prompts, negative knowledge, evaluation and export from the plugin's declarations alone.
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
from knowledge_platform.core.export.snapshot import build_snapshot, read_file
from knowledge_platform.core.pipeline import ingest_document
from knowledge_platform.core.plugins.registry import load_plugin_dir
from knowledge_platform.core.retrieval.answer import answer_question
from knowledge_platform.core.retrieval.plan import build_plan, check_completeness
from knowledge_platform.core.retrieval.retrieve import RETRIEVAL_VERSION, retrieve
from knowledge_platform.core.retrieval.search import resolve_text_search_config
from knowledge_platform.core.versioning.dependencies import dependencies_of
from knowledge_platform.db import session_scope
from knowledge_platform.models import Document, Domain, ItemStatus, KnowledgeItem, LLMCall, Source

pytestmark = requires_db

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PLUGIN_DIR = FIXTURES / "plugins" / "roboticslab"
PAGES = FIXTURES / "roboticslab"
DOMAIN_ID = "roboticslab"
_TYPES = {"SPEC": "spec", "RULE": "safety_rule", "HAZARD": "hazard", "DEMO": "demo"}
_CLAIM = re.compile(r"^(SPEC|RULE|HAZARD|DEMO): (ARM-7) (\w+(?: \w+)?) (.+?)\.")
_RETRIEVED = re.compile(r"^\[(\d+)\] \([^)]*\) (?:[A-Z-]+: )?(.+)$", re.MULTILINE)


class FakeLLM(LLMProvider):
    name = "fake"

    def generate(self, *, system, user, model, temperature=0.0):
        question = user.split("\n", 1)[0].lower()
        for n, statement in _RETRIEVED.findall(user):
            low = statement.lower()
            if ("payload" in question and "payload is" in low) or ("full speed" in question and "crush" in low):
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
        tag, subject, predicate, obj = m.groups()
        first = text[len(tag) + 2 : m.end()]
        item = {
            "knowledge_type": _TYPES[tag],
            "subject": subject,
            "predicate": predicate,
            "object": obj,
            "statement": first,
            "explanation": "",
            "topic": "Safety/Hazards" if tag in ("HAZARD", "RULE") else "Arm/Payload",
            "tags": [],
            "code": "home(); pick(A); place(B)" if tag == "DEMO" else None,
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
def plugin():
    return load_plugin_dir(PLUGIN_DIR)


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


def _page(name: str) -> httpx.Response:
    return httpx.Response(200, text=(PAGES / name).read_text(encoding="utf-8"), headers={"Content-Type": "text/html"})


def _mock_site(spec_version: int) -> None:
    respx.get("https://docs.roboticslab.test/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://docs.roboticslab.test/manual/").mock(return_value=_page("index.html"))
    respx.get("https://docs.roboticslab.test/manual/spec").mock(return_value=_page(f"spec_v{spec_version}.html"))
    respx.get("https://docs.roboticslab.test/manual/safety").mock(return_value=_page("safety.html"))


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
    return {k.knowledge_type + ":" + k.statement.split(" ")[1]: k for k in rows}


state: dict[str, uuid.UUID] = {}


def test_plugin_declarations_load_without_core_assumptions(plugin):
    assert plugin.role_of("spec") == "foundation" and plugin.role_of("hazard") == "dependent"
    assert plugin.polarity_of("hazard") == "negative" and plugin.polarity_of("spec") == "positive"
    assert tuple(plugin.types_with_role("example")) == ("demo",) and plugin.role_of("note") == "neutral"
    assert plugin.text_search_config() == "english" and plugin.discovery().prefer_hosts == ["docs.roboticslab.test"]
    summary = plugin.summary()
    assert summary["sample_questions"][0].startswith("What is the payload") and summary["validators"] == [
        "units",
        "simulate-motion",
    ]
    assert [s.source_class for s in plugin.sources()] == ["organization"]


@respx.mock
def test_pipeline_uses_the_plugin_semantics(plugin):
    _mock_site(1)
    with session_scope() as s:
        sync_domain(s, plugin)
        src = s.execute(select(Source).where(Source.domain_id == DOMAIN_ID)).scalar_one()
        assert src.source_class == "organization"
        stats = crawl_source(s, src, fetcher=Fetcher(default_delay=0, max_retries=1))
        assert stats.new == 3 and stats.failed == 0, stats
        for doc in s.execute(select(Document).where(Document.domain_id == DOMAIN_ID)).scalars():
            ingest_document(s, doc, plugin)
    with session_scope() as s:
        items = _items(s)
        assert set(items) == {"spec:payload", "spec:full", "demo:pick", "safety_rule:must", "hazard:at"}, set(items)
        payload, rule, hazard, demo = (
            items["spec:payload"],
            items["safety_rule:must"],
            items["hazard:at"],
            items["demo:pick"],
        )
        # polarity and provenance come from the plugin's declarations, not from a core vocabulary
        assert hazard.polarity == "negative" and payload.polarity == "positive"
        assert {k.provenance for k in items.values()} == {"ORGANIZATION"}
        assert all(k.status in (ItemStatus.VERIFIED, ItemStatus.SUPPORTED) for k in items.values())
        # dependencies follow the declared roles: dependents depend on foundations about the same subject,
        # examples are example_of them
        rule_deps = {(r.relation_type, k.knowledge_type) for r, k in dependencies_of(s, rule.id)}
        assert ("depends_on", "spec") in rule_deps
        demo_deps = {(r.relation_type, k.knowledge_type) for r, k in dependencies_of(s, demo.id)}
        assert ("example_of", "spec") in demo_deps
        # validators: the static one ran on the payload spec; the executing one was refused and recorded as skipped
        assert payload.validator_versions["units"] == "1.0"
        assert any(e.evidence_type == "validator" and e.details["passed"] for e in payload.evidence)
        assert demo.validator_versions["simulate-motion"]["skipped"]
        assert not any(e.evidence_type == "validator" for e in demo.evidence)
        state["payload"], state["rule"], state["hazard"] = payload.id, rule.id, hazard.id
        # the answer prompt labels the hazard with the plugin's prefix, and examples with EXAMPLE
        prompts: list[str] = []
        import knowledge_platform.core.retrieval.answer as answer_mod

        original = answer_mod.call_text
        answer_mod.call_text = lambda **kw: prompts.append(kw["user"]) or original(**kw)
        try:
            ans = answer_question(s, plugin, "Which hazard applies when ARM-7 moves at full speed?", limit=8)
        finally:
            answer_mod.call_text = original
        assert "HAZARD: ARM-7 at full speed can crush" in prompts[-1]
        assert ans.citations and "crush" in ans.answer


def test_evaluation_and_export_follow_the_plugin(plugin):
    with session_scope() as s:
        ev = run_evaluation(s, plugin, triggered_by="test")
        assert ev.status == "DONE", ev.error
        by_id = {r.question_id: r for r in ev.results}
        assert by_id["payload"].passed and by_id["abstain"].passed, [
            (r.question_id, r.failure_causes) for r in ev.results
        ]
        assert by_id["hazard-speed"].passed and by_id["hazard-speed"].checks["negative_knowledge"]["ok"]
        assert ev.metrics["negative_coverage"] == 1.0
        snap = build_snapshot(s, plugin, created_by="test")
        assert snap.status == "ready", snap.error
        glossary = json.loads(read_file(snap, "glossary.json").decode())
        specs = {t["name"]: t for t in glossary["knowledge_type_specs"]}
        assert specs["hazard"]["polarity"] == "negative" and specs["demo"]["role"] == "example"
        negative = [json.loads(line) for line in read_file(snap, "negative.jsonl").decode().splitlines() if line]
        assert [n["knowledge_type"] for n in negative] == ["hazard"]
        ai = {
            json.loads(line)["id"]: json.loads(line)
            for line in read_file(snap, "ai/knowledge.jsonl").decode().splitlines()
            if line
        }
        assert ai[str(state["hazard"])]["text"].startswith("Hazard: ARM-7 at full speed")
        assert (
            ai[str(state["payload"])]["provenance"] == "ORGANIZATION" and ai[str(state["payload"])]["usage"] == "cite"
        )
        index = json.loads(read_file(snap, "ai/index.json").decode())
        assert set(index["types"]) == {"spec", "safety_rule", "hazard", "demo"}
        examples = [json.loads(line) for line in read_file(snap, "examples.jsonl").decode().splitlines() if line]
        assert len(examples) == 1 and examples[0]["subject"] == "ARM-7"
        assert snap.manifest["generation"]["validators"] == ["simulate-motion@0.1", "units@1.0"]


def test_retrieval_pipeline_works_from_the_plugin_declarations_alone(plugin):
    """ADR 0006 in a second domain: entities, intent, synonyms, expansion and the answer plan come from the
    corpus and the plugin's declarations — no Power BI assumption in the core."""
    with session_scope() as s:
        items = _items(s)
        payload, hazard, demo = items["spec:payload"], items["hazard:at"], items["demo:pick"]
        # a plugin-declared synonym: the question says "lifting capacity", the corpus says "payload"
        r = retrieve(s, plugin, "What is the lifting capacity of ARM-7?", k=2)
        assert any(e.canonical == "ARM-7" for e in r.analysis.entities)  # filed subject, found as an entity
        assert "payload" in r.analysis.variants and "payload" in r.analysis.lexemes  # the declared synonym
        assert r.selected[0].item.id == payload.id, [sc.item.statement for sc in r.selected]
        assert "subject is the query entity 'ARM-7'" in " ".join(r.selected[0].explanation)
        # a plugin-declared intent cue ("hazard") maps to the core limitation intent, which prefers the plugin's
        # negative-polarity type and expands with it
        r = retrieve(s, plugin, "Which hazard applies when ARM-7 moves at full speed?", k=2)
        assert r.analysis.intent == "limitation" and r.selected[0].item.id == hazard.id
        assert r.selected[0].signals["intent_affinity"] > 0
        assert r.summary()["version"] == RETRIEVAL_VERSION
        # example intent expands with the plugin's example-role type
        r = retrieve(s, plugin, "Show me a demonstration example of ARM-7", k=1)
        assert any(sc.item.id == demo.id for sc in r.selected)
        # the plan is derived from the evidence: ARM-7 must be covered, and completeness accepts the synonym
        config = resolve_text_search_config(s, plugin.text_search_config())
        r = retrieve(s, plugin, "What is the lifting capacity of ARM-7?", k=3)
        plan = build_plan(s, r.analysis, r.selected, config=config, plugin=plugin)
        assert [c.term for c in plan.must_cover] == ["ARM-7"]
        assert check_completeness(s, config, "ARM-7 lifts 5 kg at the flange [1].", plan)["missing_must"] == []
        # the answer path in p3 mode carries the account of itself
        ans = answer_question(s, plugin, "What is the payload limit of ARM-7?")
        assert ans.mode == "p3" and ans.retrieval and ans.plan is not None and ans.timings_ms["total"] >= 0
        assert ans.citations and "5 kg" in ans.answer


@respx.mock
def test_dependency_change_propagates_through_declared_roles(plugin):
    respx.reset()
    _mock_site(2)  # the payload specification disappears from the page
    with session_scope() as s:
        src = s.execute(select(Source).where(Source.domain_id == DOMAIN_ID)).scalar_one()
        stats = crawl_source(s, src, fetcher=Fetcher(default_delay=0, max_retries=1))
        assert stats.changed == 1
        spec_doc = s.execute(
            select(Document).where(Document.url == "https://docs.roboticslab.test/manual/spec")
        ).scalar_one()
        result = ingest_document(s, spec_doc, plugin)
        assert result.items_stale == 1
    with session_scope() as s:
        payload, rule = s.get(KnowledgeItem, state["payload"]), s.get(KnowledgeItem, state["rule"])
        assert payload.status == ItemStatus.STALE
        assert rule.needs_revalidation and str(payload.id) in (rule.revalidation_reason or "")
