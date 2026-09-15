"""Offline retrieval evaluation on the golden set (P3, phase 4): what retrieval alone delivers, without the model.

For every golden question the harness records the query, the expected concepts / authoritative sources, the
top-K knowledge items with their ranks and scores, the rank of the first expected evidence and a retrieval
failure reason; and computes, per question and aggregated:

* concept recall@K     share of the question's required concepts that appear in at least one top-K item
* precision@K          share of top-K items filed under the question's topic branch (the runner's definition)
* MRR                  1 / rank of the first item that carries a required concept (0 when none in the top-K)
* authoritative hit@K  a top-K item cites one of the question's authoritative URLs
* pool depth           the rank at which each concept first appears in a deeper candidate pool — tells apart
                       "not in the corpus", "in the pool but below K" and "not retrievable at all"

The golden set is read as it is; nothing here changes it or the knowledge base. Deterministic given the corpus
and the embedder, so a before/after comparison of ranking changes is meaningful.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ...models import KnowledgeItem
from ..plugins.base import DomainPlugin, EvalQuestion
from ..retrieval.search import SearchResult, hybrid_search

RETRIEVAL_EVAL_VERSION = "retrieval-eval@1.0"
LIVE = ("SUPPORTED", "VERIFIED", "CONFLICTED", "STALE")


@dataclass
class RankedItem:
    rank: int
    id: str
    score: float
    vec_rank: int | None
    lex_rank: int | None
    subject: str
    knowledge_type: str
    topic: str
    status: str
    confidence: float
    verification_level: int
    max_authority: int
    statement: str
    concepts_present: list[str]
    authoritative: bool


@dataclass
class QuestionRetrieval:
    id: str
    query: str
    topic: str
    required_concepts: list[str]
    authoritative_sources: list[str]
    k: int
    top: list[RankedItem]
    concept_recall: float | None
    precision: float | None
    mrr: float | None
    authoritative_hit: bool | None
    first_expected_rank: int | None
    concept_first_rank: dict[str, int | None] = field(default_factory=dict)  # rank in the deep pool, None = absent
    concept_in_corpus: dict[str, int] = field(default_factory=dict)  # live items carrying the concept
    failure_reason: str = ""
    latency_ms: int = 0


def _url_key(url: str) -> str:
    p = urlsplit(url)
    return (p.netloc.lower() + p.path.rstrip("/")).lower()


def _text(item: KnowledgeItem) -> str:
    return f"{item.subject} {item.statement} {item.explanation or ''}".lower()


def _concepts_in(item: KnowledgeItem, concepts: list[str]) -> list[str]:
    t = _text(item)
    return [c for c in concepts if c.lower() in t]


def _authoritative(item: KnowledgeItem, sources: list[str]) -> bool:
    if not sources:
        return False
    keys = [_url_key(s) for s in sources]
    for e in item.evidence:
        if e.url:
            k = _url_key(e.url)
            if any(k.startswith(s) or s.startswith(k) for s in keys):
                return True
    return False


def _ranked(results: list[SearchResult], q: EvalQuestion, sources: dict[Any, int]) -> list[RankedItem]:
    out = []
    for n, r in enumerate(results, start=1):
        it = r.item
        out.append(
            RankedItem(
                rank=n,
                id=str(it.id),
                score=round(float(r.score), 5),
                vec_rank=r.vec_rank,
                lex_rank=r.lex_rank,
                subject=it.subject,
                knowledge_type=it.knowledge_type,
                topic=it.topic,
                status=it.status,
                confidence=round(float(it.confidence), 3),
                verification_level=int(it.verification_level),
                max_authority=max((sources.get(e.source_id, 0) for e in it.evidence), default=0),
                statement=it.statement[:160],
                concepts_present=_concepts_in(it, q.required_concepts),
                authoritative=_authoritative(it, q.authoritative_sources),
            )
        )
    return out


def evaluate_question(
    session: Session,
    plugin: DomainPlugin,
    q: EvalQuestion,
    *,
    k: int = 8,
    pool: int = 50,
    sources: dict[Any, int] | None = None,
    search=hybrid_search,
) -> QuestionRetrieval:
    """``search`` is the retrieval function under test (signature of hybrid_search) so alternatives can be compared."""
    if sources is None:
        from ...models import Source

        rows = session.execute(select(Source).where(Source.domain_id == plugin.id)).scalars()
        sources = {src.id: src.authority for src in rows}
    started = time.perf_counter()
    deep = search(
        session, domain_id=plugin.id, query=q.question, limit=pool, text_search_config=plugin.text_search_config()
    )
    latency = int((time.perf_counter() - started) * 1000)
    top = _ranked(deep[:k], q, sources)
    ranked_pool = _ranked(deep, q, sources)
    concepts = q.required_concepts
    first_rank = {c: next((r.rank for r in ranked_pool if c in r.concepts_present), None) for c in concepts}
    in_corpus: dict[str, int] = {}
    for c in concepts:
        in_corpus[c] = int(
            session.execute(
                select(func.count())
                .select_from(KnowledgeItem)
                .where(
                    KnowledgeItem.domain_id == plugin.id,
                    KnowledgeItem.status.in_(LIVE),
                    or_(
                        KnowledgeItem.statement.ilike(f"%{c}%"),
                        KnowledgeItem.explanation.ilike(f"%{c}%"),
                        KnowledgeItem.subject.ilike(f"%{c}%"),
                    ),
                )
            ).scalar_one()
        )
    hit_concepts = {c for r in top for c in r.concepts_present}
    concept_recall = (len(hit_concepts) / len(concepts)) if concepts else None
    branch = (q.topic or "").split("/")[0].lower()
    precision = (sum(1 for r in top if r.topic.lower().startswith(branch)) / len(top)) if q.topic and top else None
    first_expected = next((r.rank for r in top if r.concepts_present), None)
    mrr = (1.0 / first_expected) if first_expected else (0.0 if concepts else None)
    auth_hit = any(r.authoritative for r in top) if q.authoritative_sources else None
    reasons: list[str] = []
    for c in concepts:
        if in_corpus[c] == 0:
            reasons.append(f"'{c}' not in corpus")
        elif first_rank[c] is None:
            reasons.append(f"'{c}' in corpus ({in_corpus[c]}) but not in pool-{pool}")
        elif first_rank[c] > k:
            reasons.append(f"'{c}' at rank {first_rank[c]} (> K={k})")
    if q.authoritative_sources and auth_hit is False:
        if not any(r.authoritative for r in ranked_pool):
            reasons.append("authoritative source not in pool (not crawled or not retrievable)")
        else:
            auth_rank = next(r.rank for r in ranked_pool if r.authoritative)
            reasons.append(f"authoritative source below K (rank {auth_rank})")
    return QuestionRetrieval(
        id=q.id,
        query=q.question,
        topic=q.topic,
        required_concepts=concepts,
        authoritative_sources=q.authoritative_sources,
        k=k,
        top=top,
        concept_recall=concept_recall,
        precision=precision,
        mrr=mrr,
        authoritative_hit=auth_hit,
        first_expected_rank=first_expected,
        concept_first_rank=first_rank,
        concept_in_corpus=in_corpus,
        failure_reason="; ".join(reasons) or ("ok" if concepts or q.authoritative_sources else "n/a"),
        latency_ms=latency,
    )


def _mean(values: list[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def evaluate_retrieval(
    session: Session, plugin: DomainPlugin, *, k: int = 8, pool: int = 50, search=hybrid_search
) -> dict[str, Any]:
    questions = [q for q in plugin.evaluation_set() if not q.expect_abstain]
    results = [evaluate_question(session, plugin, q, k=k, pool=pool, search=search) for q in questions]
    return {
        "version": RETRIEVAL_EVAL_VERSION,
        "domain": plugin.id,
        "dataset_version": plugin.evaluation_version(),
        "k": k,
        "pool": pool,
        "questions": len(results),
        "metrics": {
            "concept_recall_at_k": _mean([r.concept_recall for r in results]),
            "precision_at_k": _mean([r.precision for r in results]),
            "mrr": _mean([r.mrr for r in results]),
            "authoritative_hit_rate": _mean(
                [float(r.authoritative_hit) for r in results if r.authoritative_hit is not None]
            ),
            "expected_concept_hit_rate": _mean(
                [
                    float(r.concept_first_rank[c] is not None and r.concept_first_rank[c] <= k)
                    for r in results
                    for c in r.required_concepts
                ]
            ),
            "questions_fully_covered": sum(1 for r in results if r.concept_recall == 1.0),
            "mean_latency_ms": _mean([float(r.latency_ms) for r in results]),
        },
        "results": [asdict(r) for r in results],
    }


__all__ = ["RETRIEVAL_EVAL_VERSION", "QuestionRetrieval", "RankedItem", "evaluate_question", "evaluate_retrieval"]
