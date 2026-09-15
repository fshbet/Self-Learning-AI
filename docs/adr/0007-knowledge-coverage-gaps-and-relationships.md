# ADR 0007 — Knowledge coverage, gaps, relationships and evaluation breadth (P4)

Status: **proposed — awaiting approval before implementation** · Date: 2026-09-15
Diagnosis: `docs/reports/p4/diagnosis.md` · Predecessor: ADR 0006 (accepted, implemented).

## Problem

P3 made retrieval and answering explainable and measurably better on the same corpus; the failures that remain
are about knowledge the context cannot contain. The diagnosis shows why: a uniform page budget on a FIFO crawl
left reference pages behind hub pages and threw the frontier away; the relationship graph has no edges between
different subjects, so nothing connects "incremental refresh" to "query folding" although a statement says so;
nothing measures the substrate against what the evaluation and the corpus itself say should be there; and the
evaluation set is too small to distinguish a fix from noise. The lexical channel that P3 leans on recomputes its
weighted vector per row.

## Design principles

Domain-independent core; every domain-specific value comes from the plugin manifest or the corpus. Nothing is
crawled, approved or resolved automatically: the platform *proposes* (gaps, candidates, frontier priorities) and a
person approves. No P3 baseline artefact is mutated. `accuracy`, the P3 golden set (0.3.0), the ranking weights
(`rank.W`) and the P3 harness are frozen; new evaluation is additive and versioned. Deterministic, bounded,
explainable, cycle-safe.

## Architecture

```
P4.0  baseline          scripts/p4_baseline.py → docs/reports/p4/baseline/{corpus,graph,sources,retrieval,answers}.{json,md}
                        + hashes of golden set 0.3.0, snapshot v19/v20 manifests, rank.W, prompt versions

P4.1  crawl completeness
      collector.crawl_source
        → page_links table: every in-scope link of every fetched page (from_document, url, anchor, depth)
        → frontier = priority queue, not FIFO. score(url) = referrers · vocabulary/subject match on anchor+slug
          · hub penalty for the referrer · depth penalty; deterministic tie-break (url)
        → CrawlStats.frontier_left: unvisited in-scope URLs with score and referrers → gap kind `uncollected_page`
        → run-level max_pages stays; a source's own max_pages is the default when the run gives none

P4.2  gap detection      core/quality/gaps.py  (pure functions over the DB; no model call)
        detectors, each returning Gap(kind, concept, reason, evidence[], affected{items, questions}, suggestion,
        priority, status):
          absent_concept         a term named by ≥ N statements (deterministic co-mention on subject-like n-grams)
                                 or by an evaluation question's required/optional concepts, that is no live subject
          thin_subject           subject whose evidence comes from one document that is hub-like (many in-scope
                                 links, short statements) and which is the target of an uncollected linked page
          uncollected_page       page_links target not collected, in scope, anchor/slug matches a subject or a
                                 plugin vocabulary term (from P4.1's frontier)
          unauthoritative        subject with items only from sources below the domain's authority floor or of
                                 non-official class while an official source is registered for the domain
          orphan_dependent       dependent/example item (declared roles) with no foundation edge
          unresolved_conflict    OPEN conflict whose sides share no official evidence
          eval_missing_concept   evaluation question whose required concept is absent from every live item
                                 (retrieval harness "not in corpus") — links the failure to a gap
        persisted in knowledge_gaps (migration 0013); kp gaps scan|list|resolve, /api/gaps, Review page tab
        status: open | proposed (a candidate was generated) | approved | collected | resolved | dismissed

P4.3  relationship expansion
        derive: deterministic co-mention relations — a live item whose statement names another live subject
                (exact, longest match; multi-word or identifier subjects; ≤ 3 per item) gets related_to → that
                subject's foundation item (origin system, rule "mention", details {span}). Never PROPAGATING.
                Backfill command kp db relate; runs at extraction time afterwards.
        plugin: retrieval.expansion = {relations: [related_to, depends_on], budget: 3, per_entity: 2} (declared;
                core default = the P3 behaviour: foundation/negative/example only)
        rank.expand: after the P3 step, follow declared relation types from the selected items' subjects one
                hop (depth 1, no cycles, no duplicates, budget), each addition carries expanded_from +
                relation + the item it came from in its explanation; provenance untouched
        retrieval harness: a `--retrieval full` vs `--expansion off` switch to measure the effect separately

P4.4  stored weighted lexical index (migration 0013)
        knowledge_items.lexical tsvector NULL + lexical_config text NULL; GIN index on lexical
        maintained by the application: on item create/update (pipeline, knowledge_entry, supersede) with the
        domain's text-search config; kp db reindex-lexical <domain> backfills; the domain's config change
        invalidates (lexical_config mismatch → row treated as unindexed)
        candidates.lexical_candidates: WHERE lexical_config = :cfg AND lexical @@ q, ORDER BY ts_rank_cd(lexical,…)
        with fallback to the P3 expression for rows whose lexical_config ≠ cfg (correctness never depends on the
        backfill having run); ranking semantics, weights (A/B/C/D) and variants unchanged
        benchmark script scripts/p4_bench_retrieval.py: cold/warm, lexical-only, vector-only, combined, before/after

P4.5  expanded evaluation set   domains/powerbi/evaluation-extended.yaml (dataset 1.0.0, ~60 questions)
        EvalQuestion gains: optional_concepts, expected_source_class, expected_evidence (urls), answer_type,
        abstain_ok, negative_concepts, scope (version / product area) — all optional, all backward compatible
        kp eval run --set golden|extended; kp eval retrieval --set …; runs record the set + version so
        regression baselines never mix sets; the 0.3.0 set and its metrics are untouched
        authored from Power BI knowledge across the 13 areas and 14 question types the brief lists — NOT from
        what the corpus happens to contain (questions that fail are the point: they feed P4.2/P4.7)

P4.6  knowledge-side evaluation   core/evaluation/knowledge_eval.py; kp eval knowledge <domain> [--set]
        metrics: concept coverage (set concepts present as subject / in statements / in documents), authoritative
        evidence coverage, evidence completeness, relationship coverage (dependents with foundation; cross-subject
        edges per subject), orphan entities, unsupported derived knowledge, unresolved conflicts, stale share,
        negative-knowledge coverage, example coverage, source diversity/independence per subject, freshness
        root cause per failed question: concept absent from documents → collection gap (→ P4.2 gap) ;
        in documents, not in items → extraction gap ; in items, not related to the question's subject →
        relationship gap ; related and in pool but outside K → ranking miss ; in context, not in answer →
        generation failure. Recorded on the evaluation run (findings) and as gaps.

P4.7  prioritised candidates     core/collection/prioritize.py; kp gaps candidates; UI "Proposed collection"
        from gaps + eval failures → candidates of two kinds: page candidates (frontier URLs inside existing
        sources) and source candidates (discovery queries for the search provider, ADR 0005). priority =
        f(evaluation impact, concept importance = mentions + dependents, authoritative availability, failed
        questions, source authority, freshness, confidence) — every point with a reason. Approval-gated:
        kp gaps approve <id> enqueues one bounded crawl_source job with url_seeds=[url] and max_pages=1 (or the
        discovery flow for source candidates). No auto-approval; no recursion.

P4.8  re-run P3 evaluation (golden 0.3.0, modes p2/p3-retrieval/p3-plan/p3) and the extended set;
      Recall@8/12/20 from the harness; report P2 → P3 → P4 and P4-only tables (scripts/p4_compare.py)

P4.9  no weight tuning; if needed later: offline experiment file, P3 W preserved as rank.W_P3

P4.10 roboticslab: page_links + frontier on the fixture site (one added page behind the index with a budget of
      2), gap detection (spec_v2 removes the payload spec → the safety rule's reference becomes an absent
      concept), co-mention relation (rule → payload spec), lexical column, knowledge metrics, retrieval harness

P4.11 snapshot v(n+1) full + delta from v19, AI source, schema validation, kp export verify, kp export apply
      (v19 + delta = v(n+1)), provenance diff of unchanged items = empty; new relations/gaps appear as additions
```

## Components and files

| Area | File | Change |
|---|---|---|
| migration | `alembic/versions/0013_page_links_gaps_lexical.py` | `page_links`, `knowledge_gaps`, `knowledge_items.lexical` + `lexical_config` + GIN index; reversible (`downgrade` drops all) |
| models | `models.py` | `PageLink`, `KnowledgeGap`, two columns on `KnowledgeItem` |
| crawl | `core/collection/collector.py` | priority frontier, link recording, `frontier_left`, hub score; `crawl_source(url_seeds=…)` for approved page candidates |
| crawl vocabulary | `core/collection/discovery.py` | reuse `vocabulary_terms`; add subject vocabulary from the KB (cached) |
| gaps | `core/quality/gaps.py` (new) | detectors, `scan(session, plugin)`, persistence, status transitions |
| prioritisation | `core/collection/prioritize.py` (new) | gap/eval → candidates with reasons; approval → job |
| relations | `core/versioning/dependencies.py` | `derive_mention_relations`, `related_to` rule; `PROPAGATING` unchanged |
| pipeline | `core/pipeline.py`, `core/knowledge_entry.py`, `core/versioning/supersede.py` | maintain `lexical`; call mention derivation |
| retrieval | `core/retrieval/rank.py` (`expand`), `retrieve.py`, `candidates.py` | declared-relation expansion; stored-column lexical query with fallback; `expansion` switch |
| plugin contract | `core/plugins/base.py` | `RetrievalSpec.expansion`, `EvalQuestion` optional fields, `Plugin.evaluation_set(name)` |
| evaluation | `core/evaluation/runner.py`, `retrieval_eval.py`, `knowledge_eval.py` (new) | `--set`, Recall@K list, root-cause chain, knowledge metrics |
| CLI | `cli.py` | `kp gaps scan|list|candidates|approve|resolve`, `kp db reindex-lexical`, `kp db relate`, `kp eval knowledge`, `--set` |
| API/UI | `api/routes_gaps.py` (new), `schemas.py`, `frontend/src/pages/Review.tsx` (gaps tab) or `Gaps.tsx`, `api.ts` | list/approve/dismiss gaps and candidates |
| scripts | `scripts/p4_baseline.py`, `scripts/p4_bench_retrieval.py`, `scripts/p4_compare.py` | baseline, benchmark, report tables |
| domain | `domains/powerbi/evaluation-extended.yaml`, `plugin.yaml` (`retrieval.expansion`) | new set; declared expansion relations |
| fixtures | `tests/fixtures/roboticslab/*.html`, `plugins/roboticslab/plugin.yaml` | one linked page behind the index; `retrieval.expansion` |
| docs | ADR 0007, `domains/README.md`, `README.md`, `docs/reports/p4/*`, plan §16.3 | |

Unchanged: `accuracy`, golden set 0.3.0, `rank.W`, P3 prompts and modes, export schema (relations already export;
`knowledge_gaps` are operational state, not knowledge, and are not exported), security controls, the 8B model.

## Migration requirements

One migration, `0013`, reversible:

* `page_links(id, domain_id, from_document_id FK, url, anchor, in_scope bool, depth int, created_at)`, unique
  `(from_document_id, url)`, index `(domain_id, url)`.
* `knowledge_gaps(id, domain_id, kind, concept, reason, evidence json, affected json, suggestion json, priority
  int, status, created_at, updated_at, resolved_at, resolution)`, index `(domain_id, status, priority)`.
* `knowledge_items.lexical tsvector NULL`, `knowledge_items.lexical_config varchar(40) NULL`, `ix_ki_lexical`
  GIN. Backfill is a command, not part of the migration (6,100 rows ≈ seconds; a large domain runs it in
  batches); queries fall back to the expression until then.

`downgrade` drops the three objects; no data outside them is touched. Export schema version unchanged (nothing
new is exported); if gaps are exported later that is a schema minor bump (1.6).

## Test plan

Unit: frontier scoring (referrers, vocabulary/subject match, hub penalty, determinism); gap detectors on
synthetic rows (each kind, priority ordering, status transitions, idempotent rescans); mention-relation derivation
(longest match, ≤ 3 per item, identifier vs multi-word, never PROPAGATING, no self/duplicate edges); declared
expansion (depth 1, cycle-safe, budget, duplicate evidence excluded, explanation carries the relation);
lexical-column query equivalence (same candidates and order as the expression on a fixture corpus, fallback when
`lexical_config` differs); `EvalQuestion` optional fields backward compatible; root-cause classifier on
constructed cases (collection gap / extraction gap / relationship gap / ranking miss / generation failure).

Integration (real DB, fixture domains): crawl with a budget smaller than the site → frontier recorded, the
subject-named page ranked first when the budget grows by one, hub not re-prioritised; `kp gaps scan` on
roboticslab after `spec_v2` → `absent_concept`/`orphan_dependent` for the payload rule; approve a page candidate →
one bounded crawl job, no recursion; co-mention relation → a concept reachable only through `related_to` surfaces
in `retrieve()` with weak lexical/vector similarity (Power BI: incremental refresh → query folding on the live
corpus, offline; roboticslab: safety rule → payload spec); reindex-lexical then retrieval harness identical to the
P3 JSON at `--retrieval full`; knowledge-eval metrics on roboticslab; export apply v19 + delta = new head.

Regression: the whole P3 suite (189 + orchestration) unchanged; P3 harness on the P3 corpus unchanged before any
collection (bit-identical `retrieval-full-k8.json` after P4.4).

## Expected measurements (targets, not acceptance criteria)

| Measure | P3 (baseline) | P4 expectation | How |
|---|---:|---:|---|
| CALCULATE page collected | no | yes, via an approved page candidate ranked first for `ms-learn-dax` | P4.1 + P4.7 |
| gaps detected on Power BI | — | ≥ 50 open, top-10 include CALCULATE reference, context transition, query-folding link | P4.2 |
| cross-subject relations | 75 | ≥ 2,000 `related_to` (from 5,976 mentions, ≤ 3 per item) | P4.3 |
| golden 0.3.0 accuracy (p3) | 8–9/12 | 9–10/12 after collection; dax-calculate-01 passes only if the page is approved and extracted | P4.8 |
| offline recall@8 / MRR | 0.833 / 0.700 | ≥ 0.87 / ≥ 0.72 (no weight change) | P4.8 |
| recall@12 / @20 | 0.833 / 0.833 | reported | harness |
| warm retrieval | 330–650 ms | < 300 ms (lexical queries ≈ 10–20 ms each); cold unchanged ± df scan | P4.4 |
| extended set (≈ 60 q) | — | first measurement; expected well below the golden score (that is its purpose) | P4.5 |
| knowledge metrics | — | first measurement; root cause assigned to every failed question | P4.6 |
| snapshot | v19 + v20 | v21 full + delta v22 from v19; apply reconstructs; provenance diff empty | P4.11 |

## Risks

* **Frontier prioritisation over-fits vocabulary**: pages named for known subjects win, novel topics lose. Mitigated
  by referrer count and a floor of FIFO order for ties; the leftover frontier is visible as gaps either way.
* **Gap noise**: co-mention finds generic terms ("report", "model"). Mitigated by requiring multi-word or identifier
  terms, a mention threshold, and priority driven by evaluation impact; gaps are proposals, never actions.
* **Mention relations wrong**: an item naming a subject in passing gets a `related_to` edge. Bounded (≤ 3 per item,
  never propagating, depth-1 expansion with budget) and explainable in the answer; the P3 harness guards precision.
* **Lexical column drift**: rows updated outside the maintained paths would rank on stale lexemes. Mitigated by
  `lexical_config` guard, a reindex command, and the expression fallback; a test compares column vs expression.
* **Extended set authored to the corpus**: would inflate scores. Mitigated by authoring from the brief's area/type
  matrix first and recording which questions have no corpus support (those become gaps, not deletions).
* **Approval fatigue**: too many candidates. Mitigated by priority, grouping by source, and a per-run cap.
* **Second-domain drift**: a detector that needs Power BI semantics. Stop condition: extend the plugin contract or drop
  the detector; never a domain branch in core.
