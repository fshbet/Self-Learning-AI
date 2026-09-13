"""LLM extraction of knowledge items from a document (§14, §15).

The extractor produces *candidates*; each one must carry a verbatim quote that
we can locate in the chunk. No quote → no evidence → the candidate is dropped.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from ...config import get_settings
from ..llm_service import call_json, model_for
from ..plugins.base import DomainPlugin
from ..quality.provenance import derive_polarity
from .chunker import CHUNKER_VERSION, Chunk, chunk_text
from .prompts import EXTRACT_SYSTEM, EXTRACT_USER, PROMPT_VERSION, extract_schema

log = logging.getLogger(__name__)

_WS = re.compile(r"\s+")


@dataclass
class ExtractedItem:
    knowledge_type: str
    subject: str
    predicate: str
    object: str
    statement: str
    explanation: str
    topic: str
    tags: list[str]
    code: str | None
    product_version: str | None
    evidence_quote: str
    chunk: Chunk
    polarity: str = "positive"
    details: dict[str, Any] = field(default_factory=dict)
    quote_start: int | None = None  # offsets into the chunk text
    quote_end: int | None = None
    quote_verified: bool = False
    content_hash: str = ""
    extraction: dict[str, Any] = field(default_factory=dict)


def normalize_statement(s: str) -> str:
    return _WS.sub(" ", s).strip().lower().rstrip(".")


def statement_hash(s: str) -> str:
    return "sha256:" + hashlib.sha256(normalize_statement(s).encode("utf-8")).hexdigest()


def locate_quote(quote: str, text: str) -> tuple[int, int] | None:
    """Find ``quote`` in ``text``; tolerate whitespace and case differences."""
    q = quote.strip()
    if not q:
        return None
    idx = text.find(q)
    if idx >= 0:
        return idx, idx + len(q)
    # whitespace/case-insensitive search via a regex built from the quote's tokens
    tokens = [re.escape(t) for t in _WS.split(q) if t]
    if not tokens:
        return None
    pattern = r"\s+".join(tokens)
    m = re.search(pattern, text, flags=re.IGNORECASE)
    if m:
        return m.start(), m.end()
    # last resort: the first 8 tokens (models sometimes truncate the tail of a quote)
    if len(tokens) > 8:
        m = re.search(r"\s+".join(tokens[:8]), text, flags=re.IGNORECASE)
        if m:
            return m.start(), m.end()
    return None


def looks_like_boilerplate(chunk: Chunk) -> bool:
    """Cheap filter (§40): skip navigation-like sections before spending model time."""
    lines = [ln for ln in chunk.text.split("\n") if ln.strip()]
    if not lines:
        return True
    short = sum(1 for ln in lines if len(ln) < 40)
    if len(lines) >= 8 and short / len(lines) > 0.8:
        return True
    return False


def build_prompts(plugin: DomainPlugin, *, title: str, section: str, url: str, text: str) -> tuple[str, str]:
    terminology = "\n".join(f"- {k}: {v}" for k, v in list(plugin.terminology().items())[:80]) or "- (none)"
    taxonomy = "\n".join(f"- {p}" for p in plugin.taxonomy_paths()) or "- (none)"
    hints = plugin.extraction_hints().strip()
    system = EXTRACT_SYSTEM.format(
        domain_name=plugin.name,
        domain_description=plugin.manifest.description.strip(),
        knowledge_types=", ".join(plugin.knowledge_types()),
        extraction_hints=("\nDomain-specific guidance:\n" + hints) if hints else "",
        taxonomy=taxonomy,
        terminology=terminology,
    )
    user = EXTRACT_USER.format(title=title or "(untitled)", section=section or "(top)", url=url, text=text)
    return system, user


def chunk_hash(chunk: Chunk) -> str:
    """Section-level content hash: heading path + normalised text (req. 25)."""
    body = " ".join(chunk.text.split())
    return hashlib.sha256(f"{chunk.title}\n{body}".encode()).hexdigest()


def extract_from_text(
    plugin: DomainPlugin,
    *,
    text: str,
    title: str,
    url: str,
    session: Session | None = None,
    run_id: uuid.UUID | None = None,
    max_chunks: int | None = None,
    skip_hashes: set[str] | None = None,
) -> tuple[list[ExtractedItem], dict[str, Any]]:
    settings = get_settings()
    chunks = chunk_text(text, max_chars=settings.chunk_max_chars, min_chars=settings.chunk_min_chars)
    if max_chunks:
        chunks = chunks[:max_chunks]
    schema = extract_schema(plugin.knowledge_types())
    valid_topics = set(plugin.taxonomy_paths())
    model = model_for("extract")
    items: list[ExtractedItem] = []
    stats: dict[str, Any] = {
        "chunks": len(chunks),
        "chunks_skipped": 0,
        "chunks_unchanged": 0,
        "raw_items": 0,
        "unverified_dropped": 0,
        "chunk_hashes": [],
    }

    for chunk in chunks:
        digest = chunk_hash(chunk)
        stats["chunk_hashes"].append(
            {"index": chunk.index, "heading": chunk.title[:200], "sha256": digest, "prompt": PROMPT_VERSION}
        )
        if looks_like_boilerplate(chunk):
            stats["chunks_skipped"] += 1
            continue
        if skip_hashes and digest in skip_hashes:
            stats["chunks_unchanged"] += 1
            continue
        system, user = build_prompts(plugin, title=title, section=chunk.title, url=url, text=chunk.text)
        try:
            data = call_json(purpose="extract", system=system, user=user, schema=schema, session=session, run_id=run_id)
        except Exception as exc:
            log.warning("extraction failed for chunk %d of %s: %s", chunk.index, url, exc)
            continue
        for raw in data.get("items", []) or []:
            stats["raw_items"] += 1
            statement = (raw.get("statement") or "").strip()
            quote = (raw.get("evidence_quote") or "").strip()
            if not statement or not quote:
                stats["unverified_dropped"] += 1
                continue
            loc = locate_quote(quote, chunk.text)
            if loc is None:
                stats["unverified_dropped"] += 1
                continue
            topic = (raw.get("topic") or "").strip()
            if topic and topic not in valid_topics:
                # tolerate "Parent / Child" spacing or case differences
                match = next(
                    (p for p in valid_topics if p.lower().replace(" ", "") == topic.lower().replace(" ", "")), ""
                )
                topic = match
            items.append(
                ExtractedItem(
                    knowledge_type=raw.get("knowledge_type") or "fact",
                    subject=(raw.get("subject") or "")[:300].strip(),
                    predicate=(raw.get("predicate") or "")[:200].strip(),
                    object=(raw.get("object") or "").strip(),
                    statement=statement,
                    explanation=(raw.get("explanation") or "").strip(),
                    topic=topic,
                    tags=[t.strip() for t in (raw.get("tags") or []) if isinstance(t, str) and t.strip()][:12],
                    code=(raw.get("code") or None),
                    product_version=(raw.get("product_version") or None),
                    evidence_quote=chunk.text[loc[0] : loc[1]],
                    chunk=chunk,
                    polarity="negative"
                    if (
                        raw.get("polarity") == "negative"
                        or derive_polarity(raw.get("knowledge_type") or "") == "negative"
                    )
                    else "positive",
                    details={k: v for k, v in (raw.get("details") or {}).items() if isinstance(v, str) and v.strip()},
                    quote_start=loc[0],
                    quote_end=loc[1],
                    quote_verified=True,
                    content_hash=statement_hash(statement),
                    extraction={
                        "method": "llm",
                        "extractor_version": PROMPT_VERSION,
                        "chunker_version": CHUNKER_VERSION,
                        "model": model,
                    },
                )
            )
    return items, stats
