"""P2.0.2: compare the small-KB baseline with the expanded corpus (same golden set, same evaluation code path).

Reads docs/reports/p2/baseline-metrics.json (recorded before the crawl, never overwritten), records the current
corpus metrics to docs/reports/p2/expanded-metrics.json and writes docs/reports/p2/comparison.md with corpus,
evaluation and per-question failure-class tables. Read-only with respect to the knowledge base.
"""

from __future__ import annotations

import json
import sys
import uuid
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from knowledge_platform.core.observability import ops_metrics  # noqa: E402
from knowledge_platform.db import session_scope  # noqa: E402
from knowledge_platform.models import EvaluationRun  # noqa: E402

REPORTS = Path(__file__).resolve().parents[1] / "docs" / "reports" / "p2"
CORPUS_ROWS = [
    ("documents", lambda k: k["documents"]["total"]),
    ("documents extracted", lambda k: k["documents"]["by_status"].get("EXTRACTED", 0)),
    ("sources (active)", lambda k: k["sources"]["active_enabled"]),
    ("knowledge items", lambda k: k["knowledge_items"]["total"]),
    ("  verified", lambda k: k["knowledge_items"]["by_status"].get("VERIFIED", 0)),
    ("  supported", lambda k: k["knowledge_items"]["by_status"].get("SUPPORTED", 0)),
    ("  conflicted", lambda k: k["knowledge_items"]["by_status"].get("CONFLICTED", 0)),
    ("  stale", lambda k: k["knowledge_items"]["by_status"].get("STALE", 0)),
    ("  candidate", lambda k: k["knowledge_items"]["by_status"].get("CANDIDATE", 0)),
    ("  rejected (dedup)", lambda k: k["knowledge_items"]["by_status"].get("REJECTED", 0)),
    ("  superseded", lambda k: k["knowledge_items"]["superseded"]),
    ("  negative knowledge", lambda k: k["knowledge_items"]["negative"]),
    ("  derived / synthesized", lambda k: k["knowledge_items"]["derived_or_synthesized"]),
    ("  examples", lambda k: k["knowledge_items"]["by_type"].get("example", 0)),
    ("  needs review", lambda k: k["knowledge_items"]["needs_review"]),
    ("  needs revalidation", lambda k: k["knowledge_items"]["needs_revalidation"]),
    ("evidence records", lambda k: k["evidence"]["total"]),
    ("  verified evidence", lambda k: k["evidence"]["verified"]),
    ("relationships", lambda k: k["relationships"]["total"]),
    ("conflicts open", lambda k: k["conflicts"].get("OPEN", 0)),
]
EVAL_ROWS = [
    ("accuracy", "accuracy"),
    ("coverage", "coverage"),
    ("retrieval precision", "retrieval_precision"),
    ("retrieval recall", "retrieval_recall"),
    ("evidence support", "evidence_support"),
    ("citation correctness", "citation_correctness"),
    ("hallucination rate", "hallucination_rate"),
    ("uncited rate", "uncited_rate"),
    ("unanswered rate", "unanswered_rate"),
    ("negative-knowledge coverage", "negative_coverage"),
    ("abstention correctness", "abstention_correctness"),
    ("mean latency ms", "mean_latency_ms"),
]
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
    return "—" if v is None else (f"{v:.4f}" if isinstance(v, float) else str(v))


def main(baseline_eval_id: str, expanded_eval_id: str) -> None:
    baseline = json.loads((REPORTS / "baseline-metrics.json").read_text(encoding="utf-8"))
    with session_scope() as s:
        current = ops_metrics(s, "powerbi")
        (REPORTS / "expanded-metrics.json").write_text(
            json.dumps(current, default=str, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        b_ev = s.get(EvaluationRun, uuid.UUID(baseline_eval_id))
        e_ev = s.get(EvaluationRun, uuid.UUID(expanded_eval_id))
        assert b_ev and e_ev
        b_res = {r.question_id: r for r in b_ev.results}
        e_res = {r.question_id: r for r in e_ev.results}
        kb0, kb1 = baseline["knowledge"], current["knowledge"]
        lines = ["# P2.0.2 — baseline vs expanded corpus", ""]
        lines += [
            f"Baseline metrics recorded before the crawl (`baseline-metrics.json`, eval run `{b_ev.id}` of "
            f"{b_ev.started_at:%Y-%m-%d %H:%M} UTC); expanded corpus after pipeline run 1ab22f74 (eval run "
            f"`{e_ev.id}` of {e_ev.started_at:%Y-%m-%d %H:%M} UTC, triggered {e_ev.triggered_by}). Same golden set "
            f"(dataset {e_ev.dataset_version}), same checks, same judge/answer models "
            f"({e_ev.config.get('models')}), answer prompt {e_ev.config.get('answer_version')}.",
            "",
            "## Corpus",
            "",
            "| Metric | Baseline | Expanded |",
            "|---|---:|---:|",
        ]
        for label, fn in CORPUS_ROWS:
            lines.append(f"| {label} | {fmt(fn(kb0))} | {fmt(fn(kb1))} |")
        lines += [
            f"| database size (MB) | {baseline['storage']['database_bytes'] / 1e6:.1f} | "
            f"{current['storage']['database_bytes'] / 1e6:.1f} |",
            f"| stored document bytes (MB) | {baseline['storage']['document_bytes'] / 1e6:.1f} | "
            f"{current['storage']['document_bytes'] / 1e6:.1f} |",
            "",
            "## Evaluation",
            "",
            "| Metric | Baseline | Expanded |",
            "|---|---:|---:|",
        ]
        for label, key in EVAL_ROWS:
            lines.append(f"| {label} | {fmt(b_ev.metrics.get(key))} | {fmt(e_ev.metrics.get(key))} |")
        lines += ["", "## Failure classes", "", "| Class | Baseline | Expanded |", "|---|---:|---:|"]
        bc, ec = Counter(b_ev.metrics.get("failure_classes", {})), Counter(e_ev.metrics.get("failure_classes", {}))
        for c in CLASSES:
            lines.append(f"| {c} | {bc.get(c, 0)} | {ec.get(c, 0)} |")
        header = "| Question | Baseline | Expanded | Expanded causes |"
        lines += ["", "## Per question", "", header, "|---|---|---|---|"]
        for qid in sorted(set(b_res) | set(e_res)):
            b, e = b_res.get(qid), e_res.get(qid)

            def cell(r):
                if r is None:
                    return "(not in set)"
                return "pass" if r.passed else (r.failure_class or "fail")

            lines.append(f"| {qid} | {cell(b)} | {cell(e)} | {', '.join(e.failure_causes) if e else ''} |")
        (REPORTS / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        print("\n".join(lines))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
