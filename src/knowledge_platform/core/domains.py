"""Sync domain plugins into the database (Domain + Source rows)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Domain, Source, SourceStatus, utcnow
from .plugins.base import DomainPlugin


def sync_domain(session: Session, plugin: DomainPlugin) -> dict[str, int]:
    domain = session.get(Domain, plugin.id)
    if domain is None:
        domain = Domain(id=plugin.id, name=plugin.name)
        session.add(domain)
    domain.name = plugin.name
    domain.description = plugin.manifest.description
    domain.version = plugin.manifest.version
    domain.manifest = plugin.summary()
    domain.synced_at = utcnow()
    session.flush()

    created = updated = 0
    existing = {s.key: s for s in session.execute(select(Source).where(Source.domain_id == plugin.id)).scalars()}
    for spec in plugin.sources():
        src = existing.get(spec.key)
        if src is None:
            src = Source(domain_id=plugin.id, key=spec.key, url=spec.url, name=spec.name, origin="plugin")
            session.add(src)
            created += 1
        else:
            updated += 1
        src.origin = "plugin"
        src.name = spec.name
        src.url = spec.url
        src.publisher = spec.publisher
        src.source_type = spec.source_type
        src.authority = spec.authority
        src.access_type = spec.access_type
        src.license = spec.license
        src.permissions = spec.permissions
        src.crawl_frequency_hours = spec.crawl_frequency_hours
        src.max_depth = spec.max_depth
        src.max_pages = spec.max_pages
        src.allow_patterns = spec.allow_patterns
        src.deny_patterns = spec.deny_patterns
        src.enabled = spec.enabled
        src.notes = spec.notes
        if src.status == SourceStatus.CANDIDATE:
            src.status = SourceStatus.ACTIVE
    session.flush()
    return {"sources_created": created, "sources_updated": updated}
