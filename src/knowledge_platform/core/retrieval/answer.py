"""Grounded answer generation (§30). Answers cite knowledge item IDs; nothing else is allowed."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from ...models import ItemStatus, KnowledgeItem
from ..extraction.prompts import ANSWER_SYSTEM, ANSWER_USER, ANSWER_USER_PLANNED, REGENERATE_USER
from ..llm_service import call_text
from ..plugins.base import DomainPlugin
from .plan import build_plan, check_completeness, format_plan
from .rank import Scored
from .retrieve import retrieve
from .search import SearchResult, hybrid_search, resolve_text_search_config

# p2 = two-channel retrieval, 8 items, plain prompt (the P2 baseline, kept reproducible)
# p3-retrieval = ADR 0006 retrieval and context, plain prompt (measures retrieval alone)
# p3 = retrieval + plan + completeness check + one targeted regeneration
MODES = ("p2", "p3-retrieval", "p3")
CONTEXT_K = 12  # items handed to the model in p3 mode (p2 kept 8)
MAX_CONTEXT_CHARS = 14000

_CITE = re.compile(r"\[(\d+)\]")


@dataclass
class Answer:
    question: str
    answer: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    retrieved: list[dict[str, Any]] = field(default_factory=list)
    insufficient: bool = False  # not grounded: no citation resolved (UI warning)
    no_results: bool = False  # retrieval returned nothing at all (a genuine abstention, see evaluation)
    mode: str = "p2"
    plan: dict[str, Any] | None = None  # the deterministic answer plan (p3)
    completeness: dict[str, Any] | None = None  # plan coverage of the final answer (p3)
    regeneration: dict[str, Any] | None = None  # why/what was regenerated, if anything (p3)
    retrieval: dict[str, Any] | None = None  # query analysis, channels, timings (p3)
    timings_ms: dict[str, int] = field(default_factory=dict)
    llm_calls: int = 0


_DETAIL_LABELS = {
    "condition": "Condition",
    "workaround": "Workaround",
    "expected_behavior": "Expected behaviour",
    "expected_result": "Expected result",
    "common_mistake": "Common mistake",
    "validation_method": "How to validate",
}


def trust_notes(it: KnowledgeItem) -> list[str]:
    """Why the answer model must not treat an item as ordinary trusted knowledge (P2.2). Empty = none."""
    notes: list[str] = []
    if it.status == ItemStatus.CANDIDATE:
        notes.append("UNVERIFIED CANDIDATE: not yet supported by verified evidence")
    if it.status == ItemStatus.STALE:
        notes.append("STALE: its evidence no longer appears in the current source, may be outdated")
    if it.status == ItemStatus.CONFLICTED:
        notes.append("CONFLICTED: another item contradicts it and the conflict is unresolved")
    if it.needs_review:
        notes.append(f"FLAGGED FOR REVIEW ({it.review_kind or 'manual'}): {it.review_reason or 'no reason given'}")
    if it.needs_revalidation:
        notes.append("AWAITING REVALIDATION: a dependency changed")
    return notes


def _format_items(
    results: list[SearchResult],
    plugin: DomainPlugin | None = None,
    *,
    sources: dict[Any, tuple[str, int, str]] | None = None,
    max_chars: int | None = None,
) -> str:
    """Numbered context blocks. With ``sources`` (p3) each block also names its source, authority and a short
    verified excerpt, so the model sees provenance, not just a statement."""
    lines = []
    used = 0
    for n, r in enumerate(results, start=1):
        it = r.item
        meta = [f"status={it.status}", f"confidence={it.confidence:.2f}"]
        meta.extend(trust_notes(it))
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
        if sources is not None:
            best = next((e for e in it.evidence if e.evidence_type == "extraction" and e.verified), None)
            if best is not None and best.source_id in sources:
                name, authority, klass = sources[best.source_id]
                block += f"\n    Source: {name} (authority {authority}, {klass})"
                if best.excerpt:
                    block += f'\n    Evidence: "{best.excerpt[:160].strip()}"'
            elif any(e.evidence_type == "human" for e in it.evidence):
                block += "\n    Source: provided by a person / organisation"
        if max_chars is not None and used + len(block) > max_chars and lines:
            break
        used += len(block)
        lines.append(block)
    return "\n\n".join(lines)


def answer_question(
    session: Session,
    plugin: DomainPlugin,
    question: str,
    *,
    limit: int | None = None,
    min_confidence: float = 0.0,
    include_candidates: bool = False,
    mode: str = "p3",
    regenerate: bool = True,
) -> Answer:
    """Grounded answer. ``mode="p2"`` is the two-channel retrieval + plain prompt kept for baseline comparison;
    ``mode="p3"`` runs the ADR 0006 pipeline: analysed query, explainable ranking, 12-item context with sources,
    a deterministic plan, a completeness check and at most one targeted regeneration. ``include_candidates``
    adds CANDIDATE items labelled UNVERIFIED in the prompt so the answer states their status."""
    if mode not in MODES:
        raise ValueError(f"unknown answer mode {mode!r}")
    if mode.startswith("p3"):
        return _answer_p3(
            session,
            plugin,
            question,
            k=limit or CONTEXT_K,
            include_candidates=include_candidates,
            regenerate=regenerate and mode == "p3",
            planned=mode == "p3",
        )
    limit = limit or 8
    results = [
        r
        for r in hybrid_search(
            session,
            domain_id=plugin.id,
            query=question,
            limit=limit,
            include_candidates=include_candidates,
            text_search_config=plugin.text_search_config(),
        )
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
            "trust_notes": trust_notes(r.item),
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
        question=question,
        answer=text.strip(),
        citations=citations,
        retrieved=retrieved,
        insufficient=insufficient,
        mode="p2",
        llm_calls=1,
    )


def _retrieved_rows(selected: list[Scored]) -> list[dict[str, Any]]:
    return [
        {
            "n": n,
            "id": str(s.item.id),
            "statement": s.item.statement,
            "status": s.item.status,
            "confidence": s.item.confidence,
            "topic": s.item.topic,
            "score": s.score,
            "trust_notes": trust_notes(s.item),
            "signals": {k: round(v, 5) for k, v in s.signals.items()},
            "explanation": list(s.explanation),
            "expanded_from": s.expanded_from,
        }
        for n, s in enumerate(selected, start=1)
    ]


def _answer_p3(
    session: Session,
    plugin: DomainPlugin,
    question: str,
    *,
    k: int,
    include_candidates: bool,
    regenerate: bool,
    planned: bool = True,
) -> Answer:
    import time

    from .rank import source_facts

    t0 = time.perf_counter()
    r = retrieve(session, plugin, question, k=k, include_candidates=include_candidates)
    config = resolve_text_search_config(session, plugin.text_search_config())
    timings = dict(r.timings_ms)
    retrieved = _retrieved_rows(r.selected)
    if not r.selected:
        return Answer(
            question=question,
            answer="The knowledge repository does not contain verified information relevant to this question yet.",
            retrieved=[],
            insufficient=True,
            no_results=True,
            mode="p3" if planned else "p3-retrieval",
            retrieval=r.summary(),
            timings_ms={**timings, "total": int((time.perf_counter() - t0) * 1000)},
        )
    t1 = time.perf_counter()
    facts = source_facts(session, plugin.id)
    names = {
        s_id: (name, auth, klass)
        for s_id, (auth, klass, name) in ((sid, (a, c, _source_name(session, sid))) for sid, (a, c) in facts.items())
    }
    results = r.as_results()
    items_text = _format_items(results, plugin, sources=names, max_chars=MAX_CONTEXT_CHARS)
    plan = build_plan(session, r.analysis, r.selected, config=config, plugin=plugin)
    timings["context"] = int((time.perf_counter() - t1) * 1000)
    system = ANSWER_SYSTEM.format(domain_name=plugin.name)
    plan_text = format_plan(plan) if planned else ""
    user = (
        ANSWER_USER_PLANNED.format(question=question, items=items_text, plan=plan_text)
        if plan_text
        else ANSWER_USER.format(question=question, items=items_text)
    )
    t2 = time.perf_counter()
    text_ = call_text(purpose="answer", system=system, user=user, session=session)
    timings["generation"] = int((time.perf_counter() - t2) * 1000)
    llm_calls = 1
    completeness = check_completeness(session, config, text_, plan)
    regeneration: dict[str, Any] | None = None
    if regenerate and not completeness["ok"]:
        missing = completeness["missing_must"] + completeness["missing_should"]
        # only concepts the evidence actually supports can be asked for — they all carry item numbers by construction
        supported = [m for m in missing if m["evidence"]]
        if supported:
            t3 = time.perf_counter()
            missing_text = "\n".join(
                f"- {m['term']} (see {', '.join(f'[{n}]' for n in m['evidence'])})" for m in supported
            )
            user2 = REGENERATE_USER.format(question=question, items=items_text, answer=text_, missing=missing_text)
            text2 = call_text(purpose="answer", system=system, user=user2, session=session)
            llm_calls += 1
            after = check_completeness(session, config, text2, plan)
            cited2 = {int(m) for m in _CITE.findall(text2) if 0 < int(m) <= len(results)}
            cited1 = {int(m) for m in _CITE.findall(text_) if 0 < int(m) <= len(results)}
            accepted = bool(cited2) and (after["score"] or 0) > (completeness["score"] or 0)
            regeneration = {
                "triggered": True,
                "reason": "missing planned concepts supported by the evidence",
                "missing": [m["term"] for m in supported],
                "evidence": sorted({n for m in supported for n in m["evidence"]}),
                "before": {"score": completeness["score"], "citations": sorted(cited1)},
                "after": {"score": after["score"], "citations": sorted(cited2)},
                "accepted": accepted,
                "latency_ms": int((time.perf_counter() - t3) * 1000),
            }
            if accepted:
                text_, completeness = text2, after
            timings["regeneration"] = regeneration["latency_ms"]
        else:
            regeneration = {"triggered": False, "reason": "incomplete but nothing missing is supported by the evidence"}
    cited = sorted({int(m) for m in _CITE.findall(text_) if 0 < int(m) <= len(results)})
    citations = [retrieved[n - 1] for n in cited]
    timings["total"] = int((time.perf_counter() - t0) * 1000)
    return Answer(
        question=question,
        answer=text_.strip(),
        citations=citations,
        retrieved=retrieved,
        insufficient=not cited,
        mode="p3" if planned else "p3-retrieval",
        plan=plan.as_dict(),
        completeness=completeness,
        regeneration=regeneration,
        retrieval=r.summary(),
        timings_ms=timings,
        llm_calls=llm_calls,
    )


_source_name_cache: dict[Any, str] = {}


def _source_name(session: Session, source_id: Any) -> str:
    from ...models import Source

    if source_id not in _source_name_cache:
        src = session.get(Source, source_id)
        _source_name_cache[source_id] = src.name if src else "unknown source"
    return _source_name_cache[source_id]
