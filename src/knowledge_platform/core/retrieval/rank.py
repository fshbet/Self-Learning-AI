"""Explainable ranking, diversity and bounded expansion (ADR 0006, stages 4–6).

Every ranked candidate carries ``signals`` — the numeric contribution of each signal — and a human-readable
``explanation``, so "why did this rank above that?" is answerable from the data. Relevance signals (entity,
concept coverage, channels) dominate; authority, verification and source class are tie-breakers that can never
lift an irrelevant item over a relevant one (their sum is below one entity match). Trust states are penalised,
never excluded: a CONFLICTED or STALE item that is the best match still surfaces, labelled.

All domain knowledge comes from the plugin: roles / polarity of knowledge types (intent affinity), source
authority and class (stored on sources), declared intent→type preferences.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ...models import ItemStatus, KnowledgeItem, Source
from .candidates import Candidate
from .query import DEFAULT_INTENT_TYPES, QueryAnalysis

RRF_K = 60
# weights: relevance first, then intent, then trust and provenance as tie-breakers
FUSION_K = 15  # 1/(K + rank): rank 1 → 0.0625, rank 8 → 0.043, rank 30 → 0.022
W = {
    "fusion": 0.6,  # best-rank fusion of the vector, lexical and rare-term channels (≈ 0.005–0.045)
    "entity_subject": 0.020,  # the item's subject is a query entity (scaled by entity specificity)
    "entity_mention": 0.005,  # a query entity appears in the statement
    "concept_coverage": 0.020,  # idf-weighted share of the query's lexemes found in the item (its own lexemes)
    "intent_affinity": 0.006,  # knowledge type / polarity / role preferred by the intent
    "type_match": 0.006,  # the question names the item's declared knowledge type ("which hazard", "limitations of")
    "member": 0.006,  # listing intent: the subject looks like an identifier (a member of the listed set)
    "topic_affinity": 0.008,  # filed under a taxonomy path whose name terms all occur in the query
    "authority": 0.005,  # max source authority 0–100 → 0–1
    "verification": 0.002,  # verification level 0–4 → 0–1
    "official": 0.001,  # at least one official-class source
    "version_match": 0.004,
    "version_mismatch": -0.006,
    "candidate": -0.008,
    "stale": -0.006,
    "conflicted": -0.004,
    "needs_review": -0.004,
    "needs_revalidation": -0.002,
    "validator_failed": -0.004,  # a domain validator rejected the item's example / claim
}
_WORD = re.compile(r"[a-z0-9][a-z0-9+#.-]*")


@dataclass
class Scored:
    candidate: Candidate
    score: float = 0.0
    signals: dict[str, float] = field(default_factory=dict)
    explanation: list[str] = field(default_factory=list)
    expanded_from: str | None = None  # entity that pulled this item in via relationship expansion

    @property
    def item(self) -> KnowledgeItem:
        return self.candidate.item


def _stem_tokens(text_: str) -> set[str]:
    """Cheap token normalisation for coverage/diversity: lower-case words, crude suffix stripping."""
    out = set()
    for w in _WORD.findall((text_ or "").lower()):
        for suf in ("ing", "ies", "es", "s", "ed"):
            if len(w) > 5 and w.endswith(suf):
                w = w[: -len(suf)] + ("y" if suf == "ies" else "")
                break
        out.add(w)
    return out


_IDENTIFIER = re.compile(r"^(?:[A-Z][A-Z0-9_.]{2,}|[A-Z][a-z]+(?:[A-Z][A-Za-z0-9]+)+)(?: \(.*\))?$")


def _matching_taxonomy_paths(plugin: Any, query_lexemes: set[str], weights: dict[str, float]) -> list[str]:
    """Lower-cased taxonomy paths whose last segment's content words all appear in the query lexemes — and name
    something specific: a multi-word leaf ("Bidirectional filtering", "Star schema") or a single rare word.
    A one-word leaf made of a common term ("Functions", "Visuals") would tag half the corpus."""
    try:
        paths = plugin.taxonomy_paths()
    except Exception:
        return []
    out: list[str] = []
    for path in paths:
        leaf = path.split("/")[-1]
        terms = {t for t in _stem_tokens(leaf) if len(t) > 2}
        if not terms:
            continue
        # compare crude stems with PostgreSQL lexemes by prefix in both directions ("bidirect" ~ "bidirectional")
        matched = {t: next((lx for lx in query_lexemes if t.startswith(lx) or lx.startswith(t)), None) for t in terms}
        if any(m is None for m in matched.values()):
            continue
        specific = len(terms) >= 2 or all(weights.get(m, 0.0) >= 4.0 for m in matched.values())
        if specific:
            out.append(path.lower())
    return out


def source_facts(session: Session, domain_id: str) -> dict[Any, tuple[int, str]]:
    """source id -> (authority, source_class) for the domain, fetched once per retrieval."""
    return {
        s.id: (int(s.authority), s.source_class)
        for s in session.execute(select(Source).where(Source.domain_id == domain_id)).scalars()
    }


def intent_preference(plugin: Any, intent: str | None) -> dict[str, list[str]]:
    """Types, roles and polarities the intent prefers — plugin override first, else core defaults."""
    if not intent:
        return {}
    declared = {}
    try:
        declared = plugin.manifest.retrieval.intent_types or {}
    except AttributeError:
        declared = {}
    pref = dict(DEFAULT_INTENT_TYPES.get(intent, {}))
    if intent in declared:
        pref = {"types": list(declared[intent])}
    return pref


def _affine(item: KnowledgeItem, pref: dict[str, list[str]], plugin: Any) -> bool:
    if not pref:
        return False
    if item.knowledge_type in pref.get("types", []):
        return True
    if item.polarity in pref.get("polarity", []):
        return True
    roles = pref.get("roles", [])
    if roles and plugin is not None:
        try:
            return plugin.role_of(item.knowledge_type) in roles
        except Exception:
            return False
    return False


def score_candidates(
    candidates: dict[str, Candidate],
    analysis: QueryAnalysis,
    *,
    plugin: Any,
    sources: dict[Any, tuple[int, str]],
    rrf_k: int = RRF_K,
    stage: str = "full",
    type_terms: dict[str, set[str]] | None = None,
) -> list[Scored]:
    """``stage="rrf"`` reproduces the previous two-channel ranking for measurement; ``"full"`` adds the signals.
    ``type_terms`` maps each declared knowledge type to the lexemes of its name / label / prefix (computed by the
    caller under the domain's text-search configuration) so a question that names a type can prefer it."""
    entity_weight = {e.canonical.lower(): e.weight for e in analysis.entities}
    entity_prefix = {e.canonical.lower(): e.weight for e in analysis.entities if e.kind == "prefix"}
    entity_texts = {e.canonical.lower(): e.weight for e in analysis.entities if not e.common}
    for e in analysis.entities:  # "RLS" counts as a mention of "row-level security"
        if not e.common:
            for alias in e.aliases:
                entity_texts.setdefault(alias, e.weight)
    # every content-bearing query term counts for coverage (entity words included: an item that names the entity
    # *and* the rest of the question beats one that only carries the entity as its subject)
    weights = analysis.term_weights or {lx: 1.0 for lx in analysis.lexemes}
    concept_terms = {lx for lx in analysis.lexemes if len(lx) > 1}
    total_weight = sum(weights.get(lx, 1.0) for lx in concept_terms) or 1.0
    pref = intent_preference(plugin, analysis.intent)
    # what kind of item answers this intent: "what is X" → the item filed under X; "which/when/risks of X" →
    # items *about* X filed under other subjects. Recorded in the explanation.
    about_subject = analysis.intent in (None, "definition", "conceptual", "syntax", "comparison", "architecture")
    subject_factor = 1.0 if about_subject else 0.5
    mention_factor = 0.4 if about_subject else 0.8
    if analysis.intent == "listing":  # "which X are ...": the answers are items *about* members, not about X
        subject_factor, mention_factor = 0.2, 1.2
    # taxonomy affinity: a declared taxonomy path whose own name terms all occur in the query names the area the
    # question is about ("Data Modeling/Bidirectional filtering" for "...bidirectional cross-filtering")
    query_lex = set(analysis.lexemes)
    matched_paths = _matching_taxonomy_paths(plugin, query_lex, weights)
    out: list[Scored] = []
    for c in candidates.values():
        it = c.item
        sig: dict[str, float] = {}
        why: list[str] = []
        if stage == "rrf":
            rrf = 0.0
            for rank in (c.vec_rank, c.lex_rank, c.rare_rank):
                if rank is not None:
                    rrf += 1.0 / (rrf_k + rank)
            sig["rrf"] = round(rrf, 6)
        else:
            # vector-primary fusion: semantic rank is the relevance backbone; the lexical channels exist for
            # recall (they put exact-term matches into the pool) and their evidence is scored by idf-weighted
            # concept coverage below, not by their noisy rank. An item the vector channel never saw still gets a
            # quarter of its best lexical rank so a purely lexical hit can surface. (Plain RRF over three channels
            # rewarded "mediocre everywhere" over "excellent in one" and buried items ranked 3rd by vectors.)
            fused = 0.0
            basis = "no channel rank"
            if c.vec_rank is not None:
                fused = 1.0 / (FUSION_K + c.vec_rank)
                basis = f"vector rank {c.vec_rank}"
            else:
                best = min((rk for rk in (c.lex_rank, c.rare_rank) if rk is not None), default=None)
                if best is not None:
                    fused = 0.25 / (FUSION_K + best)
                    basis = f"lexical-only rank {best} at a quarter weight"
            sig["fusion"] = round(W["fusion"] * fused, 6)
        why.append(
            f"channels {'+'.join(c.channels) or 'none'} (vec #{c.vec_rank}, lex #{c.lex_rank}, rare #{c.rare_rank})"
            + (f"; fused on {basis}" if stage != "rrf" else "")
        )
        if stage == "full":
            subj = (it.subject or "").strip().lower()
            opened = next((e for e in entity_prefix if subj.startswith(e + " ")), None)
            if subj in entity_weight:
                sig["entity_subject"] = round(W["entity_subject"] * entity_weight[subj] * subject_factor, 6)
                why.append(f"subject is the query entity '{it.subject}' (intent {analysis.intent or 'n/a'})")
            elif opened:
                sig["entity_subject"] = round(W["entity_subject"] * entity_prefix[opened] * 0.8 * subject_factor, 6)
                why.append(f"subject '{it.subject}' opens with the query entity '{opened}'")
            else:
                stmt_low = f"{it.subject} {it.statement}".lower()
                mentioned = sorted(((w, e) for e, w in entity_texts.items() if e in stmt_low), reverse=True)
                if mentioned:
                    w_, e_ = mentioned[0]
                    # commonness only discounts single-word entities (the domain's name, a generic word); a
                    # multi-word entity phrase is the topic itself however many items mention it
                    scale = w_ if " " not in e_ and "-" not in e_ else max(w_, 0.6)
                    sig["entity_mention"] = round(W["entity_mention"] * scale * mention_factor * 2, 6)
                    why.append(f"mentions entity {e_!r}")
                    if analysis.intent == "listing" and _IDENTIFIER.match((it.subject or "").strip()):
                        sig["member"] = W["member"]
                        why.append(f"'{it.subject}' looks like a member of the listed set")
            if matched_paths and any((it.topic or "").lower().startswith(p_) for p_ in matched_paths):
                sig["topic_affinity"] = W["topic_affinity"]
                why.append(f"filed under the question's area ({it.topic})")
            if concept_terms:
                item_terms = c.lexemes or _stem_tokens(f"{it.subject} {it.statement} {it.explanation or ''}")
                covered = concept_terms & item_terms
                if covered:
                    share = sum(weights.get(lx, 1.0) for lx in covered) / total_weight
                    sig["concept_coverage"] = round(W["concept_coverage"] * share, 6)
                    strongest = sorted(covered, key=lambda lx: -weights.get(lx, 1.0))[:4]
                    why.append(
                        f"covers {len(covered)}/{len(concept_terms)} query terms, {share:.0%} by weight "
                        f"({', '.join(strongest)})"
                    )
            if _affine(it, pref, plugin):
                sig["intent_affinity"] = W["intent_affinity"]
                why.append(f"{it.knowledge_type} suits intent '{analysis.intent}'")
            named = query_lex & (type_terms or {}).get(it.knowledge_type, set())
            if named:
                sig["type_match"] = W["type_match"]
                why.append(f"the question names its type ({', '.join(sorted(named))})")
            auth = max((sources.get(e.source_id, (0, ""))[0] for e in it.evidence), default=0)
            if auth:
                sig["authority"] = round(W["authority"] * auth / 100.0, 6)
                why.append(f"source authority {auth}")
            level = int(it.verification_level or 0)
            if level:
                sig["verification"] = round(W["verification"] * min(level, 4) / 4.0, 6)
            if any(sources.get(e.source_id, (0, ""))[1] == "official" for e in it.evidence):
                sig["official"] = W["official"]
            if analysis.version and it.product_version:
                if analysis.version.lower() in it.product_version.lower():
                    sig["version_match"] = W["version_match"]
                    why.append(f"version {it.product_version} matches")
                else:
                    sig["version_mismatch"] = W["version_mismatch"]
                    why.append(f"version {it.product_version} differs from {analysis.version}")
            status = ItemStatus(it.status)
            if status == ItemStatus.CANDIDATE:
                sig["candidate"] = W["candidate"]
                why.append("unverified candidate")
            elif status == ItemStatus.STALE:
                sig["stale"] = W["stale"]
                why.append("STALE")
            elif status == ItemStatus.CONFLICTED:
                sig["conflicted"] = W["conflicted"]
                why.append("CONFLICTED")
            if it.needs_review:
                sig["needs_review"] = W["needs_review"]
                why.append("flagged for review")
            if it.needs_revalidation:
                sig["needs_revalidation"] = W["needs_revalidation"]
            if any(
                getattr(e, "evidence_type", "") == "validator" and not (getattr(e, "details", None) or {}).get("passed")
                for e in it.evidence
            ):
                sig["validator_failed"] = W["validator_failed"]
                why.append("a domain validator rejected it")
        total = sum(sig.values())  # the score is exactly the sum of the recorded contributions
        out.append(Scored(candidate=c, score=round(total, 6), signals=sig, explanation=why))
    out.sort(key=lambda s: (-s.score, str(s.item.id)))
    return out


def diversify(ranked: list[Scored], *, k: int, jaccard: float = 0.75, per_subject: int = 3) -> list[Scored]:
    """One representative per near-duplicate group — same normalised subject *and* token Jaccard ≥ ``jaccard``
    (items about different subjects are never duplicates: "FIRST/LAST/NEXT … used in visual calculations only"
    are the members of a list, not restatements) — and at most ``per_subject`` items per subject in the top-K;
    the rest of the pool backfills in score order."""
    chosen: list[Scored] = []
    chosen_terms: list[tuple[str, set[str]]] = []
    per_subj: dict[str, int] = {}
    skipped: list[Scored] = []
    for s in ranked:
        if len(chosen) >= k:
            break
        terms = _stem_tokens(s.item.statement)
        subj = " ".join(sorted(_stem_tokens(s.item.subject)))  # "semantic model" == "semantic models"
        dup = any(sj == subj and len(terms & t) / max(1, len(terms | t)) >= jaccard for sj, t in chosen_terms)
        if dup or per_subj.get(subj, 0) >= per_subject:
            s.explanation.append("held back for diversity (near-duplicate or subject cap)")
            skipped.append(s)
            continue
        chosen.append(s)
        chosen_terms.append((subj, terms))
        per_subj[subj] = per_subj.get(subj, 0) + 1
    for s in skipped:  # backfill only if the pool ran out of distinct items
        if len(chosen) >= k:
            break
        chosen.append(s)
    return chosen


def expand(
    session: Session,
    selected: list[Scored],
    analysis: QueryAnalysis,
    *,
    plugin: Any,
    statuses: list[str],
    budget: int = 3,
) -> list[Scored]:
    """Bounded relationship expansion: for the top entities add the foundation item (definition) if missing
    and, by intent, negative items / examples about the same subject. Never more than ``budget`` additions."""
    if budget <= 0 or not analysis.entities:
        return selected
    present = {str(s.item.id) for s in selected}
    additions: list[Scored] = []
    try:
        foundation = set(plugin.types_with_role("foundation"))
        example_types = set(plugin.types_with_role("example"))
    except Exception:
        foundation, example_types = set(), set()
    wanted_polarity = "negative" if analysis.intent in ("limitation", "troubleshooting", "error") else None
    for ent in analysis.entities[:2]:
        if len(additions) >= budget:
            break
        rows = (
            session.execute(
                select(KnowledgeItem)
                .where(
                    KnowledgeItem.domain_id == selected[0].item.domain_id if selected else True,
                    KnowledgeItem.status.in_(statuses),
                    func.lower(KnowledgeItem.subject) == ent.canonical.lower(),
                )
                .options(selectinload(KnowledgeItem.evidence))
                .order_by(KnowledgeItem.confidence.desc())
                .limit(20)
            )
            .scalars()
            .all()
        )
        subject_present = any((s.item.subject or "").lower() == ent.canonical.lower() for s in selected)
        # the definition of the entity, when the context has nothing filed under it as a foundation
        if not any(
            (s.item.subject or "").lower() == ent.canonical.lower() and s.item.knowledge_type in foundation
            for s in selected
        ):
            for it in rows:
                if it.knowledge_type in foundation and str(it.id) not in present:
                    additions.append(_expanded(it, ent.canonical, "foundation"))
                    present.add(str(it.id))
                    break
        if wanted_polarity and len(additions) < budget:
            for it in rows:
                if it.polarity == wanted_polarity and str(it.id) not in present:
                    additions.append(_expanded(it, ent.canonical, "negative knowledge"))
                    present.add(str(it.id))
                    break
        if analysis.intent == "example" and len(additions) < budget:
            for it in rows:
                if it.knowledge_type in example_types and str(it.id) not in present:
                    additions.append(_expanded(it, ent.canonical, "example"))
                    present.add(str(it.id))
                    break
        if not subject_present and not rows:
            continue
    return selected + additions[:budget]


def _expanded(item: KnowledgeItem, entity: str, why: str) -> Scored:
    return Scored(
        candidate=Candidate(item=item),
        score=0.0,
        signals={"expansion": 0.0},
        explanation=[f"added by expansion: {why} of entity '{entity}'"],
        expanded_from=entity,
    )


__all__ = [
    "FUSION_K",
    "RRF_K",
    "W",
    "Scored",
    "diversify",
    "expand",
    "intent_preference",
    "score_candidates",
    "source_facts",
]
