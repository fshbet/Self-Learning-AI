"""Candidate generation (ADR 0006, stage 3): three recall-oriented channels into one pool.

* vector   — cosine similarity of the query embedding (as before)
* lexical  — term-weighted OR query over the query's lexemes and variants: subject weighs most, then statement,
             explanation, topic. OR semantics (any term) with ``ts_rank_cd`` favouring items that match more of
             the terms — the previous AND query matched 0–1 items for ordinary questions.
* entity   — every live item whose subject *is* a detected entity, regardless of wording

The pool is deliberately broad; ranking (rank.py) decides what reaches the answer. Candidate recall and ranking
quality are measured separately by ``kp eval retrieval``.
"""

from __future__ import annotations

import math
import re
import threading
import time
from dataclasses import dataclass, field

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, selectinload

from ...adapters import get_embedder
from ...models import KnowledgeItem
from .query import QueryAnalysis
from .search import tsv_expr

WEIGHTED_TSV = (
    "setweight(to_tsvector('{cfg}', coalesce(subject,'')), 'A') || "
    "setweight(to_tsvector('{cfg}', coalesce(statement,'')), 'B') || "
    "setweight(to_tsvector('{cfg}', coalesce(explanation,'')), 'C') || "
    "setweight(to_tsvector('{cfg}', coalesce(topic,'')), 'D')"
)

_LEXEME = re.compile(r"'((?:[^']|'')+)':")
_VEC_SQL = """
SELECT id, row_number() OVER (ORDER BY embedding <=> CAST(:vec AS vector)) AS r,
       1 - (embedding <=> CAST(:vec AS vector)) AS sim
FROM knowledge_items
WHERE domain_id = :domain AND embedding IS NOT NULL AND embedding_model = :emb AND status = ANY(:statuses)
ORDER BY embedding <=> CAST(:vec AS vector)
LIMIT :pool
"""
_LEX_SQL = """
SELECT id, ts_rank_cd({tsv}, q, 1) AS s, row_number() OVER (ORDER BY ts_rank_cd({tsv}, q, 1) DESC) AS r
FROM knowledge_items, to_tsquery('{cfg}', :q) q
WHERE domain_id = :domain AND status = ANY(:statuses) AND {tsv} @@ q
ORDER BY s DESC
LIMIT :pool
"""


@dataclass
class Candidate:
    item: KnowledgeItem
    vec_rank: int | None = None
    similarity: float | None = None
    lex_rank: int | None = None
    lex_score: float | None = None
    rare_rank: int | None = None  # rank in the rare-term lexical query (discriminating terms only)
    entity_hits: list[str] = field(default_factory=list)  # entities whose subject this item carries
    lexemes: set[str] = field(default_factory=set)  # the item's own lexemes (subject + statement + explanation)

    @property
    def channels(self) -> list[str]:
        out = []
        if self.vec_rank is not None:
            out.append("vector")
        if self.lex_rank is not None:
            out.append("lexical")
        if self.rare_rank is not None:
            out.append("rare-terms")
        if self.entity_hits:
            out.append("entity")
        return out


_df_cache: dict[tuple[str, str, str], tuple[float, int]] = {}
_df_lock = threading.Lock()
DF_TTL_SECONDS = 600.0


def term_document_frequencies(
    session: Session, *, domain_id: str, config: str, lexemes: list[str], statuses: list[str]
) -> tuple[int, dict[str, int]]:
    """(live items in the domain, lexeme -> number of live items containing it). Cached briefly per domain:
    the corpus changes slowly and this is what turns 'power', 'bi', 'data' into the weak terms they are."""
    now = time.monotonic()
    total_key = (domain_id, config, "")
    with _df_lock:
        hit = _df_cache.get(total_key)
    if hit and hit[0] > now:
        total = hit[1]
    else:
        total = int(
            session.execute(
                select(func.count())
                .select_from(KnowledgeItem)
                .where(KnowledgeItem.domain_id == domain_id, KnowledgeItem.status.in_(statuses))
            ).scalar_one()
        )
        with _df_lock:
            _df_cache[total_key] = (now + DF_TTL_SECONDS, total)
    out: dict[str, int] = {}
    tsv = tsv_expr(config)  # the plain expression: indexed for English, a sequential scan otherwise
    for lex in lexemes[:12]:
        key = (domain_id, config, lex)
        with _df_lock:
            hit = _df_cache.get(key)
        if hit and hit[0] > now:
            out[lex] = hit[1]
            continue
        sql = (
            f"SELECT count(*) FROM knowledge_items WHERE domain_id = :d AND status = ANY(:st) "
            f"AND {tsv} @@ to_tsquery('{config}', :q)"
        )
        df = int(session.execute(text(sql), {"d": domain_id, "st": statuses, "q": _tsquery([lex])}).scalar_one())
        out[lex] = df
        with _df_lock:
            _df_cache[key] = (now + DF_TTL_SECONDS, df)
    return total, out


def idf_weights(total: int, dfs: dict[str, int]) -> dict[str, float]:
    return {lex: math.log((total + 1) / (df + 1)) + 1.0 for lex, df in dfs.items()}


def item_lexemes(session: Session, *, config: str, ids: list[str]) -> dict[str, set[str]]:
    if not ids:
        return {}
    rows = session.execute(
        text(
            "SELECT id::text, to_tsvector(CAST(:cfg AS regconfig), coalesce(subject,'') || ' ' || "
            "coalesce(statement,'') || ' ' || coalesce(explanation,''))::text "
            "FROM knowledge_items WHERE id = ANY(CAST(:ids AS uuid[]))"
        ),
        {"cfg": config, "ids": ids},
    ).all()
    out: dict[str, set[str]] = {}
    for item_id, tsv in rows:
        out[str(item_id)] = {m.group(1).replace("''", "'") for m in _LEXEME.finditer(tsv or "")}
    return out


def _tsquery(lexemes: list[str]) -> str:
    """OR of quoted lexemes (already stemmed/lower-cased by the same configuration)."""
    terms = []
    for lex in lexemes:
        clean = lex.replace("'", "''").replace("\\", "")
        if clean.strip():
            terms.append(f"'{clean}'")
    return " | ".join(dict.fromkeys(terms))


def vector_candidates(session: Session, *, domain_id: str, query: str, statuses: list[str], pool: int) -> list[tuple]:
    embedder = get_embedder()
    vec = embedder.embed_one(query)
    return session.execute(
        text(_VEC_SQL),
        {"vec": str(vec), "emb": embedder.identity, "domain": domain_id, "statuses": statuses, "pool": pool},
    ).all()


def lexical_candidates(
    session: Session, *, domain_id: str, lexemes: list[str], config: str, statuses: list[str], pool: int
) -> list[tuple]:
    q = _tsquery(lexemes)
    if not q:
        return []
    tsv = WEIGHTED_TSV.format(cfg=config)
    return session.execute(
        text(_LEX_SQL.format(tsv=tsv, cfg=config)),
        {"q": q, "domain": domain_id, "statuses": statuses, "pool": pool},
    ).all()


def entity_candidates(
    session: Session, *, domain_id: str, entities: list[str], statuses: list[str], per_entity: int = 30
) -> dict[str, list[str]]:
    """entity (lower) -> item ids whose subject equals it, best confidence first."""
    out: dict[str, list[str]] = {}
    for ent in entities:
        rows = session.execute(
            select(KnowledgeItem.id)
            .where(
                KnowledgeItem.domain_id == domain_id,
                KnowledgeItem.status.in_(statuses),
                func.lower(KnowledgeItem.subject) == ent.lower(),
            )
            .order_by(KnowledgeItem.confidence.desc(), KnowledgeItem.id)
            .limit(per_entity)
        ).all()
        if rows:
            out[ent.lower()] = [str(r[0]) for r in rows]
    return out


def candidate_pool(
    session: Session,
    *,
    domain_id: str,
    analysis: QueryAnalysis,
    config: str,
    statuses: list[str],
    pool: int = 60,
    channels: tuple[str, ...] = ("vector", "lexical", "entity"),
) -> dict[str, Candidate]:
    """Union of the channels, keyed by item id, with each channel's rank/score recorded."""
    cands: dict[str, Candidate] = {}
    ids_needed: set[str] = set()
    vec_rows = (
        vector_candidates(session, domain_id=domain_id, query=analysis.query, statuses=statuses, pool=pool)
        if "vector" in channels
        else []
    )
    lex_pool = max(pool, 100)  # lexical channels are cheap and recall-oriented: cast wider than the vector pool
    lex_rows = (
        lexical_candidates(
            session, domain_id=domain_id, lexemes=analysis.lexemes, config=config, statuses=statuses, pool=lex_pool
        )
        if "lexical" in channels
        else []
    )
    ent_rows = (
        entity_candidates(
            session, domain_id=domain_id, entities=[e.canonical for e in analysis.entities], statuses=statuses
        )
        if "entity" in channels
        else {}
    )
    # rare-term query: the discriminating half of the query terms only, so an item matching "rls" and
    # "restrict" is not buried under the hundreds matching "power" and "bi"
    rare_rows: list = []
    weights: dict[str, float] = {}
    if "lexical" in channels and analysis.lexemes:
        total, dfs = term_document_frequencies(
            session, domain_id=domain_id, config=config, lexemes=analysis.lexemes, statuses=statuses
        )
        weights = idf_weights(total, dfs)
        ranked_terms = sorted(analysis.lexemes, key=lambda lx: -weights.get(lx, 0.0))
        rare = [lx for lx in ranked_terms[: max(1, len(ranked_terms) // 2)] if dfs.get(lx, 0) < max(1, total * 0.15)]
        if rare and len(rare) < len(analysis.lexemes):
            rare_rows = lexical_candidates(
                session, domain_id=domain_id, lexemes=rare, config=config, statuses=statuses, pool=lex_pool
            )
    analysis.term_weights = weights
    for r in vec_rows:
        ids_needed.add(str(r.id))
    for r in lex_rows:
        ids_needed.add(str(r.id))
    for r in rare_rows:
        ids_needed.add(str(r.id))
    for ids in ent_rows.values():
        ids_needed.update(ids)
    if not ids_needed:
        return {}
    import uuid

    items = {
        str(i.id): i
        for i in session.execute(
            select(KnowledgeItem)
            .where(KnowledgeItem.id.in_([uuid.UUID(x) for x in ids_needed]))
            .options(selectinload(KnowledgeItem.evidence))
        ).scalars()
    }
    for r in vec_rows:
        c = cands.setdefault(str(r.id), Candidate(item=items[str(r.id)]))
        c.vec_rank, c.similarity = int(r.r), float(r.sim) if r.sim is not None else None
    for r in lex_rows:
        c = cands.setdefault(str(r.id), Candidate(item=items[str(r.id)]))
        c.lex_rank, c.lex_score = int(r.r), float(r.s)
    for r in rare_rows:
        c = cands.setdefault(str(r.id), Candidate(item=items[str(r.id)]))
        c.rare_rank = int(r.r)
    for ent, ids in ent_rows.items():
        for i in ids:
            c = cands.setdefault(i, Candidate(item=items[i]))
            c.entity_hits.append(ent)
    for item_id, lex in item_lexemes(session, config=config, ids=list(cands)).items():
        cands[item_id].lexemes = lex
    return cands


__all__ = [
    "Candidate",
    "WEIGHTED_TSV",
    "candidate_pool",
    "entity_candidates",
    "idf_weights",
    "item_lexemes",
    "lexical_candidates",
    "term_document_frequencies",
    "vector_candidates",
]
