"""Sync domain plugins into the database (Domain + Source rows)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Domain, Evidence, KnowledgeItem, Source, SourceStatus, utcnow
from .plugins.base import DomainPlugin
from .quality.provenance import source_class_for


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
    class_changed: set = set()
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
        new_class = spec.source_class or source_class_for(spec.authority, "plugin")
        if src.source_class != new_class and src.id is not None:
            class_changed.add(src.id)
        src.source_class = new_class
        src.relevance = spec.relevance
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
    reclassified = _rederive_provenance(session, plugin.id, class_changed) if class_changed else 0
    return {"sources_created": created, "sources_updated": updated, "items_reclassified": reclassified}


def _rederive_provenance(session: Session, domain_id: str, source_ids: set) -> int:
    """A source's class changed (curation): items evidenced by it get their provenance re-derived (audit P1.8).
    Human-authored (USER / ORGANIZATION) and derived items keep theirs."""
    from .quality.provenance import derive_provenance

    item_ids = set(
        session.execute(
            select(Evidence.knowledge_item_id).where(Evidence.source_id.in_(source_ids), Evidence.verified.is_(True))
        ).scalars()
    )
    if not item_ids:
        return 0
    sources = {s.id: s for s in session.execute(select(Source).where(Source.domain_id == domain_id)).scalars()}
    changed = 0
    for item in session.execute(select(KnowledgeItem).where(KnowledgeItem.id.in_(item_ids))).scalars():
        if item.provenance in ("USER", "ORGANIZATION", "DERIVED"):
            continue
        evidenced = [
            sources[e.source_id]
            for e in item.evidence
            if e.evidence_type == "extraction" and e.verified and e.source_id in sources
        ]
        new = derive_provenance(evidenced) if evidenced else item.provenance
        if new != item.provenance:
            item.provenance = new
            changed += 1
    session.flush()
    return changed
