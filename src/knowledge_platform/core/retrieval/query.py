"""Query analysis (ADR 0006, stages 1–2): normalisation, lexical variants, lexemes, entities and intent.

Everything here is deterministic and domain-independent. The only domain knowledge it uses is what the plugin
declares (text-search configuration, intent cues, synonyms) and what the domain's own knowledge base contains
(subjects, for entity detection). No model call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ...models import ItemStatus, KnowledgeItem

_WS = re.compile(r"\s+")
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+#-]*")
_UPPER = re.compile(r"^[A-Z][A-Z0-9_.]{2,}$")  # CALCULATE, ALLSELECTED, INFO.VIEW
_CAMEL = re.compile(r"^[A-Z][a-z]+(?:[A-Z][A-Za-z0-9]+)+$")  # RangeStart, DirectQuery
_VERSION = re.compile(r"\b(?:v(?:ersion)?\s*)?(\d{4}|\d+\.\d+(?:\.\d+)?)\b", re.IGNORECASE)
# generic prefixes that are written hyphenated, joined or split depending on the author ("bi-directional",
# "bidirectional", "bi directional"); language-agnostic enough to live in core, extensible per plugin
COMPOUND_PREFIXES = ("anti", "bi", "cross", "multi", "non", "semi")
LIVE_STATUSES = (ItemStatus.SUPPORTED, ItemStatus.VERIFIED, ItemStatus.CONFLICTED, ItemStatus.STALE)

# core intent vocabulary and default cues (English); a plugin extends or overrides per intent
INTENTS = (
    "definition",
    "procedure",
    "troubleshooting",
    "comparison",
    "syntax",
    "limitation",
    "configuration",
    "architecture",
    "conceptual",
    "example",
    "error",
    "version",
)
DEFAULT_INTENT_CUES: dict[str, list[str]] = {
    "definition": [r"^what (is|are|does|do)\b", r"\bdefin(e|ition)\b", r"\bmean(s|ing)?\b"],
    "procedure": [r"^how (do|to|can|should)\b", r"\bsteps?\b", r"\bset ?up\b", r"\bcreate\b", r"\bconfigure\b"],
    "troubleshooting": [
        r"\btroubleshoot",
        r"\bnot working\b",
        r"\bfails?\b",
        r"\b(isn't|doesn't|won't|can't|does not|is not) (work|refresh|load|connect|start|appear)",
    ],
    "comparison": [r"\b(difference|differ|versus|vs\.?|compared? (to|with)|instead of|rather than)\b"],
    "syntax": [r"\bsyntax\b", r"\bsignature\b", r"\bparameters?\b", r"\barguments?\b"],
    "limitation": [r"\b(risks?|limitations?|restrictions?|caveats?|drawbacks?|cannot|can't|not (supported|allowed))\b"],
    "configuration": [r"\b(setting|configuration|option|enable|disable)s?\b"],
    "architecture": [r"\b(architecture|design|schema|topology|components?)\b"],
    "conceptual": [
        r"^why\b",
        r"^how (does|do|is|are)\b",
        r"^when (is|are|should|do|does)\b",
        r"\bexplain\b",
        r"\bconcept\b",
        r"\brecommended\b",
    ],
    "example": [r"\bexamples?\b", r"\bsample\b", r"\bshow me\b"],
    "error": [r"\berror\b", r"\bexception\b", r"\bmessage\b"],
    "version": [r"\bversion\b", r"\bsince\b", r"\bavailable in\b", r"\bonly (available|in)\b", r"\bdeprecated\b"],
}
# which declared roles / polarities / conventional type names an intent prefers (plugin may override)
DEFAULT_INTENT_TYPES: dict[str, dict[str, list[str]]] = {
    "definition": {"roles": ["foundation"], "types": ["definition"]},
    "conceptual": {"roles": ["foundation"], "types": ["definition", "fact", "best_practice"]},
    "procedure": {"roles": ["dependent"], "types": ["procedure", "best_practice"]},
    "configuration": {"roles": ["dependent"], "types": ["procedure"]},
    "limitation": {"polarity": ["negative"]},
    "troubleshooting": {"polarity": ["negative"], "types": ["procedure"]},
    "error": {"polarity": ["negative"]},
    "example": {"roles": ["example"]},
    "syntax": {"roles": ["foundation"], "types": ["definition", "example"]},
    "version": {"types": ["fact", "limitation"]},
    "comparison": {"roles": ["foundation"], "types": ["definition", "fact", "best_practice"]},
    "architecture": {"roles": ["foundation"], "types": ["definition", "best_practice"]},
}


@dataclass
class Entity:
    text: str  # as written in the query
    canonical: str  # the subject spelling in the knowledge base (or the query spelling)
    kind: str  # subject | identifier
    items: int = 0  # live items with this subject

    @property
    def weight(self) -> float:
        """How much an exact-subject match on this entity should count: identifiers and multi-word subjects
        fully; a common single word (the domain name, "number") less, and the more items share the subject the
        less specific it is (1 / (1 + log10(items)))."""
        import math

        specificity = 1.0 / (1.0 + math.log10(max(self.items, 1)))
        if self.kind == "identifier" or " " in self.text or "-" in self.text:
            return specificity
        return specificity * 0.5


@dataclass
class QueryAnalysis:
    query: str
    normalized: str
    tokens: list[str]
    variants: list[str]  # lexical variants worth searching (hyphenation, prefix compounds, synonyms)
    lexemes: list[str]  # stemmed, stop-word-free terms of the query (domain text-search config)
    entities: list[Entity]
    intent: str | None
    intent_cues: list[str] = field(default_factory=list)
    version: str | None = None

    def entity_names(self) -> set[str]:
        return {e.canonical.lower() for e in self.entities}

    def as_dict(self) -> dict[str, Any]:
        return {
            "normalized": self.normalized,
            "lexemes": self.lexemes,
            "variants": self.variants,
            "entities": [e.__dict__ for e in self.entities],
            "intent": self.intent,
            "intent_cues": self.intent_cues,
            "version": self.version,
        }


# ----------------------------------------------------------------------------- stage 1: normalisation


def normalize(text_: str) -> str:
    return _WS.sub(" ", (text_ or "").strip().lower())


def tokens(text_: str) -> list[str]:
    return [t.strip(".") for t in _TOKEN.findall(text_ or "") if t.strip(".")]


def compound_variants(token: str) -> set[str]:
    """Spellings the corpus may use for one token: 'bi-directional' → 'bidirectional', 'bi directional';
    'bidirectional' → 'bi-directional', 'bi directional'; 'row-level' → 'rowlevel', 'row level'."""
    t = token.lower()
    out: set[str] = set()
    if "-" in t:
        parts = [p for p in t.split("-") if p]
        out.add("".join(parts))
        out.add(" ".join(parts))
    else:
        for p in COMPOUND_PREFIXES:
            rest = t[len(p) :]
            if t.startswith(p) and len(rest) >= 6 and rest.isalpha():
                out.add(f"{p}-{rest}")
                out.add(f"{p} {rest}")
    out.discard(t)
    return out


def lexical_variants(query: str, synonyms: dict[str, list[str]] | None = None) -> list[str]:
    """Alternative spellings/synonyms of query terms (deterministic; plugin synonyms are declared, not guessed)."""
    variants: list[str] = []
    seen: set[str] = set()
    low = normalize(query)
    for tok in tokens(query):
        for v in sorted(compound_variants(tok)):
            if v not in seen:
                seen.add(v)
                variants.append(v)
    for term, alts in (synonyms or {}).items():
        t = term.lower()
        if re.search(rf"\b{re.escape(t)}\b", low) or any(re.search(rf"\b{re.escape(a.lower())}\b", low) for a in alts):
            for cand in [t, *(a.lower() for a in alts)]:
                if cand not in seen and not re.search(rf"\b{re.escape(cand)}\b", low):
                    seen.add(cand)
                    variants.append(cand)
    return variants


def lexemes(session: Session, config: str, text_: str) -> list[str]:
    """Stemmed, stop-word-free lexemes of ``text_`` under the PostgreSQL configuration (position order)."""
    if not text_.strip():
        return []
    raw = session.execute(
        text("SELECT to_tsvector(CAST(:cfg AS regconfig), :t)::text"), {"cfg": config, "t": text_}
    ).scalar_one()
    positioned: list[tuple[int, str]] = []
    for m in re.finditer(r"'((?:[^']|'')+)':([\d,]+)", raw or ""):
        lex = m.group(1).replace("''", "'")
        first = int(m.group(2).split(",")[0])
        positioned.append((first, lex))
    out: list[str] = []
    for _, lex in sorted(positioned):
        if lex not in out:
            out.append(lex)
    return out


# ----------------------------------------------------------------------------- stage 2: entities and intent


def detect_entities(session: Session, domain_id: str, query: str, *, max_ngram: int = 4) -> list[Entity]:
    """Entities = query n-grams that are subjects of live knowledge in this domain (case-insensitive), plus
    identifier-looking tokens (ALLCAPS, CamelCase) even when no item is filed under them yet."""
    toks = tokens(query)
    grams: dict[str, str] = {}  # lower -> as written
    for n in range(1, max_ngram + 1):
        for i in range(0, len(toks) - n + 1):
            g = " ".join(toks[i : i + n])
            if len(g) >= 2:
                grams.setdefault(g.lower(), g)
    if not grams:
        return []
    rows = session.execute(
        select(func.lower(KnowledgeItem.subject), func.min(KnowledgeItem.subject), func.count())
        .where(
            KnowledgeItem.domain_id == domain_id,
            KnowledgeItem.status.in_([s.value for s in LIVE_STATUSES]),
            func.lower(KnowledgeItem.subject).in_(list(grams)),
        )
        .group_by(func.lower(KnowledgeItem.subject))
    ).all()
    found: dict[str, Entity] = {}
    for low, canonical, count in rows:
        found[low] = Entity(text=grams[low], canonical=canonical, kind="subject", items=int(count))
    identifiers = {t.lower(): t for t in toks if _UPPER.match(t) or _CAMEL.match(t)}
    # longer matches subsume shorter ones ("on-premises data gateway" over "gateway") — except identifiers
    # (CALCULATE stays an entity next to "CALCULATE function")
    for low in list(found):
        if low not in identifiers and any(other != low and low in other for other in found):
            del found[low]
    for low, tok in identifiers.items():
        if low not in found:
            found[low] = Entity(text=tok, canonical=tok, kind="identifier", items=0)
        else:
            found[low].kind = "identifier"
    return sorted(found.values(), key=lambda e: (-len(e.text), e.text))


def classify_intent(query: str, cues: dict[str, list[str]] | None = None) -> tuple[str | None, list[str]]:
    """First intent whose cue matches, in a fixed priority order; unknown → None (no preference)."""
    q = normalize(query)
    merged: dict[str, list[str]] = {k: list(v) for k, v in DEFAULT_INTENT_CUES.items()}
    for k, v in (cues or {}).items():
        merged.setdefault(k, []).extend(v)
    # specific intents before the generic "definition"/"conceptual" openers
    order = [
        "limitation",
        "comparison",
        "example",
        "syntax",
        "version",
        "error",
        "troubleshooting",
        "procedure",
        "configuration",
        "architecture",
        "definition",
        "conceptual",
    ]
    for intent in order:
        matched = [c for c in merged.get(intent, []) if re.search(c, q)]
        if matched:
            return intent, matched
    return None, []


def analyze(
    session: Session,
    *,
    domain_id: str,
    query: str,
    text_search_config: str,
    synonyms: dict[str, list[str]] | None = None,
    intent_cues: dict[str, list[str]] | None = None,
) -> QueryAnalysis:
    normalized = normalize(query)
    variants = lexical_variants(query, synonyms)
    lex = lexemes(session, text_search_config, " ".join([query, *variants]))
    entities = detect_entities(session, domain_id, query)
    intent, matched = classify_intent(query, intent_cues)
    version = None
    m = _VERSION.search(query)
    if m and "version" in normalized:
        version = m.group(1)
    return QueryAnalysis(
        query=query,
        normalized=normalized,
        tokens=tokens(query),
        variants=variants,
        lexemes=lex,
        entities=entities,
        intent=intent,
        intent_cues=matched,
        version=version,
    )


__all__ = [
    "COMPOUND_PREFIXES",
    "DEFAULT_INTENT_CUES",
    "DEFAULT_INTENT_TYPES",
    "INTENTS",
    "Entity",
    "QueryAnalysis",
    "analyze",
    "classify_intent",
    "compound_variants",
    "detect_entities",
    "lexemes",
    "lexical_variants",
    "normalize",
    "tokens",
]
