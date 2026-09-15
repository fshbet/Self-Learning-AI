# P2.0.2 — baseline vs expanded corpus

Baseline metrics recorded before the crawl (`baseline-metrics.json`, eval run `d294dfd1-8da5-4195-914b-88a896b179c9` of 2026-09-14 03:10 UTC); expanded corpus after pipeline run 1ab22f74 (eval run `6b619f0a-5cae-4145-b6c5-00e47e3bb990` of 2026-09-14 23:05 UTC, triggered after-run:1ab22f74-6572-4554-9533-4aba644fe24b). Same golden set (dataset 0.3.0), same checks, same judge/answer models ({'extract': 'qwen3:8b', 'answer': 'qwen3:8b', 'judge': 'qwen3:8b'}), answer prompt answer@1.1.

## Corpus

| Metric | Baseline | Expanded |
|---|---:|---:|
| documents | 3 | 186 |
| documents extracted | 3 | 181 |
| sources (active) | 12 | 18 |
| knowledge items | 48 | 6143 |
|   verified | 48 | 6096 |
|   supported | 0 | 0 |
|   conflicted | 0 | 4 |
|   stale | 0 | 0 |
|   candidate | 0 | 0 |
|   rejected (dedup) | 0 | 43 |
|   superseded | 0 | 0 |
|   negative knowledge | 2 | 466 |
|   derived / synthesized | 2 | 66 |
|   examples | 0 | 575 |
|   needs review | 0 | 0 |
|   needs revalidation | 0 | 4 |
| evidence records | 50 | 6237 |
|   verified evidence | 50 | 6237 |
| relationships | 0 | 8400 |
| conflicts open | 0 | 2 |
| database size (MB) | 11.9 | 87.3 |
| stored document bytes (MB) | 0.1 | 14.8 |

## Evaluation

| Metric | Baseline | Expanded |
|---|---:|---:|
| accuracy | 0.4167 | 0.4167 |
| coverage | 0.4545 | 1.0000 |
| retrieval precision | 0.4545 | 0.8977 |
| retrieval recall | 0.5000 | 0.5000 |
| evidence support | 0.4000 | 0.8182 |
| citation correctness | 0.5455 | 1.0000 |
| hallucination rate | 0.6000 | 0.1818 |
| uncited rate | 0.3636 | 0.0000 |
| unanswered rate | 0.0909 | 0.0000 |
| negative-knowledge coverage | 1.0000 | 1.0000 |
| abstention correctness | 1.0000 | 1.0000 |
| mean latency ms | 10126 | 10554 |

## Failure classes

| Class | Baseline | Expanded |
|---|---:|---:|
| coverage_failure | 6 | 0 |
| retrieval_failure | 1 | 2 |
| answer_generation_failure | 0 | 5 |
| uncited_answer | 0 | 0 |
| citation_failure | 0 | 0 |
| validation_failure | 0 | 0 |
| expected_abstention | 1 | 1 |

## Per question

| Question | Baseline | Expanded | Expanded causes |
|---|---|---|---|
| abstain-01 | pass | pass |  |
| dax-calculate-01 | retrieval_failure | retrieval_failure | missing_concepts, authoritative_source_not_retrieved, judge_incorrect, judge_unsupported, hallucination |
| dax-divide-01 | pass | pass |  |
| gateway-01 | coverage_failure | answer_generation_failure | judge_incorrect, judge_unsupported, hallucination |
| m-folding-01 | coverage_failure | pass |  |
| model-bidi-01 | coverage_failure | retrieval_failure | missing_concepts, off_topic_retrieval |
| model-star-01 | coverage_failure | answer_generation_failure | missing_concepts |
| negative-divide-01 | pass | pass |  |
| negative-versions-01 | pass | pass |  |
| refresh-incremental-01 | coverage_failure | answer_generation_failure | missing_concepts |
| security-rls-01 | coverage_failure | answer_generation_failure | missing_concepts |
| visual-calc-01 | pass | answer_generation_failure | missing_concepts |
