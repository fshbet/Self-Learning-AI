# P3 — evaluation runs A / B / C / D

Same corpus, same golden set, same models and judge; only the answer mode differs. **A p2** = run `87e5255c-74d8-4be9-9317-8605ffa6e62c` (2026-09-15 04:27 UTC, mode `p2`, retrieval `retrieval@1.0`, context K 8, prompt answer@1.2); **B p3-retrieval** = run `10779553-b94c-46f6-9444-6bb4e7048bcb` (2026-09-15 04:30 UTC, mode `p3-retrieval`, retrieval `retrieval@2.0`, context K 12, prompt answer@1.2); **C p3-plan** = run `ca554e4d-a0ec-4444-8671-64a567765167` (2026-09-15 04:44 UTC, mode `p3-plan`, retrieval `retrieval@2.0`, context K 12, prompt answer@1.2); **D p3** = run `288ecc52-962c-41e9-b658-51d2a77259cd` (2026-09-15 04:46 UTC, mode `p3`, retrieval `retrieval@2.0`, context K 12, prompt answer@1.2).

## Metrics

| Metric | A p2 | B p3-retrieval | C p3-plan | D p3 |
|---|---:|---:|---:|---:|
| accuracy (gating, unchanged definition) | 0.333 | 0.667 | 0.750 | 0.667 |
| factual accuracy (judge correct + supported + citations ok) | 0.727 | 1.000 | 1.000 | 0.909 |
| completeness (required concepts in the answer) | 0.455 | 0.636 | 0.727 | 0.727 |
| plan completeness (answer covers its own plan) | — | 0.692 | 0.926 | 0.944 |
| retrieval precision | 0.898 | 0.874 | 0.874 | 0.874 |
| retrieval recall (authoritative sources) | 0.500 | 0.500 | 0.500 | 0.500 |
| authoritative-source hit rate | 0.500 | 0.500 | 0.500 | 0.500 |
| MRR (first required concept) | 0.471 | 0.666 | 0.666 | 0.666 |
| expected-concept hit rate (in context) | 0.632 | 0.789 | 0.789 | 0.789 |
| coverage | 1.000 | 1.000 | 1.000 | 1.000 |
| citation correctness | 1.000 | 1.000 | 1.000 | 1.000 |
| evidence support | 0.727 | 1.000 | 1.000 | 0.909 |
| hallucination rate | 0.364 | 0.000 | 0.182 | 0.273 |
| uncited rate | 0.000 | 0.000 | 0.000 | 0.000 |
| unanswered rate | 0.000 | 0.000 | 0.000 | 0.000 |
| abstention correctness | 1.000 | 1.000 | 1.000 | 1.000 |
| negative-knowledge coverage | 1.000 | 1.000 | 1.000 | 1.000 |
| regeneration rate | 0.000 | 0.000 | 0.000 | 0.182 |
| regeneration accepted rate | 0.000 | 0.000 | 0.000 | 0.182 |
| answer-generation failure rate | 0.545 | 0.273 | 0.182 | 0.273 |
| LLM calls per question | 1.000 | 1.000 | 1.000 | 1.182 |
| mean latency ms (question, incl. judge) | 12036 | 12585 | 13355 | 14239 |

## Stage timings (mean ms per question)

| Stage | A p2 | B p3-retrieval | C p3-plan | D p3 |
|---|---:|---:|---:|---:|
| analysis | 0 | 25 | 21 | 23 |
| embedding | — | — | 2030 | 2219 |
| candidates | 0 | 3387 | 844 | 818 |
| ranking | 0 | 7 | 6 | 6 |
| diversity | 0 | 0 | 0 | 0 |
| expansion | 0 | 16 | 15 | 17 |
| context | 0 | 13 | 11 | 15 |
| generation | 0 | 7994 | 9308 | 9153 |
| regeneration | 0 | 0 | 0 | 3070 |
| total | 0 | 11448 | 12243 | 12817 |

## Failure classes

| Class | A p2 | B p3-retrieval | C p3-plan | D p3 |
|---|---:|---:|---:|---:|
| coverage_failure | 0 | 0 | 0 | 0 |
| retrieval_failure | 2 | 1 | 1 | 1 |
| answer_generation_failure | 6 | 3 | 2 | 3 |
| uncited_answer | 0 | 0 | 0 | 0 |
| citation_failure | 0 | 0 | 0 | 0 |
| validation_failure | 0 | 0 | 0 | 0 |
| expected_abstention | 1 | 1 | 1 | 1 |

## Per question

| Question | A p2 | B p3-retrieval | C p3-plan | D p3 | D causes |
|---|---|---|---|---|---|
| abstain-01 | pass | pass | pass | pass |  |
| dax-calculate-01 | retrieval_failure | retrieval_failure | retrieval_failure | retrieval_failure | missing_concepts, authoritative_source_not_retrieved, hallucination |
| dax-divide-01 | pass | pass | pass | pass ⟲ |  |
| gateway-01 | answer_generation_failure | pass | pass | pass |  |
| m-folding-01 | answer_generation_failure | pass | pass | pass |  |
| model-bidi-01 | retrieval_failure | answer_generation_failure | pass | pass |  |
| model-star-01 | answer_generation_failure | pass | pass | answer_generation_failure | judge_incorrect, judge_unsupported, hallucination |
| negative-divide-01 | pass | pass | pass | pass ⟲ |  |
| negative-versions-01 | pass | pass | pass | pass |  |
| refresh-incremental-01 | answer_generation_failure | answer_generation_failure | answer_generation_failure | answer_generation_failure | missing_concepts |
| security-rls-01 | answer_generation_failure | answer_generation_failure | answer_generation_failure | answer_generation_failure | missing_concepts |
| visual-calc-01 | answer_generation_failure | pass | pass | pass |  |

⟲ = a targeted regeneration was attempted on that question.
