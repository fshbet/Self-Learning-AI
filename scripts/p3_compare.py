"""P3 (ADR 0006): compare evaluation runs A (P2 baseline answerer), B (P3 retrieval only), C (retrieval + plan +
completeness, no regeneration) and D (final: + one targeted regeneration) on the same corpus and golden set.

Usage: ``python scripts/p3_compare.py <run-A-id> <run-B-id> <run-C-id> <run-D-id>`` — writes
``docs/reports/p3/comparison.md`` and ``docs/reports/p3/comparison.json``. Read-only with respect to the knowledge
base; the runs are read from the ``evaluation_runs`` table (``kp eval run powerbi --mode …`` stores them).
"""

from __future__ import annotations

import json
import sys
import uuid
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from knowledge_platform.db import session_scope  # noqa: E402
from knowledge_platform.models import EvaluationRun  # noqa: E402

REPORTS = Path(__file__).resolve().parents[1] / "docs" / "reports" / "p3"
LABELS = ("A p2", "B p3-retrieval", "C p3-plan", "D p3")
EVAL_ROWS = [
    ("accuracy (gating, unchanged definition)", "accuracy"),
    ("factual accuracy (judge correct + supported + citations ok)", "factual_accuracy"),
    ("completeness (required concepts in the answer)", "completeness"),
    ("plan completeness (answer covers its own plan)", "plan_completeness"),
    ("retrieval precision", "retrieval_precision"),
    ("retrieval recall (authoritative sources)", "retrieval_recall"),
    ("authoritative-source hit rate", "authoritative_hit_rate"),
    ("MRR (first required concept)", "mrr"),
    ("expected-concept hit rate (in context)", "expected_concept_hit_rate"),
    ("coverage", "coverage"),
    ("citation correctness", "citation_correctness"),
    ("evidence support", "evidence_support"),
    ("hallucination rate", "hallucination_rate"),
    ("uncited rate", "uncited_rate"),
    ("unanswered rate", "unanswered_rate"),
    ("abstention correctness", "abstention_correctness"),
    ("negative-knowledge coverage", "negative_coverage"),
    ("regeneration rate", "regeneration_rate"),
    ("regeneration accepted rate", "regeneration_accepted_rate"),
    ("answer-generation failure rate", "answer_generation_failure_rate"),
    ("LLM calls per question", "llm_calls_per_question"),
    ("mean latency ms (question, incl. judge)", "mean_latency_ms"),
]
TIMINGS = (
    "analysis",
    "candidates",
    "ranking",
    "diversity",
    "expansion",
    "context",
    "generation",
    "regeneration",
    "total",
)
CLASSES = (
    "coverage_failure",
    "retrieval_failure",
    "answer_generation_failure",
    "uncited_answer",
    "citation_failure",
    "validation_failure",
    "expected_abstention",
)


def fmt(v):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def main(ids: list[str]) -> None:
    with session_scope() as s:
        runs = [s.get(EvaluationRun, uuid.UUID(i)) for i in ids]
        assert all(runs), "unknown evaluation run id"
        lines = ["# P3 — evaluation runs A / B / C / D", ""]
        lines.append(
            "Same corpus, same golden set, same models and judge; only the answer mode differs. "
            + "; ".join(
                f"**{lab}** = run `{r.id}` ({r.started_at:%Y-%m-%d %H:%M} UTC, mode `{r.config.get('answer_mode')}`, "
                f"retrieval `{r.config.get('retrieval_version')}`, context K {r.config.get('context_k')}, "
                f"prompt {r.config.get('answer_version')})"
                for lab, r in zip(LABELS, runs, strict=True)
            )
            + "."
        )
        lines += ["", "## Metrics", "", "| Metric | " + " | ".join(LABELS) + " |", "|---|" + "---:|" * len(runs)]
        for label, key in EVAL_ROWS:
            lines.append(f"| {label} | " + " | ".join(fmt(r.metrics.get(key)) for r in runs) + " |")
        lines += [
            "",
            "## Stage timings (mean ms per question)",
            "",
            "| Stage | " + " | ".join(LABELS) + " |",
            "|---|" + "---:|" * len(runs),
        ]
        for key in TIMINGS:
            lines.append(
                f"| {key} | " + " | ".join(fmt((r.metrics.get("timings_ms") or {}).get(key)) for r in runs) + " |"
            )
        lines += ["", "## Failure classes", "", "| Class | " + " | ".join(LABELS) + " |", "|---|" + "---:|" * len(runs)]
        counters = [Counter(r.metrics.get("failure_classes", {})) for r in runs]
        for c in CLASSES:
            lines.append(f"| {c} | " + " | ".join(str(cnt.get(c, 0)) for cnt in counters) + " |")
        lines += [
            "",
            "## Per question",
            "",
            "| Question | " + " | ".join(LABELS) + " | D causes |",
            "|---|" + "---|" * len(runs) + "---|",
        ]
        by_run = [{res.question_id: res for res in r.results} for r in runs]
        for qid in sorted(by_run[0]):
            cells = []
            for res_map in by_run:
                res = res_map.get(qid)
                if res is None:
                    cells.append("(missing)")
                    continue
                meta = res.checks.get("answer_meta", {})
                regen = (meta.get("regeneration") or {}).get("triggered")
                cells.append(("pass" if res.passed else (res.failure_class or "fail")) + (" ⟲" if regen else ""))
            d = by_run[-1].get(qid)
            lines.append(f"| {qid} | " + " | ".join(cells) + f" | {', '.join(d.failure_causes) if d else ''} |")
        lines += ["", "⟲ = a targeted regeneration was attempted on that question.", ""]
        out = {
            lab: {
                "id": str(r.id),
                "config": r.config,
                "metrics": r.metrics,
                "results": [
                    {
                        "question_id": res.question_id,
                        "passed": res.passed,
                        "failure_class": res.failure_class,
                        "failure_causes": res.failure_causes,
                        "answer_meta": res.checks.get("answer_meta"),
                    }
                    for res in r.results
                ],
            }
            for lab, r in zip(LABELS, runs, strict=True)
        }
        (REPORTS / "comparison.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
        (REPORTS / "comparison.json").write_text(
            json.dumps(out, default=str, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        print("\n".join(lines))


if __name__ == "__main__":
    main(sys.argv[1:5])
