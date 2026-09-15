"""Hybrid retrieval: lexical (PostgreSQL full-text) + vector (pgvector), fused with RRF (§22).

Language independence (P2.5): the lexical side uses the domain's PostgreSQL text-search configuration
(``DomainPlugin.text_search_config()``: declared in the manifest, else derived from its language, else ``simple``)
instead of assuming English. The configuration name is validated against ``pg_ts_config`` and inlined as a literal,
so the ``english`` expression index created by the initial migration keeps serving English domains; other
configurations run without an expression index (fine for tens of thousands of rows — add a per-domain expression
index when a large non-English domain needs it). Vector retrieval is language-neutral.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload

from ...adapters import get_embedder
from ...models import ItemStatus, KnowledgeItem

log = logging.getLogger(__name__)

_TEXT_COLUMNS = (
    "coalesce(subject,'') || ' ' || coalesce(statement,'') || ' ' || "
    "coalesce(explanation,'') || ' ' || coalesce(topic,'')"
)
DEFAULT_TEXT_SEARCH_CONFIG = "simple"
# kept for the migration that created the English expression index and for callers that build the same expression
TSV_EXPR = f"to_tsvector('english', {_TEXT_COLUMNS})"


def tsv_expr(config: str) -> str:
    return f"to_tsvector('{config}', {_TEXT_COLUMNS})"


_known_configs: set[str] | None = None
_known_lock = threading.Lock()


def available_text_search_configs(session: Session) -> set[str]:
    """Names in ``pg_ts_config`` (cached per process): the only values ever inlined into the query."""
    global _known_configs
    with _known_lock:
        if _known_configs is None:
            _known_configs = {str(r[0]) for r in session.execute(text("SELECT cfgname FROM pg_ts_config")).all()}
        return set(_known_configs)


def resolve_text_search_config(session: Session, wanted: str | None) -> str:
    """A safe, existing configuration name: ``wanted`` when PostgreSQL knows it, else ``simple`` (logged once)."""
    name = (wanted or DEFAULT_TEXT_SEARCH_CONFIG).strip().lower()
    if name in available_text_search_configs(session):
        return name
    log.warning("text-search configuration %r is not installed in PostgreSQL; using 'simple'", name)
    return DEFAULT_TEXT_SEARCH_CONFIG


def _config_for_domain(domain_id: str) -> str | None:
    try:
        from ..plugins.registry import get_registry

        return get_registry().get(domain_id).text_search_config()
    except Exception:  # unknown domain (fixtures, tests): no assumption, "simple"
        return None


# Retrieval policy (P2.2) — what an answer may be built from:
#   VERIFIED / SUPPORTED  normal retrieval
#   CONFLICTED / STALE    retrieved, but labelled as caution for the answer model (and the evaluator checks the label)
#   CANDIDATE             excluded unless a caller asks for it explicitly; then labelled UNVERIFIED — never silently
#                         ranked alongside trusted knowledge. It stays in the database for future validation.
#   EXTRACTED / REJECTED / SUPERSEDED   never served (superseded knowledge is historical: exported, not answered)
DEFAULT_STATUSES = (ItemStatus.SUPPORTED, ItemStatus.VERIFIED, ItemStatus.CONFLICTED, ItemStatus.STALE)
CAUTION_STATUSES = (ItemStatus.CONFLICTED, ItemStatus.STALE)

_SQL = """
WITH vec AS (
    SELECT id, row_number() OVER (ORDER BY embedding <=> CAST(:vec AS vector)) AS r,
           1 - (embedding <=> CAST(:vec AS vector)) AS sim
    FROM knowledge_items
    WHERE domain_id = :domain AND embedding IS NOT NULL AND embedding_model = :emb AND status = ANY(:statuses)
    ORDER BY embedding <=> CAST(:vec AS vector)
    LIMIT :pool
),
lex AS (
    SELECT id, row_number() OVER (ORDER BY ts_rank_cd({tsv}, q) DESC) AS r
    FROM knowledge_items, websearch_to_tsquery('{cfg}', :q) q
    WHERE domain_id = :domain AND status = ANY(:statuses) AND {tsv} @@ q
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
    signals: dict[str, float] = field(default_factory=dict)  # ranking contributions (pipeline retrieval)
    explanation: list[str] = field(default_factory=list)  # why it ranked where it did


def hybrid_search(
    session: Session,
    *,
    domain_id: str,
    query: str,
    limit: int = 10,
    statuses: list[str] | None = None,
    include_candidates: bool = False,
    text_search_config: str | None = None,
    pool: int = 50,
    rrf_k: int = 60,
) -> list[SearchResult]:
    """``statuses`` overrides the policy entirely; otherwise DEFAULT_STATUSES, plus CANDIDATE when asked for.
    ``text_search_config`` defaults to the domain plugin's declaration (``simple`` for unknown domains)."""
    query = query.strip()
    if not query:
        return []
    cfg = resolve_text_search_config(session, text_search_config or _config_for_domain(domain_id))
    if statuses is None:
        statuses = [st.value for st in DEFAULT_STATUSES]
        if include_candidates:
            statuses.append(ItemStatus.CANDIDATE.value)
    embedder = get_embedder()
    vec = embedder.embed_one(query)
    rows = session.execute(
        text(_SQL.format(tsv=tsv_expr(cfg), cfg=cfg)),
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
