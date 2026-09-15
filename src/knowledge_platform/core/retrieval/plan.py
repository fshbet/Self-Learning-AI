"""Deterministic answer plan and completeness check (ADR 0006, stages 8–9).

The plan says what a complete answer must and should cover, derived only from things that exist: the query's
entities (must — when at least one selected item carries them), and terms the selected evidence itself keeps
returning to (should — lexeme bigrams/unigrams recurring across several items). Nothing is invented: every
concept in the plan names the evidence items ``[n]`` that support it, and a concept that no item supports is
never planned. No model call.

Completeness compares in lexeme space (the domain's text-search configuration: case-, inflection- and
stop-word-insensitive), with entity aliases (declared synonyms, hyphenation variants) counting as the entity —
never a bare substring test. The evaluator's golden ``required_concepts`` are *not* consulted: they are for
measuring, not for steering.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from .query import QueryAnalysis, normalize
from .rank import Scored

_LEXPOS = re.compile(r"'((?:[^']|'')+)':([\d,]+)")
MAX_MUST = 4
MAX_SHOULD = 4
MIN_SHOULD_ITEMS = 2  # a should-cover term must recur in at least this many selected items


@dataclass
class Concept:
    term: str  # human-readable surface form
    key: str  # lexeme form used for matching ("fact tabl")
    aliases: list[str] = field(default_factory=list)  # other lower-case surface forms that satisfy it
    evidence: list[int] = field(default_factory=list)  # [n] of the context items carrying it
    kind: str = "must"  # must | should

    def as_dict(self) -> dict[str, Any]:
        return {
            "term": self.term,
            "key": self.key,
            "aliases": self.aliases,
            "evidence": self.evidence,
            "kind": self.kind,
        }


@dataclass
class AnswerPlan:
    intent: str | None
    entities: list[str]
    must_cover: list[Concept]
    should_cover: list[Concept]
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "entities": self.entities,
            "must_cover": [c.as_dict() for c in self.must_cover],
            "should_cover": [c.as_dict() for c in self.should_cover],
            "notes": self.notes,
        }


def lexeme_positions(session: Session, config: str, text_: str) -> list[tuple[int, str]]:
    """(position, lexeme) pairs in position order — bigrams need adjacency, which plain lexeme sets lose."""
    if not text_ or not text_.strip():
        return []
    raw = session.execute(
        text("SELECT to_tsvector(CAST(:cfg AS regconfig), :t)::text"), {"cfg": config, "t": text_}
    ).scalar_one()
    out: list[tuple[int, str]] = []
    for m in _LEXPOS.finditer(raw or ""):
        lex = m.group(1).replace("''", "'")
        for pos in m.group(2).split(","):
            out.append((int(pos), lex))
    return sorted(out)


def _grams(positions: list[tuple[int, str]]) -> tuple[set[str], set[str]]:
    """(unigrams, bigrams of adjacent positions) as lexeme strings."""
    uni = {lex for _, lex in positions}
    bi: set[str] = set()
    by_pos: dict[int, list[str]] = {}
    for pos, lex in positions:
        by_pos.setdefault(pos, []).append(lex)
    for pos, lexs in by_pos.items():
        for lx in lexs:
            for nxt in by_pos.get(pos + 1, []):
                bi.add(f"{lx} {nxt}")
    return uni, bi


def _surface(key: str, texts: list[str]) -> str:
    """A human-readable form of a lexeme key found in the evidence text ("fact tabl" → "fact table")."""
    parts = key.split(" ")

    def pat(lex: str) -> str:
        stem = re.escape(lex)
        alt = re.escape(lex[:-1] + "y") if lex.endswith("i") else None
        core = f"(?:{stem}|{alt})" if alt else stem
        return rf"\b{core}\w*"

    rx = re.compile(r"\s+".join(pat(p) for p in parts), re.IGNORECASE)
    for t in texts:
        m = rx.search(t)
        if m:
            return m.group(0).lower()
    return key


def build_plan(
    session: Session, analysis: QueryAnalysis, selected: list[Scored], *, config: str, plugin: Any = None
) -> AnswerPlan:
    n_of: dict[str, int] = {str(s.item.id): n for n, s in enumerate(selected, start=1)}
    texts = [f"{s.item.subject}. {s.item.statement}" for s in selected]
    lower = [t.lower() for t in texts]
    must: list[Concept] = []
    for ent in analysis.entities:
        names = [ent.canonical.lower(), ent.text.lower(), *ent.aliases]
        carrying = [
            n_of[str(s.item.id)] for s, low in zip(selected, lower, strict=True) if any(nm in low for nm in names)
        ]
        if carrying and not ent.common:
            must.append(
                Concept(
                    term=ent.canonical,
                    key=ent.canonical.lower(),
                    aliases=sorted({nm for nm in names if nm != ent.canonical.lower()}),
                    evidence=carrying[:6],
                    kind="must",
                )
            )
    must = must[:MAX_MUST]
    # recurring evidence terms: what the items themselves keep coming back to (bigrams preferred)
    query_uni = set(analysis.lexemes)
    per_item: list[tuple[set[str], set[str]]] = []
    for s in selected:
        per_item.append(_grams(lexeme_positions(session, config, f"{s.item.subject}. {s.item.statement}")))
    bi_count: Counter[str] = Counter()
    uni_count: Counter[str] = Counter()
    for uni, bi in per_item:
        bi_count.update(bi)
        uni_count.update(uni)
    must_keys = {c.key for c in must}
    should: list[Concept] = []
    for gram, cnt in bi_count.most_common():
        a, b = gram.split(" ")
        if cnt < MIN_SHOULD_ITEMS or len(a) < 3 or len(b) < 3:
            continue
        if a in query_uni and b in query_uni:
            continue  # the question already says it
        term = _surface(gram, texts)
        if term in must_keys or any(term in c.key or c.key in term for c in must):
            continue
        ev = [n_of[str(s.item.id)] for s, (_, bi) in zip(selected, per_item, strict=True) if gram in bi]
        should.append(Concept(term=term, key=gram, evidence=ev[:6], kind="should"))
        if len(should) >= MAX_SHOULD:
            break
    if len(should) < MAX_SHOULD:
        for lex, cnt in uni_count.most_common():
            if cnt < MIN_SHOULD_ITEMS + 1 or len(lex) < 4 or lex in query_uni:
                continue
            if any(lex in c.key for c in should) or any(lex in c.key for c in must):
                continue
            term = _surface(lex, texts)
            ev = [n_of[str(s.item.id)] for s, (uni, _) in zip(selected, per_item, strict=True) if lex in uni]
            should.append(Concept(term=term, key=lex, evidence=ev[:6], kind="should"))
            if len(should) >= MAX_SHOULD:
                break
    notes: list[str] = []
    if any(s.item.polarity == "negative" for s in selected):
        notes.append("state the limitations / warnings the items carry when they bear on the question")
    if analysis.intent == "listing":
        notes.append("list every member the items name; do not stop at the first")
    if analysis.intent in ("procedure", "configuration"):
        notes.append("give the steps in order")
    if any(s.item.status in ("STALE", "CONFLICTED") or s.item.needs_review for s in selected):
        notes.append("say explicitly which cited items are stale, conflicted or flagged")
    return AnswerPlan(
        intent=analysis.intent,
        entities=[e.canonical for e in analysis.entities],
        must_cover=must,
        should_cover=should,
        notes=notes,
    )


def check_completeness(session: Session, config: str, answer: str, plan: AnswerPlan) -> dict[str, Any]:
    """Which planned concepts the answer covers — in lexeme space, with aliases; never a bare substring."""
    uni, bi = _grams(lexeme_positions(session, config, answer))
    low = normalize(answer)

    def covered(c: Concept) -> bool:
        if c.kind == "must":
            if any(nm in low for nm in [c.key, *c.aliases]):
                return True
            ent_uni, ent_bi = _grams(lexeme_positions(session, config, c.term))
            return bool(ent_bi and ent_bi <= bi) or bool(not ent_bi and ent_uni and ent_uni <= uni)
        return c.key in bi if " " in c.key else c.key in uni

    missing_must = [c for c in plan.must_cover if not covered(c)]
    missing_should = [c for c in plan.should_cover if not covered(c)]
    total = len(plan.must_cover) + len(plan.should_cover)
    score = (total - len(missing_must) - len(missing_should)) / total if total else None
    return {
        "ok": not missing_must and len(missing_should) < max(1, (len(plan.should_cover) + 1) // 2),
        "score": None if score is None else round(score, 3),
        "missing_must": [c.as_dict() for c in missing_must],
        "missing_should": [c.as_dict() for c in missing_should],
        "planned": total,
    }


def format_plan(plan: AnswerPlan) -> str:
    """The plan as prompt text: what to cover and where the support is."""
    lines: list[str] = []
    if plan.intent:
        lines.append(f"Question type: {plan.intent}.")
    if plan.must_cover:
        lines.append(
            "Must address: "
            + "; ".join(f"{c.term} (items {', '.join(f'[{n}]' for n in c.evidence)})" for c in plan.must_cover)
        )
    if plan.should_cover:
        lines.append(
            "The items also establish these points — include them when they bear on the question: "
            + "; ".join(f"{c.term} ({', '.join(f'[{n}]' for n in c.evidence)})" for c in plan.should_cover)
        )
    lines.extend(f"- {n}" for n in plan.notes)
    return "\n".join(lines)


__all__ = ["AnswerPlan", "Concept", "build_plan", "check_completeness", "format_plan", "lexeme_positions"]
