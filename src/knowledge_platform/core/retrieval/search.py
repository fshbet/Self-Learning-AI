"""Hybrid retrieval: lexical (PostgreSQL full-text) + vector (pgvector), fused with RRF (§22)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload

from ...adapters import get_embedder
from ...models import ItemStatus, KnowledgeItem

TSV_EXPR = (
    "to_tsvector('english', coalesce(subject,'') || ' ' || coalesce(statement,'') || ' ' || "
    "coalesce(explanation,'') || ' ' || coalesce(topic,''))"
)

# Retrieval policy (P2.2) — what an answer may be built from:
#   VERIFIED / SUPPORTED  normal retrieval
#   CONFLICTED / STALE    retrieved, but labelled as caution for the answer model (and the evaluator checks the label)
#   CANDIDATE             excluded unless a caller asks for it explicitly; then labelled UNVERIFIED — never silently
#                         ranked alongside trusted knowledge. It stays in the database for future validation.
#   EXTRACTED / REJECTED / SUPERSEDED   never served (superseded knowledge is historical: exported, not answered)
DEFAULT_STATUSES = (ItemStatus.SUPPORTED, ItemStatus.VERIFIED, ItemStatus.CONFLICTED, ItemStatus.STALE)
CAUTION_STATUSES = (ItemStatus.CONFLICTED, ItemStatus.STALE)

_SQL = f"""
WITH vec AS (
    SELECT id, row_number() OVER (ORDER BY embedding <=> CAST(:vec AS vector)) AS r,
           1 - (embedding <=> CAST(:vec AS vector)) AS sim
    FROM knowledge_items
    WHERE domain_id = :domain AND embedding IS NOT NULL AND embedding_model = :emb AND status = ANY(:statuses)
    ORDER BY embedding <=> CAST(:vec AS vector)
    LIMIT :pool
),
lex AS (
    SELECT id, row_number() OVER (ORDER BY ts_rank_cd({TSV_EXPR}, q) DESC) AS r
    FROM knowledge_items, websearch_to_tsquery('english', :q) q
    WHERE domain_id = :domain AND status = ANY(:statuses) AND {TSV_EXPR} @@ q
    LIMIT :pool
)
SELECT COALESCE(vec.id, lex.id) AS id,
       COALESCE(1.0 / (:k + vec.r), 0) + COALESCE(1.0 / (:k + lex.r), 0) AS score,
       vec.r AS vec_rank, lex.r AS lex_rank, vec.sim AS similarity
FROM vec FULL OUTER JOIN lex ON vec.id = lex.id
ORDER BY score DESC
LIMIT :limit
"""


@dataclass
class SearchResult:
    item: KnowledgeItem
    score: float
    vec_rank: int | None
    lex_rank: int | None
    similarity: float | None


def hybrid_search(
    session: Session,
    *,
    domain_id: str,
    query: str,
    limit: int = 10,
    statuses: list[str] | None = None,
    include_candidates: bool = False,
    pool: int = 50,
    rrf_k: int = 60,
) -> list[SearchResult]:
    """``statuses`` overrides the policy entirely; otherwise DEFAULT_STATUSES, plus CANDIDATE when asked for."""
    query = query.strip()
    if not query:
        return []
    if statuses is None:
        statuses = [st.value for st in DEFAULT_STATUSES]
        if include_candidates:
            statuses.append(ItemStatus.CANDIDATE.value)
    embedder = get_embedder()
    vec = embedder.embed_one(query)
    rows = session.execute(
        text(_SQL),
        {
            "vec": str(vec),
            "emb": embedder.identity,
            "domain": domain_id,
            "statuses": statuses,
            "q": query,
            "pool": pool,
            "k": rrf_k,
            "limit": limit,
        },
    ).all()
    if not rows:
        return []
    ids = [uuid.UUID(str(r.id)) for r in rows]
    items = {
        i.id: i
        for i in session.execute(
            select(KnowledgeItem).where(KnowledgeItem.id.in_(ids)).options(selectinload(KnowledgeItem.evidence))
        ).scalars()
    }
    out: list[SearchResult] = []
    for r in rows:
        item = items.get(uuid.UUID(str(r.id)))
        if item is None:
            continue
        out.append(
            SearchResult(
                item=item,
                score=float(r.score),
                vec_rank=int(r.vec_rank) if r.vec_rank is not None else None,
                lex_rank=int(r.lex_rank) if r.lex_rank is not None else None,
                similarity=float(r.similarity) if r.similarity is not None else None,
            )
        )
    return out
