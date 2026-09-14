"""New-source discovery: candidates, scored and filtered, never trusted automatically (ADR 0005, P2.8).

``discover(session, plugin, ...)`` runs the plugin's discovery queries (plus user keywords) through the search
provider and registers unknown hosts as CANDIDATE sources with an explainable relevance score. It is the only
path by which the platform proposes a source it did not know; approval stays a person's decision.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...models import Document, DomainKeyword, Source, SourceStatus
from ..plugins.base import DomainPlugin
from .normalize import canonicalize_url

log = logging.getLogger(__name__)

_WORD = re.compile(r"[a-z0-9][a-z0-9+#.-]{2,}")
# hosts nobody should crawl as a knowledge source: link farms, aggregators that only republish, and pastebins
GLOBAL_DENY_HOSTS = (
    "pinterest.com",
    "facebook.com",
    "x.com",
    "twitter.com",
    "instagram.com",
    "tiktok.com",
    "pastebin.com",
    "scribd.com",
    "slideshare.net",
    "coursehero.com",
)


@dataclass
class Candidate:
    host: str
    url: str
    title: str
    snippet: str
    queries: list[str] = field(default_factory=list)
    best_rank: int = 999
    relevance: int = 0
    reasons: list[str] = field(default_factory=list)


@dataclass
class DiscoveryStats:
    queries: int = 0
    hits: int = 0
    duplicates: int = 0
    denied: int = 0
    irrelevant: int = 0
    candidates_added: int = 0
    capped: int = 0

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _host(url: str) -> str:
    h = urlsplit(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def _under(host: str, entries: list[str] | tuple[str, ...]) -> bool:
    return any(host == e or host.endswith("." + e) for e in (x.strip().lower() for x in entries if x.strip()))


def vocabulary_terms(plugin: DomainPlugin) -> set[str]:
    """Words from the taxonomy, terminology keys and the domain name: what "about this domain" means."""
    terms: set[str] = set()
    for path in plugin.taxonomy_paths():
        for part in path.split("/"):
            terms.update(_WORD.findall(part.lower()))
    for term in plugin.terminology():
        terms.update(_WORD.findall(term.lower()))
    terms.update(_WORD.findall(plugin.name.lower()))
    return {t for t in terms if len(t) > 2 and t not in {"and", "the", "for", "with", "from"}}


def score_candidate(c: Candidate, terms: set[str], prefer_hosts: list[str]) -> None:
    """Explainable relevance 0–100 (ADR 0005); every point comes with a reason in ``c.reasons``."""
    score = 0
    extra = min(len(set(c.queries)) - 1, 3)
    if extra > 0:
        score += 15 * extra
        c.reasons.append(f"hit by {len(set(c.queries))} discovery queries (+{15 * extra})")
    words = set(_WORD.findall(f"{c.title} {c.snippet}".lower()))
    matched = sorted(words & terms)
    if matched:
        pts = min(5 * len(matched), 30)
        score += pts
        c.reasons.append(f"domain terms in title/snippet: {', '.join(matched[:6])} (+{pts})")
    if c.best_rank <= 3:
        score += 10
        c.reasons.append("top-3 result for a query (+10)")
    elif c.best_rank <= 10:
        score += 5
        c.reasons.append("top-10 result for a query (+5)")
    if _under(c.host, prefer_hosts):
        score += 25
        c.reasons.append("publisher preferred by the plugin (+25)")
    c.relevance = max(0, min(100, score))


def discover(
    session: Session,
    plugin: DomainPlugin,
    *,
    search: Any,
    queries: list[str] | None = None,
    limit: int = 10,
) -> DiscoveryStats:
    spec = plugin.discovery()
    stats = DiscoveryStats()
    known_hosts = {_host(s.url) for s in session.execute(select(Source).where(Source.domain_id == plugin.id)).scalars()}
    known_urls = {
        canonicalize_url(u)
        for u in session.execute(
            select(Document.canonical_url).where(Document.domain_id == plugin.id, Document.canonical_url.is_not(None))
        ).scalars()
    }
    user_keywords = list(
        session.execute(
            select(DomainKeyword.keyword).where(DomainKeyword.domain_id == plugin.id, DomainKeyword.enabled.is_(True))
        ).scalars()
    )
    queries = queries or (spec.queries + user_keywords) or [f"{plugin.name} documentation"]
    deny = list(spec.deny_hosts) + list(GLOBAL_DENY_HOSTS) + get_settings().discovery_deny_hosts_list()
    terms = vocabulary_terms(plugin)
    found: dict[str, Candidate] = {}
    for q in queries:
        stats.queries += 1
        try:
            hits = search.search(q, limit=limit)
        except Exception as exc:
            log.warning("search failed for %r: %s", q, exc)
            continue
        for rank, h in enumerate(hits, start=1):
            url = canonicalize_url(h.url)
            if not url:
                continue
            stats.hits += 1
            host = _host(url)
            if host in known_hosts or url in known_urls:
                stats.duplicates += 1
                continue
            if _under(host, deny):
                stats.denied += 1
                continue
            c = found.setdefault(
                host, Candidate(host=host, url=url, title=(h.title or "")[:200], snippet=h.snippet or "")
            )
            c.queries.append(q)
            c.best_rank = min(c.best_rank, rank)
    ranked: list[Candidate] = []
    for c in found.values():
        score_candidate(c, terms, spec.prefer_hosts)
        if c.relevance < spec.min_relevance:
            stats.irrelevant += 1
            log.info("discovery: %s scored %d (< %d), not registered", c.host, c.relevance, spec.min_relevance)
            continue
        ranked.append(c)
    ranked.sort(key=lambda c: (-c.relevance, c.host))
    for c in ranked:
        if stats.candidates_added >= spec.max_candidates:
            stats.capped += 1
            continue
        session.add(
            Source(
                domain_id=plugin.id,
                key=f"discovered:{c.host}",
                origin="discovered",
                source_class="community",  # never official: a person decides what a discovered source is
                relevance=c.relevance,
                name=c.title or c.host,
                url=c.url,
                publisher=c.host,
                authority=30,
                status=SourceStatus.CANDIDATE,
                enabled=False,
                max_depth=1,
                max_pages=20,
                notes="Discovered via: "
                + "; ".join(sorted(set(c.queries)))[:300]
                + "\nWhy this score: "
                + "; ".join(c.reasons),
            )
        )
        known_hosts.add(c.host)
        stats.candidates_added += 1
    session.flush()
    return stats


__all__ = ["Candidate", "DiscoveryStats", "GLOBAL_DENY_HOSTS", "discover", "score_candidate", "vocabulary_terms"]
