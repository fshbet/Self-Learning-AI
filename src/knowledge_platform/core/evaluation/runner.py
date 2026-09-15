"""Automated self-evaluation runner (req. 15, 16, 38).

    golden questions → retrieval → answer → mechanical checks → LLM judge → result rows
                                                      ↓
                                 metrics → regression vs previous run → findings

The runner never modifies knowledge (req. 16): it produces results, metrics and *suggested* actions.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import Counter
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ... import __version__
from ...adapters import get_embedder
from ...config import get_settings
from ...models import Document, EvaluationResult, EvaluationRun, KnowledgeItem, RunStatus, utcnow
from ..extraction.chunker import CHUNKER_VERSION
from ..extraction.prompts import ANSWER_VERSION, JUDGE_SCHEMA, JUDGE_SYSTEM, JUDGE_USER, JUDGE_VERSION, PROMPT_VERSION
from ..llm_service import call_json, model_for
from ..plugins.base import DomainPlugin, EvalQuestion
from ..quality.scoring import SCORING_RULE_VERSION
from ..retrieval.answer import CONTEXT_K, answer_question
from ..retrieval.retrieve import RETRIEVAL_VERSION
from .checks import (
    GATING_CHECKS,
    SUGGESTED_ACTIONS,
    CitedItem,
    RetrievedItem,
    failure_causes,
    failure_class,
    run_all_checks,
)

log = logging.getLogger(__name__)

METRIC_KEYS = (
    "accuracy",
    "citation_correctness",
    "evidence_support",
    "retrieval_precision",
    "retrieval_recall",
    "hallucination_rate",
    "abstention_correctness",
    "freshness_ok",
    "contradiction_handling",
    "version_correctness",
    "validator_success",
    "negative_coverage",
    "coverage",
    "unanswered_rate",
    "uncited_rate",
)
REGRESSION_METRICS = ("accuracy", "citation_correctness")


# ----------------------------------------------------------------------------- helpers


def _item_facts(session: Session, ids: list[uuid.UUID]) -> dict[str, dict[str, Any]]:
    """Evidence/status facts for cited and retrieved items, in one query."""
    if not ids:
        return {}
    items = (
        session.execute(
            select(KnowledgeItem).where(KnowledgeItem.id.in_(ids)).options(selectinload(KnowledgeItem.evidence))
        )
        .scalars()
        .all()
    )
    out: dict[str, dict[str, Any]] = {}
    for it in items:
        out[str(it.id)] = {
            "status": it.status,
            "topic": it.topic,
            "product_version": it.product_version,
            "polarity": it.polarity,
            "evidence_verified": any(e.verified for e in it.evidence if e.evidence_type == "extraction"),
            "evidence_urls": [e.url for e in it.evidence if e.url],
            "validator_results": [bool(e.details.get("passed")) for e in it.evidence if e.evidence_type == "validator"],
            "statement": it.statement,
        }
    return out


def _coverage(session: Session, plugin: DomainPlugin, q: EvalQuestion) -> dict[str, Any] | None:
    """Does the knowledge base contain relevant knowledge for this question at all (audit P0.6)?

    Three independent signals, each only when the question declares it: live items filed under the question's
    exact topic, at least one of its authoritative sources crawled as a document, and *all* of its required
    concepts appearing in live statements. Any positive signal = covered; none = coverage failure; no signals
    declared = n/a. This is about *existence* of knowledge — retrieval quality is measured separately.
    """
    live = KnowledgeItem.status.in_(["SUPPORTED", "VERIFIED", "CONFLICTED", "STALE"])
    signals: dict[str, Any] = {}
    if q.topic:
        top = q.topic.split("/")[0]
        n_topic = session.execute(
            select(func.count())
            .select_from(KnowledgeItem)
            .where(KnowledgeItem.domain_id == plugin.id, live, KnowledgeItem.topic.ilike(f"{q.topic}%"))
        ).scalar_one()
        n_branch = session.execute(
            select(func.count())
            .select_from(KnowledgeItem)
            .where(KnowledgeItem.domain_id == plugin.id, live, KnowledgeItem.topic.ilike(f"{top}%"))
        ).scalar_one()
        # only the exact topic counts as coverage; the branch total is context (a whole area may be missing)
        signals["topic_items"] = {"topic": n_topic, "branch": n_branch, "ok": n_topic > 0}
    if q.authoritative_sources:
        crawled = 0
        for url in q.authoritative_sources:
            key = url.split("#")[0].rstrip("/")
            hit = session.execute(
                select(func.count())
                .select_from(Document)
                .where(Document.domain_id == plugin.id, Document.url.ilike(f"{key}%"))
            ).scalar_one()
            crawled += 1 if hit else 0
        signals["sources_crawled"] = {"crawled": crawled, "of": len(q.authoritative_sources), "ok": crawled > 0}
    if q.required_concepts:
        hits = 0
        for concept in q.required_concepts:
            found = session.execute(
                select(func.count())
                .select_from(KnowledgeItem)
                .where(
                    KnowledgeItem.domain_id == plugin.id,
                    live,
                    or_(
                        KnowledgeItem.statement.ilike(f"%{concept}%"),
                        KnowledgeItem.explanation.ilike(f"%{concept}%"),
                        KnowledgeItem.subject.ilike(f"%{concept}%"),
                    ),
                )
            ).scalar_one()
            hits += 1 if found else 0
        # required concepts are required: one of two present is a hint, not coverage
        signals["concept_hits"] = {
            "found": hits,
            "of": len(q.required_concepts),
            "ok": hits == len(q.required_concepts),
        }
    if not signals:
        return None
    positives = sum(1 for v in signals.values() if v["ok"])
    covered = positives > 0
    detail = "; ".join(
        f"{k}: {v.get('topic', v.get('crawled', v.get('found')))}"
        + (f"/{v['of']}" if "of" in v else (f" (+{v['branch']} in branch)" if "branch" in v else ""))
        for k, v in signals.items()
    )
    return {
        "ok": covered,
        "detail": ("relevant knowledge present — " if covered else "no relevant knowledge in the base — ") + detail,
        "value": positives / len(signals),
        "signals": signals,
    }


def _judge(
    session: Session, plugin: DomainPlugin, q: EvalQuestion, answer: str, cited_texts: list[str]
) -> dict[str, Any]:
    try:
        data = call_json(
            purpose="reason",
            system=JUDGE_SYSTEM.format(domain_name=plugin.name),
            user=JUDGE_USER.format(
                question=q.question,
                expected=q.expected_answer or "(none given)",
                alternatives="; ".join(q.acceptable_alternatives) or "(none)",
                items="\n".join(cited_texts) or "(none)",
                answer=answer,
            ),
            schema=JUDGE_SCHEMA,
            session=session,
        )
        data["version"] = JUDGE_VERSION
        return data
    except Exception as exc:  # judging must never abort the run
        log.warning("judge failed for %s: %s", q.id, exc)
        return {"error": str(exc)[:300], "version": JUDGE_VERSION}


def _config_snapshot(session: Session, plugin: DomainPlugin, mode: str = "p3") -> dict[str, Any]:
    s = get_settings()
    counts = dict(
        session.execute(
            select(KnowledgeItem.status, func.count())
            .where(KnowledgeItem.domain_id == plugin.id)
            .group_by(KnowledgeItem.status)
        ).all()
    )
    return {
        "platform_version": __version__,
        "plugin_version": plugin.manifest.version,
        "models": {"extract": model_for("extract"), "answer": model_for("answer"), "judge": model_for("reason")},
        "embedding": get_embedder().identity,
        "prompt_version": PROMPT_VERSION,
        "answer_version": ANSWER_VERSION,
        "judge_version": JUDGE_VERSION,
        "chunker_version": CHUNKER_VERSION,
        "scoring_rule_version": SCORING_RULE_VERSION,
        "retrieval_k": s.eval_retrieval_k,
        "answer_mode": mode,
        "retrieval_version": RETRIEVAL_VERSION if mode.startswith("p3") else "retrieval@1.0",
        "context_k": CONTEXT_K if mode.startswith("p3") else s.eval_retrieval_k,
        "knowledge_counts": {str(k): v for k, v in counts.items()},
    }


# ----------------------------------------------------------------------------- per question


def evaluate_question(
    session: Session, plugin: DomainPlugin, q: EvalQuestion, *, k: int, mode: str = "p3"
) -> EvaluationResult:
    started = time.perf_counter()
    ans = answer_question(session, plugin, q.question, limit=k if mode == "p2" else None, mode=mode)
    facts = _item_facts(session, [uuid.UUID(r["id"]) for r in ans.retrieved])
    retrieved = [
        RetrievedItem(
            id=r["id"],
            topic=facts.get(r["id"], {}).get("topic", ""),
            evidence_urls=facts.get(r["id"], {}).get("evidence_urls", []),
        )
        for r in ans.retrieved
    ]
    cited = [
        CitedItem(
            id=c["id"],
            n=c["n"],
            status=facts.get(c["id"], {}).get("status", c.get("status", "")),
            topic=facts.get(c["id"], {}).get("topic", ""),
            evidence_verified=facts.get(c["id"], {}).get("evidence_verified", False),
            evidence_urls=facts.get(c["id"], {}).get("evidence_urls", []),
            validator_results=facts.get(c["id"], {}).get("validator_results", []),
            product_version=facts.get(c["id"], {}).get("product_version"),
            polarity=facts.get(c["id"], {}).get("polarity", "positive"),
        )
        for c in ans.citations
    ]
    checks = run_all_checks(
        answer=ans.answer,
        no_results=ans.no_results,
        question=q,
        cited=cited,
        retrieved=retrieved,
        coverage=_coverage(session, plugin, q),
    )

    judge: dict[str, Any] = {}
    # every real answer is judged — cited or not (audit P0.5): an uncited answer must not escape as an abstention
    if not q.expect_abstain and checks["answer_kind"]["kind"] != "abstention":
        cited_texts = [f"[{c['n']}] {facts.get(c['id'], {}).get('statement', c['statement'])}" for c in ans.citations]
        if not cited_texts:  # uncited answer: the judge sees what *was* retrieved and can name the invented claims
            cited_texts = [f"(retrieved, not cited) {r['statement']}" for r in ans.retrieved]
        judge = _judge(session, plugin, q, ans.answer, cited_texts)

    # the answerer's own account (plan, completeness, regeneration, timings): informational, never gating
    checks["answer_meta"] = {
        "ok": True,
        "detail": f"mode {ans.mode}; {ans.llm_calls} model call(s)"
        + (f"; regenerated ({ans.regeneration.get('reason')})" if (ans.regeneration or {}).get("triggered") else ""),
        "mode": ans.mode,
        "plan": ans.plan,
        "completeness": ans.completeness,
        "regeneration": ans.regeneration,
        "retrieval": ans.retrieval,
        "timings_ms": ans.timings_ms,
        "llm_calls": ans.llm_calls,
    }
    gating_ok = all(checks[name]["ok"] for name in GATING_CHECKS if name in checks)
    if q.expect_abstain:
        passed = gating_ok
    else:
        judge_ok = (
            bool(judge.get("correct")) and bool(judge.get("supported_by_citations")) if "error" not in judge else True
        )
        passed = gating_ok and judge_ok
    causes = failure_causes(checks, judge, q.expect_abstain) if not passed else []
    klass = failure_class(checks, judge, q.expect_abstain, passed)

    return EvaluationResult(
        question_id=q.id,
        question=q.question,
        expected_answer=q.expected_answer,
        answer=ans.answer,
        retrieved=ans.retrieved,
        citations=ans.citations,
        checks=checks,
        judge=judge,
        passed=passed,
        failure_causes=causes,
        failure_class=klass,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


# ----------------------------------------------------------------------------- metrics


def _rate(values: list[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def compute_metrics(results: list[EvaluationResult], questions: list[EvalQuestion]) -> dict[str, Any]:
    by_id = {q.id: q for q in questions}
    answerable = [r for r in results if not by_id[r.question_id].expect_abstain]
    abstain = [r for r in results if by_id[r.question_id].expect_abstain]

    def ok(r: EvaluationResult, name: str) -> float | None:
        c = r.checks.get(name)
        return None if c is None else (1.0 if c["ok"] else 0.0)

    def val(r: EvaluationResult, name: str) -> float | None:
        c = r.checks.get(name)
        return None if c is None else c.get("value")

    judged = [r for r in answerable if r.judge and "error" not in r.judge]
    metrics: dict[str, Any] = {
        "questions": len(results),
        "passed": sum(1 for r in results if r.passed),
        "accuracy": _rate([1.0 if r.passed else 0.0 for r in results]),
        "citation_correctness": _rate([ok(r, "citations") for r in answerable]),
        "evidence_support": _rate([1.0 if r.judge.get("supported_by_citations") else 0.0 for r in judged]),
        "retrieval_precision": _rate([val(r, "retrieval_precision") for r in results]),
        "retrieval_recall": _rate([val(r, "retrieval_recall") for r in results]),
        "hallucination_rate": _rate([1.0 if r.judge.get("hallucinated_claims") else 0.0 for r in judged]),
        "abstention_correctness": _rate([ok(r, "abstention") for r in abstain]) if abstain else None,
        "freshness_ok": _rate([ok(r, "freshness") for r in answerable]),
        "contradiction_handling": _rate([ok(r, "contradictions") for r in answerable]),
        "version_correctness": _rate([ok(r, "version") for r in answerable if by_id[r.question_id].expected_version]),
        "validator_success": _rate([val(r, "validators") for r in answerable]),
        "negative_coverage": _rate([val(r, "negative_knowledge") for r in answerable if by_id[r.question_id].negative]),
        "unanswered_rate": _rate([1.0 if r.checks["abstention"].get("abstained") else 0.0 for r in answerable]),
        "uncited_rate": _rate(
            [1.0 if r.checks.get("answer_kind", {}).get("kind") == "uncited_answer" else 0.0 for r in answerable]
        ),
        "coverage": _rate(
            [ok(r, "coverage") if r.checks.get("coverage", {}).get("value") is not None else None for r in answerable]
        ),
        "failure_classes": dict(Counter(r.failure_class for r in results if r.failure_class)),
        "mean_latency_ms": int(sum(r.latency_ms for r in results) / len(results)) if results else 0,
        "failure_causes": dict(Counter(c for r in results for c in r.failure_causes)),
    }
    # --- P3 (ADR 0006): reported separately, additive to the metrics above; `accuracy` keeps its definition
    metrics["factual_accuracy"] = _rate(
        [
            1.0
            if (
                r.judge.get("correct")
                and r.judge.get("supported_by_citations")
                and r.checks.get("citations", {}).get("ok")
                and r.checks.get("must_not_contain", {}).get("ok", True)
            )
            else 0.0
            for r in judged
        ]
    )
    with_concepts = [r for r in answerable if by_id[r.question_id].required_concepts]
    metrics["completeness"] = _rate([ok(r, "required_concepts") for r in with_concepts])
    metrics["mrr"] = _rate([_first_concept_rank(r, by_id[r.question_id]) for r in with_concepts])
    metrics["expected_concept_hit_rate"] = _rate(
        [
            1.0 if _concept_in_retrieved(r, c) else 0.0
            for r in with_concepts
            for c in by_id[r.question_id].required_concepts
        ]
    )
    with_sources = [r for r in answerable if by_id[r.question_id].authoritative_sources]
    metrics["authoritative_hit_rate"] = _rate(
        [1.0 if (val(r, "retrieval_recall") or 0) > 0 else 0.0 for r in with_sources]
    )
    metas = [r.checks.get("answer_meta", {}) for r in answerable]
    metrics["regeneration_rate"] = _rate(
        [1.0 if (m.get("regeneration") or {}).get("triggered") else 0.0 for m in metas]
    )
    metrics["regeneration_accepted_rate"] = _rate(
        [1.0 if (m.get("regeneration") or {}).get("accepted") else 0.0 for m in metas]
    )
    metrics["plan_completeness"] = _rate(
        [
            (m.get("completeness") or {}).get("score")
            for m in metas
            if (m.get("completeness") or {}).get("score") is not None
        ]
    )
    metrics["answer_generation_failure_rate"] = _rate(
        [1.0 if r.failure_class == "answer_generation_failure" else 0.0 for r in answerable]
    )
    metrics["llm_calls_per_question"] = _rate([float(m.get("llm_calls") or 1) for m in metas])
    timing_keys = (
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
    metrics["timings_ms"] = {
        key: int(_rate([float(m["timings_ms"][key]) for m in metas if key in (m.get("timings_ms") or {})]) or 0)
        for key in timing_keys
    }
    return metrics


def _concept_in_retrieved(r: EvaluationResult, concept: str) -> bool:
    c = concept.lower()
    return any(c in (row.get("statement") or "").lower() for row in (r.retrieved or []))


def _first_concept_rank(r: EvaluationResult, q: EvalQuestion) -> float:
    for row in r.retrieved or []:
        text_ = (row.get("statement") or "").lower()
        if any(c.lower() in text_ for c in q.required_concepts):
            return 1.0 / int(row.get("n") or 1)
    return 0.0


def detect_regression(
    current: dict[str, Any], baseline: dict[str, Any] | None, threshold: float
) -> tuple[bool, dict[str, Any]]:
    if not baseline:
        return False, {"reason": "no baseline"}
    details: dict[str, Any] = {"threshold": threshold, "metrics": {}}
    regressed = False
    for key in REGRESSION_METRICS:
        cur, base = current.get(key), baseline.get(key)
        if cur is None or base is None:
            continue
        delta = round(cur - base, 4)
        details["metrics"][key] = {"baseline": base, "current": cur, "delta": delta}
        if delta < -threshold:
            regressed = True
    return regressed, details


def build_findings(results: list[EvaluationResult]) -> list[dict[str, Any]]:
    """Failure analysis grouped by cause with suggested actions (req. 16)."""
    grouped: dict[str, list[str]] = {}
    for r in results:
        for c in r.failure_causes:
            grouped.setdefault(c, []).append(r.question_id)
    return [
        {"cause": cause, "questions": qids, "count": len(qids), "action": SUGGESTED_ACTIONS.get(cause, "")}
        for cause, qids in sorted(grouped.items(), key=lambda kv: -len(kv[1]))
    ]


# ----------------------------------------------------------------------------- run


def run_evaluation(
    session: Session,
    plugin: DomainPlugin,
    *,
    triggered_by: str = "cli",
    run_id: uuid.UUID | None = None,
    question_ids: list[str] | None = None,
    mode: str = "p3",
) -> EvaluationRun:
    settings = get_settings()
    questions = plugin.evaluation_set()
    if question_ids:
        questions = [q for q in questions if q.id in set(question_ids)]
    dataset_version = plugin.evaluation_version()

    ev = EvaluationRun(
        domain_id=plugin.id,
        dataset_version=dataset_version,
        triggered_by=triggered_by,
        run_id=run_id,
        config=_config_snapshot(session, plugin, mode),
    )
    session.add(ev)
    session.commit()  # the run row is visible (RUNNING) while questions are evaluated
    if not questions:
        ev.status = RunStatus.FAILED
        ev.error = "the plugin has no evaluation questions"
        ev.finished_at = utcnow()
        session.flush()
        return ev

    baseline = session.execute(
        select(EvaluationRun)
        .where(
            EvaluationRun.domain_id == plugin.id,
            EvaluationRun.dataset_version == dataset_version,
            EvaluationRun.status == RunStatus.DONE,
            EvaluationRun.id != ev.id,
        )
        .order_by(EvaluationRun.finished_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    try:
        for q in questions:
            result = evaluate_question(session, plugin, q, k=settings.eval_retrieval_k, mode=mode)
            ev.results.append(result)
            session.commit()  # each result is visible while the run progresses
        ev.metrics = compute_metrics(ev.results, questions)
        ev.baseline_run_id = baseline.id if baseline else None
        from ..runtime_config import eval_config

        ev.regression, ev.regression_details = detect_regression(
            ev.metrics, baseline.metrics if baseline else None, eval_config()["regression_threshold"]
        )
        ev.findings = build_findings(ev.results)
        ev.status = RunStatus.DONE
    except Exception as exc:
        log.exception("evaluation run failed")
        ev.status = RunStatus.FAILED
        ev.error = str(exc)[:2000]
    ev.finished_at = utcnow()
    session.commit()
    return ev


def latest_evaluation(session: Session, domain_id: str) -> EvaluationRun | None:
    return session.execute(
        select(EvaluationRun)
        .where(EvaluationRun.domain_id == domain_id, EvaluationRun.status == RunStatus.DONE)
        .order_by(EvaluationRun.finished_at.desc())
        .limit(1)
    ).scalar_one_or_none()


__all__ = [
    "METRIC_KEYS",
    "build_findings",
    "compute_metrics",
    "detect_regression",
    "evaluate_question",
    "latest_evaluation",
    "run_evaluation",
]
