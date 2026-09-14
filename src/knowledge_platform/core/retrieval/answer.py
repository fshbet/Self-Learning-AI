"""Grounded answer generation (§30). Answers cite knowledge item IDs; nothing else is allowed."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from ..extraction.prompts import ANSWER_SYSTEM, ANSWER_USER
from ..llm_service import call_text
from ..plugins.base import DomainPlugin
from .search import SearchResult, hybrid_search

_CITE = re.compile(r"\[(\d+)\]")


@dataclass
class Answer:
    question: str
    answer: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    retrieved: list[dict[str, Any]] = field(default_factory=list)
    insufficient: bool = False  # not grounded: no citation resolved (UI warning)
    no_results: bool = False  # retrieval returned nothing at all (a genuine abstention, see evaluation)


_DETAIL_LABELS = {
    "condition": "Condition",
    "workaround": "Workaround",
    "expected_behavior": "Expected behaviour",
    "expected_result": "Expected result",
    "common_mistake": "Common mistake",
    "validation_method": "How to validate",
}


def _format_items(results: list[SearchResult], plugin: DomainPlugin | None = None) -> str:
    lines = []
    for n, r in enumerate(results, start=1):
        it = r.item
        meta = [f"status={it.status}", f"confidence={it.confidence:.2f}"]
        if it.product_version:
            meta.append(f"version={it.product_version}")
        if it.publication_date:
            meta.append(f"date={it.publication_date}")
        label = ""
        if it.polarity == "negative":
            label = f"{it.knowledge_type.replace('_', '-').upper()}: "
        elif plugin is not None and plugin.role_of(it.knowledge_type) == "example":
            label = "EXAMPLE: "
        block = f"[{n}] ({', '.join(meta)}) {label}{it.statement}"
        if it.explanation:
            block += f"\n    Explanation: {it.explanation[:500]}"
        if it.code:
            block += f"\n    Code:\n{it.code[:800]}"
        for key, name in _DETAIL_LABELS.items():
            if (it.details or {}).get(key):
                block += f"\n    {name}: {str(it.details[key])[:300]}"
        lines.append(block)
    return "\n\n".join(lines)


def answer_question(
    session: Session, plugin: DomainPlugin, question: str, *, limit: int = 8, min_confidence: float = 0.0
) -> Answer:
    results = [
        r
        for r in hybrid_search(session, domain_id=plugin.id, query=question, limit=limit)
        if r.item.confidence >= min_confidence
    ]
    retrieved = [
        {
            "n": n,
            "id": str(r.item.id),
            "statement": r.item.statement,
            "status": r.item.status,
            "confidence": r.item.confidence,
            "topic": r.item.topic,
            "score": r.score,
        }
        for n, r in enumerate(results, start=1)
    ]
    if not results:
        return Answer(
            question=question,
            answer="The knowledge repository does not contain verified information relevant to this question yet.",
            insufficient=True,
            no_results=True,
        )
    system = ANSWER_SYSTEM.format(domain_name=plugin.name)
    user = ANSWER_USER.format(question=question, items=_format_items(results, plugin))
    text = call_text(purpose="answer", system=system, user=user, session=session)
    cited = sorted({int(m) for m in _CITE.findall(text) if 0 < int(m) <= len(results)})
    citations = [retrieved[n - 1] for n in cited]
    insufficient = not cited
    return Answer(
        question=question, answer=text.strip(), citations=citations, retrieved=retrieved, insufficient=insufficient
    )
