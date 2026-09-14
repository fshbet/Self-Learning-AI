"""Transitive dependency propagation (audit P1.7): chains, diamonds and cycles; reasons kept; review flags untouched."""

from __future__ import annotations

import pytest
from sqlalchemy import delete
from tests.conftest import requires_db

from knowledge_platform import adapters
from knowledge_platform.core.domains import sync_domain
from knowledge_platform.core.knowledge_entry import KnowledgeEntry, create_knowledge
from knowledge_platform.core.orchestration.jobs import revalidate_item_job
from knowledge_platform.core.plugins.registry import get_registry
from knowledge_platform.core.verification.review_flags import flag_for_review
from knowledge_platform.core.versioning.dependencies import (
    add_relation,
    affected_by,
    mark_dependents,
    unresolved_dependencies,
)
from knowledge_platform.core.versioning.lifecycle import transition
from knowledge_platform.db import session_scope
from knowledge_platform.models import ItemStatus, Job, KnowledgeItem

pytestmark = requires_db
DOMAIN = "example"


class _Emb:
    name, model, dimension = "fake", "fake", 768
    identity = "fake:fake:768"

    def embed(self, texts):
        return [[((hash(t) >> (i % 32)) & 1) * 0.9 for i in range(768)] for t in texts]

    def embed_one(self, text):
        return self.embed([text])[0]


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    emb = _Emb()
    monkeypatch.setattr(adapters, "get_embedder", lambda: emb)
    monkeypatch.setattr("knowledge_platform.core.retrieval.embeddings.get_embedder", lambda: emb)
    monkeypatch.setattr("knowledge_platform.core.retrieval.search.get_embedder", lambda: emb)
    with session_scope() as s:
        sync_domain(s, get_registry().get(DOMAIN))
    yield
    with session_scope() as s:
        s.execute(delete(KnowledgeItem).where(KnowledgeItem.domain_id == DOMAIN))


def _mk(s, plugin, name):
    # distinct subjects so no structural relations are derived automatically; edges are added explicitly
    return create_knowledge(
        s,
        plugin,
        KnowledgeEntry(
            statement=f"{name} is a component of the fixture graph.",
            subject=name,
            predicate="is",
            object="graph node",
            knowledge_type="fact",
            provenance="ORGANIZATION",
            provided_by="qa",
            authority=90,
            evidence_text="spec",
        ),
    )


def test_chain_diamond_and_cycle_propagation():
    plugin = get_registry().get(DOMAIN)
    with session_scope() as s:
        # chain D -> C -> B -> A (D depends on C, ...), diamond X -> {B, C} -> A, cycle A <-> Z
        a, b, c, d, x, z, unrelated = (_mk(s, plugin, n) for n in ("NA", "NB", "NC", "ND", "NX", "NZ", "NU"))
        add_relation(s, b, a, "depends_on")
        add_relation(s, c, b, "depends_on")
        add_relation(s, d, c, "example_of")
        add_relation(s, x, b, "depends_on")
        add_relation(s, x, c, "depends_on")
        add_relation(s, z, a, "depends_on")
        add_relation(s, a, z, "depends_on")  # cycle
        add_relation(s, unrelated, d, "related_to")  # non-propagating
        ids = {"a": a.id, "b": b.id, "c": c.id, "d": d.id, "x": x.id, "z": z.id, "u": unrelated.id}
        flag_for_review(d, "manual", "please double-check")  # must survive propagation untouched

        hops = {dep: h for dep, _, h in affected_by(s, a.id)}
        assert hops == {ids["b"]: 1, ids["z"]: 1, ids["c"]: 2, ids["x"]: 2, ids["d"]: 3}
        assert ids["u"] not in hops and a.id not in hops  # non-propagating edge / the root itself
        # deterministic: two calls give the same order
        assert affected_by(s, a.id) == affected_by(s, a.id)

        assert mark_dependents(s, a, "became STALE: test") == 5
        s.flush()
        by = {k: s.get(KnowledgeItem, v) for k, v in ids.items()}
        assert all(by[k].needs_revalidation for k in ("b", "c", "d", "x", "z"))
        assert not by["u"].needs_revalidation
        assert by["b"].revalidation_reason.startswith(f"dependency {a.id} (NA): became STALE")
        assert f"via {b.id} (2 hops)" in by["c"].revalidation_reason
        assert "(3 hops)" in by["d"].revalidation_reason
        # review flag untouched, and re-marking for the same root does not churn
        assert by["d"].needs_review and by["d"].review_kind == "manual"
        assert mark_dependents(s, a, "became STALE: again") == 0
        assert by["c"].revalidation_reason.endswith("became STALE: test")

        # unresolved dependencies: B waits for A (not live) once A is stale; C waits for B while B is still flagged
        transition(s, a, ItemStatus.STALE, reason="test", actor="test")
        assert [k.id for k in unresolved_dependencies(s, by["b"])] == [a.id]
        assert [k.id for k in unresolved_dependencies(s, by["c"])] == [b.id]  # live but still flagged
        # the cycle partner Z depends on the stale A: unresolved by liveness, not by the flag
        assert [k.id for k in unresolved_dependencies(s, by["z"])] == [a.id]
        # ... and A (on the cycle with Z) is not held hostage by Z's flag once Z is live
        assert unresolved_dependencies(s, by["a"]) == []
        a_id = a.id

    # revalidation settles in dependency order once the root is live again
    with session_scope() as s:
        a = s.get(KnowledgeItem, a_id)
        transition(s, a, ItemStatus.VERIFIED, reason="restored", actor="test")
        for key in ("c", "b"):  # C first: still blocked because B is flagged
            out = revalidate_item_job(s, Job(type="revalidate_item", payload={"item_id": str(ids[key])}))
            assert out["unresolved_dependencies"] == (1 if key == "c" else 0)
        assert (
            s.get(KnowledgeItem, ids["c"]).needs_revalidation and not s.get(KnowledgeItem, ids["b"]).needs_revalidation
        )
        out = revalidate_item_job(s, Job(type="revalidate_item", payload={"item_id": str(ids["c"])}))
        assert out["unresolved_dependencies"] == 0 and not s.get(KnowledgeItem, ids["c"]).needs_revalidation
        # the review flag on D is still there after its own revalidation
        revalidate_item_job(s, Job(type="revalidate_item", payload={"item_id": str(ids["d"])}))
        d = s.get(KnowledgeItem, ids["d"])
        assert d.needs_review and not d.needs_revalidation
