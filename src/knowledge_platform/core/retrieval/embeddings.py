"""Embedding text construction and batch embedding of knowledge items (§22)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ...adapters import get_embedder
from ...models import KnowledgeItem


def embedding_text(item: KnowledgeItem) -> str:
    parts = [item.statement]
    if item.topic:
        parts.append(f"Topic: {item.topic}")
    if item.explanation:
        parts.append(item.explanation[:600])
    return "\n".join(parts)


def embed_items(session: Session, items: list[KnowledgeItem]) -> int:
    if not items:
        return 0
    embedder = get_embedder()
    vectors = embedder.embed([embedding_text(i) for i in items])
    for item, vec in zip(items, vectors, strict=True):
        item.embedding = vec
        item.embedding_model = embedder.identity
    session.flush()
    return len(items)
