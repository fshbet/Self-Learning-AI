"""Human-authored knowledge (req. 9): USER and ORGANIZATION provenance.

Items entered here do not come from a crawled document, so their evidence is a ``human`` record with
``details.provided = True``: the author's name, the declared authority, and (optionally) a quote and URL of the
material they relied on. They pass through the same scoring, dedup and lifecycle as extracted knowledge and are
exported with their provenance level intact, so consumers can always tell them apart from official sources.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from ..models import Evidence, ItemStatus, KnowledgeItem, utcnow
from .extraction.extractor import statement_hash
from .pipeline import rescore, run_validators
from .plugins.base import DomainPlugin
from .quality.dedup import find_exact, find_near
from .quality.provenance import derive_polarity
from .retrieval.embeddings import embed_items
from .verification.conflicts import detect_conflicts

ENTRY_VERSION = "human-entry@1.0"


@dataclass
class KnowledgeEntry:
    statement: str
    subject: str
    predicate: str
    object: str
    knowledge_type: str = "fact"
    explanation: str = ""
    topic: str = ""
    tags: list[str] = field(default_factory=list)
    code: str | None = None
    product_version: str | None = None
    provenance: str = "USER"  # USER | ORGANIZATION
    polarity: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    evidence_text: str = ""  # what the author relied on (quote, note)
    evidence_url: str | None = None
    provided_by: str = "user"
    authority: int = 60  # declared trust in the author / organisation source (0-100)


class DuplicateKnowledge(ValueError):
    def __init__(self, existing_id: uuid.UUID) -> None:
        super().__init__(f"an equivalent item already exists: {existing_id}")
        self.existing_id = existing_id


def create_knowledge(session: Session, plugin: DomainPlugin, entry: KnowledgeEntry) -> KnowledgeItem:
    if entry.provenance not in ("USER", "ORGANIZATION"):
        raise ValueError("provenance must be USER or ORGANIZATION for human-authored knowledge")
    if entry.knowledge_type not in plugin.knowledge_types():
        raise ValueError(f"knowledge_type must be one of {plugin.knowledge_types()}")
    content_hash = statement_hash(entry.statement)
    existing = find_exact(session, plugin.id, content_hash)
    if existing is not None:
        raise DuplicateKnowledge(existing.id)

    item = KnowledgeItem(
        domain_id=plugin.id,
        knowledge_type=entry.knowledge_type,
        subject=entry.subject.strip()[:300],
        predicate=entry.predicate.strip()[:200],
        object=entry.object.strip(),
        statement=entry.statement.strip(),
        explanation=entry.explanation.strip(),
        topic=entry.topic if entry.topic in set(plugin.taxonomy_paths()) else "",
        tags=[t.strip() for t in entry.tags if t.strip()][:12],
        code=entry.code or None,
        product_version=entry.product_version or None,
        language=plugin.manifest.language,
        status=ItemStatus.EXTRACTED,
        content_hash=content_hash,
        extraction={"method": "human", "extractor_version": ENTRY_VERSION, "provided_by": entry.provided_by},
        origin="DIRECT",
        provenance=entry.provenance,
        polarity=entry.polarity or derive_polarity(entry.knowledge_type),
        details={k: v for k, v in entry.details.items() if isinstance(v, str) and v.strip()},
        first_discovered_at=utcnow(),
    )
    session.add(item)
    session.flush()
    item.evidence.append(
        Evidence(
            knowledge_item_id=item.id,
            evidence_type="human",
            relation="supports",
            excerpt=entry.evidence_text.strip()
            or f"Provided by {entry.provided_by} ({entry.provenance.lower()} knowledge)",
            url=entry.evidence_url or None,
            verified=True,
            retrieved_at=utcnow(),
            details={
                "provided": True,
                "provided_by": entry.provided_by,
                "authority": max(0, min(int(entry.authority), 100)),
                "provenance": entry.provenance,
            },
        )
    )
    session.flush()
    embed_items(session, [item])
    near = find_near(session, plugin.id, item)
    if near is not None:
        other, _ = near
        session.delete(item)
        session.flush()
        raise DuplicateKnowledge(other.id)
    run_validators(session, item, plugin)
    rescore(session, item, plugin, actor=f"human:{entry.provided_by}")
    detect_conflicts(session, item, domain_name=plugin.name)
    session.flush()
    return item
