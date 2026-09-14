"""Polarity-aware contradictions and structural non-contradiction verdicts (audit P1.6)."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import delete, select
from tests.conftest import requires_db

from knowledge_platform import adapters
from knowledge_platform.adapters.llm.base import LLMProvider, LLMResult
from knowledge_platform.core import llm_service
from knowledge_platform.core.domains import sync_domain
from knowledge_platform.core.knowledge_entry import KnowledgeEntry, create_knowledge
from knowledge_platform.core.plugins.registry import get_registry
from knowledge_platform.core.verification.conflicts import is_opposing, predicate_core
from knowledge_platform.db import session_scope
from knowledge_platform.models import Conflict, KnowledgeItem, KnowledgeRelation

pytestmark = requires_db
DOMAIN = "example"


class _Emb:
    name, model, dimension = "fake", "fake", 768
    identity = "fake:fake:768"

    def embed(self, texts):
        return [[((hash(t) >> (i % 32)) & 1) * 0.9 for i in range(768)] for t in texts]

    def embed_one(self, text):
        return self.embed([text])[0]


class _Judge(LLMProvider):
    """Verdict chosen by the statements' content so the test controls it."""

    name = "fake"

    def generate(self, *, system, user, model, temperature=0.0):
        return LLMResult(text="x", model=model)

    def generate_json(self, *, system, user, model, schema, temperature=0.0):
        if "DirectQuery" in user:
            return LLMResult(
                text=json.dumps(
                    {"verdict": "different_scopes", "conditions": "Import vs DirectQuery", "rationale": "storage modes"}
                ),
                model=model,
            )
        return LLMResult(text=json.dumps({"verdict": "contradict", "rationale": "they disagree"}), model=model)

    def available_models(self):
        return ["fake"]


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    emb, llm = _Emb(), _Judge()
    monkeypatch.setattr(adapters, "get_embedder", lambda: emb)
    monkeypatch.setattr("knowledge_platform.core.retrieval.embeddings.get_embedder", lambda: emb)
    monkeypatch.setattr("knowledge_platform.core.retrieval.search.get_embedder", lambda: emb)
    monkeypatch.setattr(adapters, "get_llm", lambda: llm)
    monkeypatch.setattr(llm_service, "get_llm", lambda: llm)
    with session_scope() as s:
        sync_domain(s, get_registry().get(DOMAIN))
    yield
    with session_scope() as s:
        s.execute(delete(KnowledgeItem).where(KnowledgeItem.domain_id == DOMAIN))


def _entry(kind, subject, predicate, obj, statement, **kw):
    return KnowledgeEntry(
        statement=statement,
        subject=subject,
        predicate=predicate,
        object=obj,
        knowledge_type=kind,
        provenance="ORGANIZATION",
        provided_by="qa",
        authority=90,
        evidence_text="spec",
        **kw,
    )


def test_predicate_core_and_opposition_are_polarity_aware():
    assert predicate_core("does not support") == ("support", True)
    assert predicate_core("supports") == ("support", False)
    assert predicate_core("is not") == ("is", True)

    class _I:
        def __init__(self, predicate, obj, polarity="positive"):
            self.predicate, self.object, self.polarity = predicate, obj, polarity

    assert is_opposing(_I("supports", "DirectQuery"), _I("does not support", "DirectQuery"))
    assert is_opposing(_I("supports", "DirectQuery"), _I("supports", "DirectQuery", polarity="negative"))
    assert is_opposing(_I("returns", "BLANK"), _I("returns", "0"))
    assert not is_opposing(_I("returns", "BLANK"), _I("returns", "blank."))
    assert not is_opposing(_I("covers", "A"), _I("includes", "B"))  # different core predicates never pair


def test_negation_opens_a_conflict_and_qualified_verdicts_are_kept_structurally():
    plugin = get_registry().get(DOMAIN)
    with session_scope() as s:
        pos = create_knowledge(
            s, plugin, _entry("fact", "WIDGET", "supports", "hot swap", "WIDGET supports hot swap of modules.")
        )
        neg = create_knowledge(
            s,
            plugin,
            _entry(
                "limitation",
                "WIDGET",
                "does not support",
                "hot swap",
                "WIDGET does not support hot swap of modules while powered.",
                polarity="negative",
            ),
        )
        pos_id, neg_id = pos.id, neg.id
        conflicts = s.execute(select(Conflict).where(Conflict.domain_id == DOMAIN)).scalars().all()
        assert len(conflicts) == 1 and {conflicts[0].item_a_id, conflicts[0].item_b_id} == {pos_id, neg_id}
        assert s.get(KnowledgeItem, pos_id).status == "CONFLICTED"

        # different recorded product versions: structural, no model call, no conflict
        v1 = create_knowledge(
            s,
            plugin,
            _entry("fact", "GADGET", "returns", "BLANK", "GADGET returns BLANK on error in v1.", product_version="1.0"),
        )
        v2 = create_knowledge(
            s,
            plugin,
            _entry("fact", "GADGET", "returns", "0", "GADGET returns 0 on error in v2.", product_version="2.0"),
        )
        rel = s.execute(
            select(KnowledgeRelation).where(
                KnowledgeRelation.relation_type == "compatible_under",
                KnowledgeRelation.from_item_id.in_([v1.id, v2.id]),
            )
        ).scalar_one()
        assert rel.details["verdict"] == "different_versions" and "1.0" in rel.details["conditions"]
        assert s.get(KnowledgeItem, v1.id).status != "CONFLICTED" and s.get(KnowledgeItem, v2.id).status != "CONFLICTED"

        # the judge says different_scopes: kept as a compatible_under relation with the distinguishing scope
        a = create_knowledge(
            s, plugin, _entry("fact", "SPROCKET", "caches", "results", "SPROCKET caches results in Import mode.")
        )
        b = create_knowledge(
            s, plugin, _entry("fact", "SPROCKET", "caches", "nothing", "SPROCKET caches nothing in DirectQuery mode.")
        )
        rel = s.execute(
            select(KnowledgeRelation).where(
                KnowledgeRelation.relation_type == "compatible_under", KnowledgeRelation.from_item_id.in_([a.id, b.id])
            )
        ).scalar_one()
        assert rel.details == {
            "verdict": "different_scopes",
            "conditions": "Import vs DirectQuery",
            "rationale": "storage modes",
        }
        assert (
            not s.execute(
                select(Conflict).where(Conflict.item_a_id.in_([a.id, b.id]) | Conflict.item_b_id.in_([a.id, b.id]))
            )
            .scalars()
            .all()
        )
        assert s.get(KnowledgeItem, a.id).status == "VERIFIED" and s.get(KnowledgeItem, b.id).status == "VERIFIED"
