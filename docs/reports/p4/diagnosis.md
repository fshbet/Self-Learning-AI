# P4 diagnosis — the knowledge substrate after P3

Date: 2026-09-15 · Inspection only; nothing was changed. Companion design: `docs/adr/0007-knowledge-coverage-gaps-and-relationships.md`.

## 1. State of the substrate (Power BI domain, live database)

| Measure | Value | Note |
|---|---:|---|
| sources (plugin, active) | 17 + 1 user | `powerbi-blog` and `ms-learn-powerbi-admin` yield 0 pages (publishers moved) |
| documents | 186 (181 EXTRACTED, 5 FETCHED) | the 5 un-extracted: `admin`, `datasets`, `info-functions-dax`, `desktop-custom-format-strings`, `service-roles-new-workspaces` (partial-extraction timeouts, P2) |
| knowledge items | 6,096 VERIFIED · 4 CONFLICTED · 43 REJECTED | no SUPPORTED/STALE/CANDIDATE at present |
| evidence | 6,158 extraction (all verbatim-verified) · 79 validator | |
| relationships | 7,280 `depends_on` · 1,006 `example_of` · 114 `compatible_under` · **0 `related_to`** | all `system`-derived; **75** cross-subject (the `compatible_under` pairs), the rest same-subject |
| live items with no relation | 3,703 of 6,096 (61 %) | |
| distinct subjects | 3,011 — 2,171 (72 %) have a single item; 2,777 have evidence from one document only | thin, hub-fed subjects |
| conflicts | 2 OPEN (ALLSELECTED, Burndown %) | no authoritative resolution |
| snapshots | v18 full, v19 full, v20 delta (2026-09-15 00:25) | the P3 state has **no snapshot of its own** — v19/v20 predate P3's report but P3 changed no knowledge, so v19 = P3 corpus |
| runs | **32 `evaluate` and 2 `snapshot` runs stuck RUNNING** | scheduler-triggered runs whose worker died when the server was stopped; housekeeping before P4.0 (mark FAILED/interrupted — no knowledge mutation) |
| golden set | 12 questions, dataset 0.3.0 | fields: required_concepts, authoritative_sources, expect_abstain, expected_version, must_not_contain, negative |

## 2. The CALCULATE corpus gap — where the page was lost

Chain inspected: source registry → crawl → link scope → page budget → extraction → knowledge → discovery.

| # | Question | Finding | Evidence |
|---|---|---|---|
| 1 | Was it discovered? | **Not by new-source discovery — it did not need to be.** The page is *inside* an existing plugin source: `ms-learn-dax` (seed `…/dax/dax-function-reference`, `allow_patterns: learn.microsoft.com/en-us/dax/`, `max_depth 2`, `max_pages 80`). Discovery (ADR 0005) proposes *sources*, never pages; it was never run for this domain (all 18 sources have `origin` plugin/user, no CANDIDATE rows, no search provider configured). | `sources` table; `discovery.py` |
| 2 | If discovered, what score? | n/a — page-level candidates do not exist as a concept in the platform today. | |
| 3 | If filtered, why? | **Not filtered.** `UrlScope.accepts(…/dax/calculate-function-dax)` is true (host, allow pattern, no deny match). | `collector.UrlScope` |
| 4 | Excluded because of page budget? | **Yes — this is the loss point.** The P2 pipeline run `1ab22f74` was launched with `max_pages: 15` per source (the run-level cap in `RunCreate.max_pages`, applied to every `crawl_source` job — payloads show `{'max_pages': 15}` for all 18 sources). The crawl is a FIFO BFS: the seed (depth 0) links 20 in-scope pages (category hubs); the budget was spent on the seed + 14 of those hubs (10 extracted, 1 FETCHED) and **depth 2 was never reached**. `filter-functions-dax` (depth 1, collected) links `calculate-function-dax` and `calculatetable-function-dax` directly. Every other source shows the same pattern (11–15 documents each, depth ≤ 1 except where hubs are small). | `jobs` payloads/results; `documents.depth` for `ms-learn-dax`: 1 at depth 0, 10 at depth 1, 0 at depth 2; raw HTML of `filter-functions-dax` |
| 5 | Was the hub page considered sufficient? | **Implicitly, yes.** The hub yields three one-line CALCULATE definitions ("evaluates an expression in a modified filter context"), which made CALCULATE a *subject* with foundation items — nothing in the pipeline distinguishes "a definition sentence from a listing page" from "the reference page". Nothing records that the hub's 32 in-scope links were left uncollected. | `knowledge_items` subject `CALCULATE`: 3 definitions, all from the hub |
| 6 | Did discovery fail to follow a relevant link? | The *crawler* saw the link (it parses all links of every fetched page) and queued it; the queue was abandoned when the budget ran out. The frontier (in-scope URLs seen but not visited) is discarded — `CrawlStats` has no record of it. | `collector.crawl_source` |
| 7 | Did source authority influence the decision? | **No.** Authority plays no role in crawling; BFS order is link order on the page. `new-dax-functions` and `financial-functions-dax` were collected before any function reference page because they come first on the seed. | |
| 8 | Could the knowledge graph have predicted the missing page? | **Yes, with two signals it does not compute today:** (a) a subject (CALCULATE) whose every item comes from one *hub-like* document (a page with many in-scope links and one-line statements per subject) and which is the target of an uncollected in-scope link whose slug/anchor names it; (b) a concept named by other items' statements that is nobody's subject ("context transition" is mentioned by 2 items — ADDCOLUMNS example/limitation — and by 4 documents, but no item is *about* it). Both are domain-independent structural facts. | `documents.text ilike 'context transition'` = 4; items mentioning it = 2; subject "context transition" = none |
| 9 | Could extraction identify an important referenced concept that is absent? | Partly. Extraction already produces `subject` for every item; the *reference* side (concepts named inside statements) is not extracted as structure. Deterministic co-mention detection over the existing statements finds **5,976** cross-subject mentions of multi-word subjects — enough to derive references without a model call. A referenced term that is never a subject is exactly the "absent concept" signal. | co-mention query in this diagnosis |
| 10 | Will this recur for other important concepts? | **Yes, systematically.** Every source was cut at 15 pages; reference pages sit at depth 2 behind category hubs on every Microsoft Learn section (M functions, REST endpoints, visuals). 2,171 single-item subjects and 2,777 single-document subjects are the footprint of hub-fed knowledge. Two of the three remaining P3 failures (`query folding` for incremental refresh, `DAX filter` for RLS) are the same shape: the concept exists, the page that connects it to the question's subject was not collected or the connection was not recorded. | §1 counts; P3 final report §8 |

Root cause in one sentence: **a run-level page budget applied uniformly to a FIFO crawl spent the budget on hub pages, and the platform neither prioritises the frontier by what the corpus already talks about nor records what it left behind, so the loss was invisible to discovery, extraction, the graph and the evaluation until a golden question named the page.**

The smallest domain-independent fix (ADR 0007 §P4.1): (1) keep the link graph of collected pages (`page_links`), (2) order the crawl frontier by evidence instead of FIFO — in-scope links referenced by more collected pages, whose anchor/slug matches an existing subject or a plugin vocabulary term, rank first; hub pages (link-dense, low text per link) do not consume the budget ahead of the pages they point to, (3) record the unvisited frontier with its scores as *coverage gaps* instead of dropping it. No URL is hard-coded; the Power BI plugin needs no new declaration (its taxonomy/terminology already contain the vocabulary; `CALCULATE` is a live subject).

## 3. Relationships

* The graph is **structural only**: same-subject `depends_on`/`example_of` (derived from declared roles) and `compatible_under` from conflict resolution. There is no relation between *different* subjects except those 114 pairs, so retrieval expansion (P3 stage 6, bounded to the entity's own foundation/negative/example items) cannot reach a related concept.
* The corpus states the relations in words: "Before configuring your incremental refresh solution, you must read and understand Query folding guidance" (subject *Incremental refresh solution*), i.e. a `related_to`/`depends_on` edge to *Query folding* is derivable deterministically from co-mention (exact subject match inside a statement, longest match, multi-word or identifier subjects only). 5,976 such mentions exist.
* `RELATION_TYPES` already contains `related_to`; nothing creates it. Propagation (`PROPAGATING`) must not include it (a related concept changing does not invalidate the item).

## 4. Lexical index

`WEIGHTED_TSV` (`setweight(to_tsvector(cfg, subject),'A') || … statement 'B' || explanation 'C' || topic 'D'`) is computed per matching row at query time; only the *plain* expression `to_tsvector('english', subject||statement||explanation)` has a GIN index (migration 0001). The `@@` filter uses that index; `ts_rank_cd` then recomputes four `to_tsvector` calls per candidate row — 100–160 ms per lexical query, two queries per retrieval (full + rare-term), plus a 260 ms document-frequency scan once per term set. Warm retrieval 330–650 ms. A stored weighted tsvector column with its own GIN index removes the recomputation; the per-domain text-search configuration means it cannot be a single `GENERATED` column with a constant config — it must be maintained per item with the domain's config recorded alongside it.

## 5. Evaluation breadth

12 questions (11 answerable), 19 required concepts, 6 topics. `EvalQuestion` lacks: optional concepts, expected source class, expected evidence URLs beyond `authoritative_sources`, expected answer type, abstention-acceptable, scope/version requirements as structured fields. Retrieval evaluation (`kp eval retrieval`) and answer evaluation (`kp eval run`) already share the set but report separately; both key on `required_concepts` substrings. One question decides 8.3 points; the judge's verdict on paraphrases varies run to run (P3 report §6).

## 6. Knowledge-side measurement today

`knowledge_metrics()` (ops) counts items by status/type, negative, derived, examples, needs-review, evidence, relationships, conflicts. It does not measure concept coverage against the evaluation sets, authoritative-evidence coverage per subject, evidence completeness (verbatim-verified share per item), relationship coverage (share of dependent items with a foundation edge; cross-subject edges), orphan entities (referenced-but-absent subjects), source diversity per subject, or freshness distribution; and nothing links an evaluation failure to a substrate cause (gap vs missing relation vs ranking).

## 7. Second domain

`roboticslab` (fixture plugin, 3 pages, 5 items) exercises the pipeline, evaluation, export and P3 retrieval. Its pages link each other (`index → spec, safety`), so page-link recording, frontier prioritisation, gap detection (a HAZARD referencing a spec that is absent when `spec_v2` removes it), co-mention relations (safety rule mentions "payload rating" → payload spec) and knowledge-side metrics can all be validated on it without new fixtures beyond one extra page.

## 8. Housekeeping before P4.0 (no knowledge mutation)

* Mark the 34 stuck RUNNING runs (scheduler evaluate/snapshot) as interrupted — the worker they belonged to no longer exists.
* The 5 FETCHED-but-not-extracted documents are a known P2 leftover (300 s model timeouts); they are *not* to be re-extracted before the baseline is recorded.
* Snapshot v19 (full) + v20 (delta) are the P3-state export; P4.0 records their ids/hashes rather than building a new one.
