"""Source crawler: scoped BFS with change detection (§10, §11, §24)."""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...adapters import get_object_store
from ...adapters.storage.base import ObjectStore
from ...config import get_settings
from ...models import Document, DocumentStatus, Source, SourceStatus, utcnow
from .fetcher import FetchBlocked, Fetcher
from .normalize import canonicalize_url, normalize, normalize_html

log = logging.getLogger(__name__)


@dataclass
class CrawlStats:
    visited: int = 0
    fetched: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    confirmed: int = 0  # knowledge items whose source was re-checked and still states them
    skipped: int = 0
    blocked: int = 0
    failed: int = 0
    to_extract: list[str] = field(default_factory=list)  # document ids (str)

    def as_dict(self) -> dict[str, object]:
        d = self.__dict__.copy()
        d["to_extract"] = len(self.to_extract)
        return d


class UrlScope:
    """Decides whether a discovered link belongs to a source."""

    def __init__(self, source: Source) -> None:
        seed = urlsplit(source.url)
        self.host = seed.netloc.lower()
        base_path = seed.path if seed.path.endswith("/") else seed.path.rsplit("/", 1)[0] + "/"
        self.base_path = base_path
        self.allow = [re.compile(p) for p in (source.allow_patterns or [])]
        self.deny = [re.compile(p) for p in (source.deny_patterns or [])]

    def accepts(self, url: str) -> bool:
        parts = urlsplit(url)
        if parts.netloc.lower() != self.host:
            return False
        if any(p.search(url) for p in self.deny):
            return False
        if self.allow:
            return any(p.search(url) for p in self.allow)
        return parts.path.startswith(self.base_path)


def _confirm(session: Session, doc: Document) -> int:
    """Unchanged page: the claims it evidences are confirmed as still stated (freshness only, audit P1.4)."""
    from ..pipeline import confirm_unchanged_document
    from ..plugins.registry import get_registry

    try:
        plugin = get_registry().get(doc.domain_id)
    except KeyError:
        plugin = None
    return confirm_unchanged_document(session, doc, plugin)


def link_mirror(session: Session, doc: Document) -> Document | None:
    """Source independence (req. 22/31): the same text fetched from a *different* source is a mirror, not a second
    confirmation. The earliest copy is canonical; mirrors point at it and count once in confidence scoring."""
    canonical = session.execute(
        select(Document)
        .where(
            Document.domain_id == doc.domain_id,
            Document.content_hash == doc.content_hash,
            Document.id != doc.id,
            Document.canonical_document_id.is_(None),
        )
        .order_by(Document.fetched_at.asc(), Document.id.asc())
        .limit(1)
    ).scalar_one_or_none()
    if canonical is None or canonical.source_id == doc.source_id:
        doc.canonical_document_id = None
        return None
    doc.canonical_document_id = canonical.id
    doc.meta = {**(doc.meta or {}), "mirror_of": canonical.url}
    log.info("document %s mirrors %s (same content from another source)", doc.url, canonical.url)
    return canonical


def _raw_key(domain_id: str, raw: bytes, content_type: str) -> str:
    digest = hashlib.sha256(raw).hexdigest()
    ext = "pdf" if "pdf" in content_type.lower() else "html"
    return f"{domain_id}/{digest[:2]}/{digest}.{ext}"


def crawl_source(
    session: Session,
    source: Source,
    *,
    run_id: uuid.UUID | None = None,
    fetcher: Fetcher | None = None,
    store: ObjectStore | None = None,
    max_pages: int | None = None,
) -> CrawlStats:
    settings = get_settings()
    fetcher = fetcher or Fetcher()
    store = store or get_object_store()
    stats = CrawlStats()
    scope = UrlScope(source)
    limit = max_pages or source.max_pages or settings.crawl_max_pages_default

    seed = canonicalize_url(source.url)
    queue: deque[tuple[str, int]] = deque([(seed, 0)])
    seen: set[str] = {seed}

    source.robots_info = fetcher.robots_summary(seed)
    if not fetcher.allowed(seed):
        source.status = SourceStatus.BLOCKED
        source.last_error = "robots.txt disallows the seed URL"
        source.last_checked_at = utcnow()
        session.flush()
        stats.blocked += 1
        return stats

    while queue and stats.visited < limit:
        url, depth = queue.popleft()
        stats.visited += 1
        existing = session.execute(
            select(Document).where(Document.domain_id == source.domain_id, Document.url == url)
        ).scalar_one_or_none()

        try:
            result = fetcher.fetch(
                url,
                etag=existing.http_etag if existing else None,
                last_modified=existing.http_last_modified if existing else None,
            )
        except FetchBlocked:
            stats.blocked += 1
            continue
        except Exception as exc:  # network / retries exhausted
            stats.failed += 1
            log.warning("fetch failed %s: %s", url, exc)
            if existing:
                existing.error = str(exc)[:500]
            continue

        links: list[str] = []
        if result.not_modified and existing:
            stats.unchanged += 1
            existing.fetched_at = utcnow()
            stats.confirmed += _confirm(session, existing)
            if depth < source.max_depth and store.exists(existing.raw_object_key):
                base = str((existing.meta or {}).get("final_url") or url)
                links = normalize_html(store.get(existing.raw_object_key), base).links
        elif result.status != 200:
            stats.failed += 1
            if existing:
                existing.error = f"HTTP {result.status}"
        elif not any(t in result.content_type.lower() for t in ("html", "pdf", "xml", "text/plain")):
            stats.skipped += 1
        else:
            stats.fetched += 1
            # resolve relative links against the URL the server actually served (redirects, trailing slash)
            nd = normalize(result.content, result.final_url or url, result.content_type)
            links = nd.links
            if len(nd.text) < settings.chunk_min_chars:
                stats.skipped += 1
            else:
                key = _raw_key(source.domain_id, result.content, result.content_type)
                if not store.exists(key):
                    store.put(key, result.content, result.content_type.split(";")[0] or "text/html")
                if existing is None:
                    doc = Document(
                        domain_id=source.domain_id,
                        source_id=source.id,
                        url=url,
                        title=nd.title,
                        content_hash=nd.content_hash,
                        raw_object_key=key,
                        text=nd.text,
                        language=nd.language[:12],
                        http_etag=result.etag,
                        http_last_modified=result.last_modified,
                        published_at=nd.published_at,
                        depth=depth,
                        byte_size=len(result.content),
                        content_changed_at=utcnow(),
                        status=DocumentStatus.FETCHED,
                        meta={"final_url": result.final_url, "content_type": result.content_type},
                    )
                    session.add(doc)
                    session.flush()
                    link_mirror(session, doc)
                    stats.new += 1
                    stats.to_extract.append(str(doc.id))
                elif existing.content_hash != nd.content_hash:
                    existing.previous_content_hash = existing.content_hash
                    existing.content_hash = nd.content_hash
                    existing.raw_object_key = key
                    existing.text = nd.text
                    existing.title = nd.title or existing.title
                    existing.http_etag = result.etag
                    existing.http_last_modified = result.last_modified
                    existing.published_at = nd.published_at or existing.published_at
                    existing.fetched_at = utcnow()
                    existing.version += 1
                    existing.content_changed_at = utcnow()
                    existing.byte_size = len(result.content)
                    existing.status = DocumentStatus.FETCHED
                    existing.error = None
                    link_mirror(session, existing)
                    stats.changed += 1
                    stats.to_extract.append(str(existing.id))
                    source.last_changed_at = utcnow()
                else:
                    existing.http_etag = result.etag
                    existing.http_last_modified = result.last_modified
                    existing.fetched_at = utcnow()
                    existing.error = None
                    stats.unchanged += 1
                    stats.confirmed += _confirm(session, existing)
                    if existing.status != DocumentStatus.EXTRACTED:
                        stats.to_extract.append(str(existing.id))

        if depth < source.max_depth:
            for link in links:
                if link not in seen and scope.accepts(link):
                    seen.add(link)
                    queue.append((link, depth + 1))
        session.flush()

    source.last_checked_at = utcnow()
    source.last_error = None
    if stats.new or stats.changed:
        source.last_changed_at = utcnow()
    session.flush()
    return stats
