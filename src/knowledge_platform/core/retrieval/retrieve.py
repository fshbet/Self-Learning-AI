"""The retrieval pipeline (ADR 0006): analysis → candidates → scoring → diversity → expansion.

``retrieve()`` returns everything a caller — the answerer, the API, the offline evaluation — needs to explain
the result: the query analysis, the whole candidate pool with per-channel ranks, the scored ranking with each
item's signal contributions, the selected context, and per-stage timings. ``stage`` limits the pipeline so
the offline evaluation can measure each stage's contribution:

    "rrf"        candidates from all channels, ranked by the old two-channel fusion only
    "rank"       + explainable signals
    "diverse"    + near-duplicate suppression
    "full"       + bounded relationship expansion (default)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from ...models import ItemStatus
from .candidates import Candidate, candidate_pool, embed_query
from .query import QueryAnalysis, analyze, lexemes
from .rank import Scored, diversify, expand, score_candidates, source_facts
from .search import DEFAULT_STATUSES, SearchResult, resolve_text_search_config

RETRIEVAL_VERSION = "retrieval@2.0"  # 1.x = two-channel RRF (P2); 2.0 = ADR 0006 pipeline
STAGES = ("rrf", "rank", "diverse", "full")


@dataclass
class Retrieval:
    query: str
    analysis: QueryAnalysis
    candidates: dict[str, Candidate]
    ranked: list[Scored]
    selected: list[Scored]
    k: int
    stage: str
    timings_ms: dict[str, int] = field(default_factory=dict)

    def as_results(self) -> list[SearchResult]:
        """The selected context as the primitive SearchResult (with signals) for existing callers."""
        return [
            SearchResult(
                item=s.item,
                score=s.score,
                vec_rank=s.candidate.vec_rank,
                lex_rank=s.candidate.lex_rank,
                similarity=s.candidate.similarity,
                signals=dict(s.signals),
                explanation=list(s.explanation),
            )
            for s in self.selected
        ]

    def summary(self) -> dict[str, Any]:
        return {
            "version": RETRIEVAL_VERSION,
            "stage": self.stage,
            "analysis": self.analysis.as_dict(),
            "candidates": len(self.candidates),
            "channels": {
                ch: sum(1 for c in self.candidates.values() if ch in c.channels)
                for ch in ("vector", "lexical", "entity")
            },
            "timings_ms": self.timings_ms,
        }


_type_lexeme_cache: dict[tuple[str, str], dict[str, set[str]]] = {}


def type_lexemes(session: Session, plugin: Any, config: str) -> dict[str, set[str]]:
    """Lexemes of each declared knowledge type's name, label and prefix ("hazard" → {hazard}), so a question
    that names a type ("which hazard …") can prefer items of that type. Declared vocabulary only."""
    key = (plugin.id, config)
    hit = _type_lexeme_cache.get(key)
    if hit is not None:
        return hit
    out: dict[str, set[str]] = {}
    for spec in plugin.type_specs():
        words = " ".join(w.replace("_", " ") for w in (spec.name, spec.label or "", spec.prefix or ""))
        out[spec.name] = {lx for lx in lexemes(session, config, words) if len(lx) >= 3}
    _type_lexeme_cache[key] = out
    return out


def retrieve(
    session: Session,
    plugin: Any,
    query: str,
    *,
    k: int = 8,
    pool: int = 60,
    statuses: list[str] | None = None,
    include_candidates: bool = False,
    stage: str = "full",
    expansion_budget: int = 3,
) -> Retrieval:
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}")
    timings: dict[str, int] = {}
    t0 = time.perf_counter()
    config = resolve_text_search_config(session, plugin.text_search_config())
    spec = getattr(plugin.manifest, "retrieval", None)
    analysis = analyze(
        session,
        domain_id=plugin.id,
        query=query,
        text_search_config=config,
        synonyms=getattr(spec, "synonyms", None) or None,
        intent_cues=getattr(spec, "intent_cues", None) or None,
    )
    timings["analysis"] = int((time.perf_counter() - t0) * 1000)
    if statuses is None:
        statuses = [s.value for s in DEFAULT_STATUSES]
        if include_candidates:
            statuses.append(ItemStatus.CANDIDATE.value)
    t1 = time.perf_counter()
    embedded = embed_query(query)  # timed on its own: it includes any model swap on the local model server
    timings["embedding"] = int((time.perf_counter() - t1) * 1000)
    t1 = time.perf_counter()
    candidates = candidate_pool(
        session, domain_id=plugin.id, analysis=analysis, config=config, statuses=statuses, pool=pool, embedded=embedded
    )
    timings["candidates"] = int((time.perf_counter() - t1) * 1000)
    t2 = time.perf_counter()
    sources = source_facts(session, plugin.id)
    ranked = score_candidates(
        candidates,
        analysis,
        plugin=plugin,
        sources=sources,
        stage="rrf" if stage == "rrf" else "full",
        type_terms=type_lexemes(session, plugin, config),
    )
    timings["ranking"] = int((time.perf_counter() - t2) * 1000)
    t3 = time.perf_counter()
    selected = diversify(ranked, k=k) if stage in ("diverse", "full") else ranked[:k]
    timings["diversity"] = int((time.perf_counter() - t3) * 1000)
    t4 = time.perf_counter()
    if stage == "full":
        selected = expand(session, selected, analysis, plugin=plugin, statuses=statuses, budget=expansion_budget)
    timings["expansion"] = int((time.perf_counter() - t4) * 1000)
    timings["total"] = int((time.perf_counter() - t0) * 1000)
    return Retrieval(
        query=query,
        analysis=analysis,
        candidates=candidates,
        ranked=ranked,
        selected=selected,
        k=k,
        stage=stage,
        timings_ms=timings,
    )


def pipeline_search(
    session: Session,
    *,
    domain_id: str,
    query: str,
    limit: int = 10,
    statuses: list[str] | None = None,
    include_candidates: bool = False,
    text_search_config: str | None = None,
    stage: str = "full",
    plugin: Any = None,
    **_: Any,
) -> list[SearchResult]:
    """Drop-in for ``hybrid_search`` that runs the full pipeline (used by the offline evaluation and callers
    that only need the ranked list)."""
    if plugin is None:
        from ..plugins.registry import get_registry

        plugin = get_registry().get(domain_id)
    r = retrieve(session, plugin, query, k=limit, statuses=statuses, include_candidates=include_candidates, stage=stage)
    return r.as_results()


__all__ = ["RETRIEVAL_VERSION", "STAGES", "Retrieval", "pipeline_search", "retrieve"]
