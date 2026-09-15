# P2 — Final report: operating the platform at realistic scale

Date: 2026-09-15 · Domain: Power BI · Pipeline run `1ab22f74-6572-4554-9533-4aba644fe24b` (2026-09-14 11:17 → 23:05 UTC)
Companion files: `baseline-metrics.json` (before), `expanded-metrics.json` (after), `comparison.md` (P2.0.2 tables),
`longrun.jsonl` (70 ten-minute samples), `scripts/longrun_sample.py`, `scripts/p2_compare.py`.

## 1. Recovery (machine restart during the crawl)

The PC rebooted at about 15:05 UTC with the crawl half-way (82 of 186 documents extracted, 2,578 items).

| Question | Answer |
|---|---|
| What survived | Everything: the run row, all 205 job rows with their states, 186 documents, 2,578 items, 2,660 evidence, 2,468 relations, the two open partial-section retries, 19 long-run samples. State lives only in PostgreSQL and the object store; there are no PID/lock/lease files. Docker Desktop did not auto-start; once started, the `kp-postgres` container came back by itself. |
| What was recovered | The one job that was RUNNING at shutdown (`incremental-refresh-xmla`, locked 15:05:24). Its transaction never committed, so nothing partial existed. The worker's own housekeeping requeued it 60 minutes after the lock time ("requeued: worker lock expired") without intervention; it then ran to completion (attempt 2). |
| What was retried | That job; the two partial-section retries already queued; later the dead-letter job (see §4). |
| Was work duplicated | No. 106 live extract jobs ↔ 106 distinct documents before resume; unique constraint on (domain, url); 0 duplicate document hashes; 0 duplicate item hashes; 0 duplicate evidence; section hashes on completed documents mean a re-crawl sends nothing to the model. |
| Was work lost | No. Items: 2,578 → 6,143 after resume with the same run id; no document was re-extracted. |

Verdict: the platform recovered from the machine restart, the worker termination, the interrupted job, the
partial extractions and the queued work **without manual repair**. The only manual step was starting Docker Desktop.

## 2. Corpus growth (baseline → final)

| Metric | Baseline | Final |
|---|---:|---:|
| sources (active) | 12 | 18 |
| documents fetched | 3 | 186 |
| documents fully extracted | 3 | 181 |
| documents partially extracted | 0 | 5 (one section each timed out three times; kept `FETCHED` + error, retried on the next crawl) |
| knowledge items (non-rejected) | 48 | 6,100 (6,096 VERIFIED, 4 CONFLICTED) |
| rejected as near-duplicates | 0 | 43 |
| negative knowledge (limitations/warnings/anti-patterns) | 2 | 466 |
| examples | 0 | 575 |
| derived / synthesized | 2 | 66 |
| evidence records (all verbatim-verified) | 50 | 6,237 |
| relationships | 0 | 8,400 |
| open conflicts | 0 | 2 |
| needs review / needs revalidation | 0 / 0 | 0 / 4 |
| database | 11.9 MB | 87.3 MB |
| raw documents stored | 0.1 MB | 14.8 MB |

Areas covered (items): Fabric 1,177 · DAX 1,076 · Service 748 · Data Modeling 614 · Visualization 585 · Gateways &
Refresh 501 · Power Query 332 · Security 191 · REST API 123 · Troubleshooting 98 · Administration 10 · unclassified
645 (extractor topic did not match the taxonomy). Provenance: 5,422 OFFICIAL, 678 EXTERNAL (SQLBI); 3 items have
evidence from two independent sources; 0 mirror documents. Two catalogue entries yielded nothing: `powerbi-blog`
(now redirects off the allowed host) and `ms-learn-powerbi-admin` (hub moved to `fabric/admin`) — catalogue
curation, not pipeline defects.

Extraction: 1,293 sections, 6,986 raw claims, 818 dropped for lacking a verbatim quote (11.7 %), 77 validator runs.

## 3. Evaluation (same golden set 0.3.0, same checks, same qwen3:8b answer/judge, answer prompt answer@1.1)

| Metric | Baseline | Expanded |
|---|---:|---:|
| accuracy (all 12 checks + judge) | 0.4167 | 0.4167 |
| coverage (knowledge present for the question) | 0.4545 | **1.0000** |
| retrieval precision | 0.4545 | **0.8977** |
| retrieval recall (authoritative source retrieved) | 0.5000 | 0.5000 |
| evidence support | 0.4000 | **0.8182** |
| citation correctness | 0.5455 | **1.0000** |
| hallucination rate (judge) | 0.6000 | **0.1818** |
| uncited-answer rate | 0.3636 | **0.0000** |
| negative-knowledge coverage | 1.0000 | 1.0000 |
| expected abstention | 1/1 | 1/1 |

| Failure class | Baseline | Expanded |
|---|---:|---:|
| coverage_failure | 6 | 0 |
| retrieval_failure | 1 | 2 |
| answer_generation_failure | 0 | 5 |
| uncited_answer / citation_failure / validation_failure | 0 | 0 |
| expected_abstention | 1 | 1 |

Reading: growing the corpus removed every coverage failure and every uncited answer, and made answers grounded
(citation correctness 1.0, hallucination 0.6 → 0.18). Accuracy did not move because the failures changed *kind*:
the dominant remaining class is `answer_generation_failure` — the judge finds 4 of those 5 answers correct and
supported, but the answer omits a required concept (`query folding` for incremental refresh, `DAX filter` for
RLS, `fact table` for star schema, `PREVIOUS` for visual calculations). Per the rules, the evaluation was not
changed to accommodate this.

## 4. Failure analysis

| Question | Class | Kind | What happened |
|---|---|---|---|
| dax-calculate-01 | retrieval_failure | retrieval + answer | 94 CALCULATE items exist, but the retrieved eight came from DAX Guide / SQLBI articles, not the authoritative `calculate-function-dax` page (its section was never fetched: 15-page budget); answer omits *context transition* and adds an unsupported environment claim. |
| model-bidi-01 | retrieval_failure | retrieval | 3/8 retrieved items on topic; RLS-flavoured bi-directional items outrank the modelling guidance; *ambiguity* not stated. Judge: correct. |
| gateway-01 | answer_generation_failure | answer | Cited three true "standard gateway is recommended for …" items but answered a different question (which gateway) and never stated the core condition (source not reachable from the cloud). |
| model-star-01, refresh-incremental-01, security-rls-01, visual-calc-01 | answer_generation_failure | answer (wording) | Judge: correct and supported; a required concept word is missing from the answer text. |
| 5 partial documents | pipeline | model | one section each (REST reference tables, a DAX info-functions table, custom format strings) exceeded the 300 s model timeout on all three retries — kept partial, never marked extracted. |

Pipeline defects found and fixed during the run (each with a regression test):

1. `GuardedTransport` died on redirect hops (`RequestNotRead`) — `8e6b754`.
2. Model timeouts / malformed JSON were recorded as extracted → silent knowledge loss — `f87bbc3` (failed sections
   retried with backoff, document stays partial; two already-affected documents were re-extracted).
3. Subjects used as ILIKE patterns: `%`, `_`, trailing `\` — false same-subject candidates across 28 subjects and a
   dead-letter crash on the custom-format-strings page — `82fe99b` (job retried successfully: 50 items).
4. Revalidation churn: four dependents of a CONFLICTED item revalidated every scheduler tick (48× each) —
   `9b1a4e0` (only re-queued when the item or a dependency changed; "Revalidate all" still forces).
5. Test hygiene: cleanups deleted live-domain job history; test workers drained live jobs — `1ab6dd4`, `3dee2af`.

## 5. Long-run stability (70 samples, 11 h wall clock, one reboot)

| Aspect | Observation |
|---|---|
| memory | server RSS 109–211 MB, no growth trend (210 → 140 → 161 MB across restarts) |
| queue | max age 9.6 h — a FIFO backlog on a single embedded worker, drained completely; 0 duplicate live jobs at every sample |
| jobs | 250 done; throughput 10–52/h (dip = reboot window); extract job avg 200 s, max 1,433 s |
| retries | 3 jobs retried; 13 partial-section retry jobs; backoff honoured |
| DLQ | 1 (the ILIKE crash), retried after the fix, now 0 |
| stuck | 1 (the reboot), auto-requeued after 60 min |
| scheduler | evaluation after the pipeline ran on time; revalidation churn found and fixed; discovery off by default |
| model | 1,266 extract calls, 2.2 M prompt / 1.35 M completion tokens, 6.9 h of model time, avg 20 s, max 190 s; 51 failed (38 timeouts, 3 malformed JSON, 10 counted twice by the retry wrapper) = 3.3 % |
| storage | DB 18 → 87 MB, raw docs 14.8 MB; no unbounded growth (changelog 18 k rows is the largest table) |
| errors | none outside the four defects above; health 200 at every sample |

Cost note: the last hour of the run was spent on three hopeless retries of the same five sections (each 300 s ×
2). Bounded, correct, but a candidate for a shorter per-section budget.

## 6. Restart resilience

**Did the platform safely recover from the machine restart? Yes.** Worker termination, interrupted job, partial
extractions and queued work all resumed under the original run id; nothing was duplicated or lost; the one lock
left behind expired and was requeued by the built-in mechanism.

## 7. Snapshot integrity

* New full snapshot **v18** (`ac6d7923…`, export schema 1.5, database schema 0012): 6,100 knowledge, 6,194 evidence,
  179 documents, 8,400 relationships, 566 examples, 464 negative, 2 conflicts; gate ok; `verify` ok with schema
  validation; **rebuilding produces the identical integrity hash** (v19).
* Delta **v20** (v16 → v18, delta@1.3): 6,052 added, 48 modified (the original items: re-checked + rescored), 0
  removed, 2 conflicts opened, 179 documents added; verify ok.
* `kp export apply v16 v20` reconstructed all 23 promised files **byte for byte** (`reconstruction ok: True`,
  derived renderings excluded by contract).
* AI Knowledge Source: 6,100 records, 0 schema problems, every record cited; usage 6,092 `cite` / 8 `caution`
  (4 conflicted, 4 awaiting revalidation) / 0 historical.

**snapshot + delta = target snapshot: confirmed.** Previous snapshots (v1–v17) untouched.

## 8. Knowledge quality

* Conflicts: 2 opened by the crawl (ALLSELECTED definition vs DAX Guide wording; "Burndown %" definitions across
  two Fabric pages), 4 items CONFLICTED, 4 dependents flagged; both wait for a reviewer — nothing was suppressed.
* Source independence: 3 items with two independent sources; 0 mirrors; SQLBI/DAX Guide content is EXTERNAL,
  never counted as official.
* Stale knowledge: 0 (no page changed during the run). Review flags: 0. Derived/synthesized: 66 (validator-passed
  examples marked EXPERIMENTALLY_VALIDATED count here). Negative knowledge: 466. Examples: 575.
* Weak spots: 645 items unclassified by taxonomy; the 15-page budget missed the authoritative CALCULATE page.

## 9. Remaining P2 items

Completed: P2.0 (all sub-steps), P2.1, P2.2, P2.3, P2.4, P2.5, P2.8 (controlled discovery), P2.9, P2.10, P2.11.
Partial: P2.6 (design + tier-0 guards; runner tiers 1–2 await approval), P2.7 (design + exposure guard; proxy/token
layer awaits approval), P2.8 auto-approval policy (designed, not built).
Deferred by decision: media, PDF, fuzzy source similarity, locator refresh after section reorders, quality
dashboards, per-section model budget tuning, taxonomy-matching improvements for the extractor.

## 10. Production readiness

**Beta** (up from late beta only in confidence, not in category). Reasons: the self-updating loop has now been run
at realistic scale on real sources, survived a machine restart, and every defect it exposed has a regression test;
snapshot, delta and reconstruction are proven on 6,100 items. Blockers to production-ready: no authentication
layer for anything beyond localhost (design only); executing validators have no sandbox (design only); a single
embedded worker (9.6 h queue age); answer quality depends on an 8B local model that omits required concepts
and occasionally times out on large sections; two open conflicts and five partial documents need a reviewer /
another crawl; catalogue entries need curation when publishers move pages.
