# ADR 0006 — Retrieval and answer intelligence (P3)

Status: **accepted and implemented** (approved 2026-09-15; commits `4bc50f0` … `62539d5`, see `docs/reports/p3/final-report.md`) · Date: 2026-09-15
Deviations from the design as approved are listed in the final report (§2 *Architecture changes*).
Diagnosis: `docs/reports/p3/diagnosis.md` · Baseline: `docs/reports/p3/retrieval-baseline-k8.json`

## Problem

Retrieval ranks 6,100 items by two signals only (whole-question full-text rank with AND semantics, cosine
similarity) fused by RRF, hands a fixed eight to the model with no plan and no completeness check. Four of the
five P2 "answer-generation failures" are concepts that exist in the corpus but rank 10–37; one is a concept the
lexical channel can never surface for a natural-language question; retrieval knows nothing about entities,
intent, authority, verification, negative knowledge, examples or duplicates.

## Design

Domain-independent pipeline in `core/retrieval/`; every domain-specific value comes from the plugin manifest.

```
question
  → query analysis        normalise (case, punctuation, hyphen variants); lexemes via the domain's text-search
                          config; entities = query n-grams that are subjects in this domain's live knowledge,
                          code-like / upper-case tokens; intent by deterministic cues (core set + plugin cues)
  → candidate generation  three channels into one pool (≈60): vector; lexical with term-weighted OR query
                          (subject weight A, statement B, explanation C, topic D); entity channel = live items
                          whose subject is a detected entity
  → scoring               explainable score = RRF base + bonuses, every bonus recorded per item:
                          entity-subject match · concept coverage of query lexemes · intent→type affinity
                          (via the plugin's roles/polarity: definition→foundation, limitation/risk→negative,
                          example→example role, procedure→dependent) · authority (0–100) · verification level ·
                          source class official · penalties for CONFLICTED / STALE / flagged (never excluded,
                          still labelled) · product-version match/mismatch when the query names one
  → diversity             one representative per near-duplicate group (token Jaccard ≥ 0.8 or same subject +
                          predicate core), best score wins; backfill from the pool
  → expansion             for the top entities add their foundation item (definition) if missing and, by intent,
                          negative items / examples about the same subject (bounded)
  → evidence selection    K_context = 12 (was 8); each block gains source name + authority + a short excerpt
  → answer plan           deterministic: entities, intent, must-cover = entities + subjects of their foundation
                          items, should-cover = terms that recur across the selected evidence; passed to the
                          model as "cover these; state limitations the items carry"
  → generation            one call; citations [n] as today
  → completeness check    deterministic: must/should-cover terms present in the evidence but absent from the
                          answer; if any must-cover (or ≥ half of should-cover) is missing → one targeted
                          regeneration ("extend the answer to also cover X, citing [n]"), citations preserved,
                          reason recorded on the Answer; already-complete answers never regenerate
```

Intent vocabulary in core (`definition`, `procedure`, `troubleshooting`, `comparison`, `syntax`, `limitation`,
`configuration`, `architecture`, `conceptual`, `example`, `error`, `version`), cues in English by default;
the plugin may add cues per intent (`retrieval.intent_cues`) and override the intent→type map
(`retrieval.intent_types`, defaults derived from declared roles/polarity). No LLM call for intent: the
deterministic classifier is enough for the failure modes observed; an unknown intent means "no preference".

## What changes

| Component | Change |
|---|---|
| `core/retrieval/query.py` (new) | normalisation, lexemes, entity detection (subject lookup), intent classifier |
| `core/retrieval/rank.py` (new) | scoring with recorded signals, diversity, relationship expansion |
| `core/retrieval/search.py` | lexical channel → weighted OR query; entity channel; `retrieve()` pipeline; `hybrid_search` kept as the raw two-channel primitive (tests, `kp search`) with an option to run the full pipeline |
| `core/retrieval/answer.py` | context assembly (12, richer blocks), plan, completeness check, targeted regeneration; `Answer.plan`, `Answer.regeneration` |
| `core/extraction/prompts.py` | `answer@1.2` prompt with plan section; regeneration prompt |
| `core/plugins/base.py` | `RetrievalSpec.intent_cues`, `intent_types` (optional) |
| `core/evaluation/checks.py`, `runner.py` | new metrics reported separately: `factual_accuracy`, `completeness`, `authoritative_hit_rate`, `regeneration_rate`, `answer_generation_failure_rate`; `accuracy` and every gating check unchanged; `optional_concepts` on `EvalQuestion` (informational) |
| `core/evaluation/retrieval_eval.py` | report per-item signals; `--pipeline` switch to compare raw vs full |
| API `/api/ask`, `AskResponse`; Search page | plan + regeneration exposed (read-only display) |
| migration | none required; optional `0013` GIN index on the weighted tsvector expression for speed |

Unchanged: the corpus, the golden set and its gating checks, `accuracy`'s definition, the scoring rule and
confidence, snapshot/export contracts, security boundaries, the 8B model.

## Test plan

Unit: query normalisation/hyphen variants, entity detection, intent cues (core + plugin), score composition and
signal recording, diversity grouping, expansion bounds, plan construction, completeness check, regeneration
trigger/no-trigger, citation preservation. Integration (fixture domains, fake model): the ten regression cases in
the P3 brief (CALCULATE-style entity beats term-dense noise; exact concept beats generic similarity; low-authority
similar text does not outrank authoritative evidence; conflicting knowledge visible and labelled; stale/flagged
not authoritative; required concepts detected; supported-but-incomplete caught; regeneration preserves citations;
complete answers do not regenerate; domain independence via `roboticslab` retrieval). Offline: `kp eval retrieval`
before/after on the same corpus; online: `kp eval run` on the same golden set.

## Expected metrics (same corpus, same model)

concept recall@8 0.74 → ≥ 0.90 · MRR 0.56 → ≥ 0.75 · precision@8 ≥ 0.88 (unchanged or better) · authoritative
hit rate 0.50 (bounded by the un-crawled page) · accuracy 5/12 → 8–9/12 · completeness ≥ 0.8 · regeneration on
≤ 30 % of questions · retrieval latency < 300 ms · one extra model call only when regeneration triggers.

## Risks

Entity over-boost on questions where the entity is incidental (mitigated by capping the bonus and requiring a
lexeme match too); intent misclassification (mitigated: unknown → no preference, cues are conservative);
diversity dropping a needed sibling (mitigated: backfill and a small group threshold); larger context slowing the
8B model (bounded at 12 items / ~6 k tokens); the plan being ignored by the model (measured by the
completeness metric and regeneration rate); ranking changes shifting which items get cited (the evaluator's
citation checks stay).
