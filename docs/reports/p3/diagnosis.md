# P3 — Diagnosis: why the expanded corpus is not yet reliably retrievable (2026-09-15)

Inputs: P2 final report and comparison, `core/retrieval/search.py`, `core/retrieval/answer.py`,
`core/evaluation/{runner,checks}.py`, `domains/powerbi/evaluation.yaml`, `core/quality/scoring.py`,
`core/versioning/dependencies.py`, export schema; live queries against the 6,100-item corpus; and the new offline
retrieval harness (`kp eval retrieval`, `docs/reports/p3/retrieval-baseline-k8.json`, `-k20.json`).

## Offline retrieval baseline (current ranking, golden set 0.3.0, 11 answerable questions)

| Metric | K = 8 (what the answer model sees) | K = 20 |
|---|---:|---:|
| concept recall@K (share of required concepts present in top-K) | 0.742 | 0.833 |
| expected-concept hit rate (per concept) | 0.684 | 0.789 |
| precision@K (top-K on the question's topic branch) | 0.898 | 0.886 |
| MRR (first item carrying a required concept) | 0.560 | 0.560 |
| authoritative-source hit rate (2 questions declare one) | 0.50 | 0.50 |
| questions with every concept in top-K | 5 / 11 | 7 / 11 |
| mean retrieval latency | 127 ms | 116 ms |

Where each missing concept actually is (pool = 50 candidates before truncation to K):

| Question | Missing concept | In corpus | Rank in pool | Verdict |
|---|---|---:|---:|---|
| model-star-01 | fact table | 50 items | 11 | ranking (below K) |
| visual-calc-01 | PREVIOUS | 53 | 10 | ranking (below K) |
| model-bidi-01 | ambiguity / ambiguous | 2 / 8 | 26 / 10 | ranking (below K) |
| refresh-incremental-01 | query folding | 15 | 37 | ranking (deep in pool) |
| security-rls-01 | DAX filter | 7 | not in pool | candidate generation (lexical channel dead, see Q1/Q2) |
| dax-calculate-01 | context transition | 2 (both about ADDCOLUMNS) | not in pool | **corpus gap**: the CALCULATE page was never fetched |
| dax-calculate-01 | authoritative source | page not crawled | — | **collection budget**, not ranking |
| gateway-01 | "not reachable from the cloud" (expected wording) | 0 | — | corpus wording gap + answer scope |

So of the five "answer-generation failures" in P2, **four are retrieval-depth/ranking failures in disguise**: the
concept the answer lacks exists in the corpus and sits at rank 10–37, outside the eight items handed to the model.
The model cannot state what it was not shown.

## Answers to the twelve questions

**1. Why did the CALCULATE document lose the retrieval competition?** It never entered it. The DAX source is
crawled breadth-first from the function-reference hub with a 15-page budget for this run; the budget was spent on
the hub and the category pages (depth 1), and `calculate-function-dax` (depth 2) was never fetched. The CALCULATE
knowledge that does exist (an `ms-learn` definition from the filter-functions page and SQLBI definitions) loses
the *ranking* competition to "DAX user-defined function", "INFO.VIEW DAX functions" and "Many existing DAX
functions work in visual calculations", because the query words *DAX* and *function* match those statements
lexically and semantically while the definition "CALCULATE evaluates an expression in a modified filter context"
contains neither word. Nothing rewards the fact that its **subject is exactly the entity the question names**.

**2. What signals affect ranking today?** Exactly two, fused by reciprocal rank fusion (k = 60) over a pool of 50:
(a) PostgreSQL full-text rank of the *whole question* against subject + statement + explanation + topic, using
`websearch_to_tsquery`, which ANDs every term; (b) cosine similarity of the question embedding to the item
embedding. Ties broken by nothing. `min_confidence` exists but defaults to 0.

**3. Which signals are missing?** Entity / subject match; concept match on the query's key terms (not the whole
sentence); knowledge-type / role preference by intent (a "what does X do" wants the definition; "risks of"
wants negative-polarity items); source authority, source class and verification level (only inside
`confidence`, which is not used); relationship expansion (the definition an item depends on, examples of it);
diversity (the top-8 for the gateway question is three near-identical "standard gateway is recommended for …"
items); freshness / conflict status as a tie-breaker; product-version compatibility; topic affinity.

**4. Where is query intent represented?** Nowhere. The question string goes verbatim to both channels and to the
answer prompt. The golden set carries `negative: true` for two questions, but only the evaluator reads it.

**5. How are authoritative sources weighted?** Only at scoring time: `source_authority` is one of six factors of an
item's `confidence` (weight table in `quality/scoring.py`), and `verification_level` ≥ 2 needs authority ≥ 80.
Retrieval ignores both. An SQLBI item (authority 85) and a Microsoft Learn item (95) about the same claim rank
purely on text similarity.

**6. Can exact concept/entity matches outrank generic similarity?** Not today. The lexical channel is the only
place an exact match could win, and it is effectively switched off for natural-language questions: with AND
semantics "How does row-level security restrict data in Power BI?" matches **1** item and "What are the risks of
bidirectional cross-filtering?" matches **0** (the corpus writes *bi-directional*), so those questions are ranked
by vector similarity alone. Rewriting the query with OR semantics matches 3,332 and 21 items respectively — the
channel needs term selection and weighting, not just a different operator.

**7. How is the amount retrieved decided?** Fixed numbers: `limit = 8` (evaluation `eval_retrieval_k`, the API's
`AskRequest.limit`), `pool = 50`, `rrf_k = 60`. No query- or corpus-dependent cut-off.

**8. How is retrieved evidence passed to answer generation?** As a numbered block per item: status/confidence/
trust notes, polarity or EXAMPLE label, statement, explanation (≤ 500 chars), code (≤ 800), structured details.
No evidence excerpt, source name, authority, topic, or relationships. The prompt says "answer using only the items
above, citing them as [n]" — there is no plan, no notion of what the question requires, and no completeness check.

**9. How does the evaluator determine required concepts?** From the golden set only (`required_concepts` per
question); `check_required_concepts` is a normalised substring test on the answer text, gating (`GATING_CHECKS`).

**10. Why can a supported answer still fail?** Because "correct and supported" (the judge) and "contains the
required term" (the substring check) are independent gates and `passed` needs both. Four answers were judged
correct and supported yet omitted the term — in every one of those four the term was outside the top-8 (see the
table), so the check is measuring retrieval depth, not the model's wording. The evaluator has no separate
"completeness" metric; the gap is folded into `accuracy`.

**11. Retrieval vs generation.** Retrieval: model-star, visual-calc, model-bidi, refresh-incremental
(concept below K), security-rls (concept never enters the pool). Collection/corpus: dax-calculate (page not
crawled; *context transition* absent). Generation: gateway-01 (answered "which gateway" with cited true statements
instead of "when required"; the corpus lacks the "not reachable from the cloud" wording, but the answer also
never used the cloud-reachability items in the pool at rank 36) — and, partly, dax-calculate (an unsupported
environment claim). Wording-only: none — every missing concept was genuinely absent from the model's context.

**12. What can be fixed without changing the corpus?** Ranking (entity, concept, intent-type, authority,
verification, diversity), candidate generation (query normalisation + term-weighted lexical channel), context
assembly (more candidates, deduplicated, relationship-expanded), an evidence-grounded answer plan with a
completeness check and targeted regeneration, and separate reporting of completeness vs factual accuracy.
Not fixable without the corpus: the CALCULATE page (crawl depth/budget) and the gateway wording.
