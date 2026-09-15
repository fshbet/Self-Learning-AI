"""Evaluation classification (audit P0.5 / P0.6): uncited answers are not abstentions, coverage failures are
distinguished from retrieval and answer failures, and uncited answers are judged."""

from __future__ import annotations

from knowledge_platform.core.evaluation import runner
from knowledge_platform.core.evaluation.checks import (
    CitedItem,
    RetrievedItem,
    answer_kind,
    failure_causes,
    failure_class,
    is_abstention,
    run_all_checks,
)
from knowledge_platform.core.plugins.base import EvalQuestion
from knowledge_platform.core.retrieval.answer import Answer

Q = EvalQuestion(
    id="q", question="What is query folding?", required_concepts=["native query"], topic="Power Query / M/Query folding"
)
COVERED = {"ok": True, "detail": "relevant knowledge present", "value": 1.0}
UNCOVERED = {"ok": False, "detail": "no relevant knowledge in the base", "value": 0.0}
DECLINE = "The provided knowledge items do not contain information about query folding, so I cannot answer."
MEMORY = (
    "Query folding in Power Query refers to the process where Power BI translates M code into a native query that "
    "the data source executes, instead of loading everything into memory. This makes refreshes faster and is the "
    "reason transformations should stay foldable wherever possible."
)
MIXED = (
    "The provided knowledge items do not directly explain what CALCULATE does. However, CALCULATE is a core DAX "
    "function that evaluates an expression in a modified filter context: filter arguments add to or replace "
    "existing filters, and when a row context exists it performs context transition, which is why measures "
    "behave differently inside iterators. It is the most important function in the language and appears in "
    "almost every non-trivial measure written by practitioners."
)


def test_uncited_answer_is_not_an_abstention_and_genuine_abstention_still_is():
    assert is_abstention(DECLINE, no_results=False)
    assert is_abstention("anything", no_results=True)  # nothing retrieved at all
    assert not is_abstention(MEMORY, no_results=False)
    assert not is_abstention(MIXED, no_results=False)  # a decline followed by a real answer is an answer
    assert answer_kind(DECLINE, False, 0) == "abstention"
    assert answer_kind(MEMORY, False, 0) == "uncited_answer"
    assert answer_kind(MEMORY, False, 2) == "cited_answer"


def _checks(answer, cited, coverage, retrieved=None):
    return run_all_checks(
        answer=answer,
        no_results=False,
        question=Q,
        cited=cited,
        retrieved=retrieved if retrieved is not None else [RetrievedItem(id="x", topic="DAX/Functions")],
        coverage=coverage,
    )


def test_coverage_failure_is_distinguished_from_retrieval_and_answer_failures():
    # nothing relevant exists: whatever the model did, the root cause is coverage
    c = _checks(DECLINE, [], UNCOVERED)
    assert c["answer_kind"]["kind"] == "abstention" and not c["coverage"]["ok"]
    assert failure_causes(c, {}, False) == ["coverage_failure"]
    assert failure_class(c, {}, False, passed=False) == "coverage_failure"
    c = _checks(MEMORY, [], UNCOVERED)
    assert failure_causes(c, {"hallucinated_claims": ["x"]}, False)[:2] == ["uncited_answer", "coverage_failure"]
    assert failure_class(c, {}, False, passed=False) == "coverage_failure"
    # knowledge exists but retrieval surfaced the wrong topic and the model declined: retrieval failure
    c = _checks(DECLINE, [], COVERED)
    assert failure_causes(c, {}, False) == ["abstained_unexpectedly", "off_topic_retrieval"]
    assert failure_class(c, {}, False, passed=False) == "retrieval_failure"
    # knowledge exists and was retrieved on topic, yet the model declined: the answer step failed
    c = _checks(DECLINE, [], COVERED, retrieved=[RetrievedItem(id="x", topic="Power Query / M/Query folding")])
    assert failure_class(c, {}, False, passed=False) == "answer_generation_failure"
    # knowledge exists, the model answered from memory without citations
    c = _checks(MEMORY, [], COVERED)
    assert failure_causes(c, {"correct": True, "hallucinated_claims": []}, False)[0] == "uncited_answer"
    assert failure_class(c, {}, False, passed=False) == "uncited_answer"
    # cited answer whose citations do not resolve
    cited = [CitedItem(id="ghost", n=1, status="VERIFIED", evidence_verified=False)]
    c = _checks("Folding pushes work to the native query [1].", cited, COVERED)
    assert failure_class(c, {}, False, passed=False) == "citation_failure"
    # no coverage signals declared: n/a, never counted as a failure
    c = run_all_checks(answer=DECLINE, no_results=False, question=Q, cited=[], retrieved=[], coverage=None)
    assert c["coverage"]["ok"] and c["coverage"]["value"] is None
    # expected abstention
    qa = EvalQuestion(id="a", question="?", expect_abstain=True)
    c = run_all_checks(answer=DECLINE, no_results=False, question=qa, cited=[], retrieved=[], coverage=None)
    assert failure_class(c, {}, True, passed=True) == "expected_abstention"
    c = run_all_checks(answer=MEMORY, no_results=False, question=qa, cited=[], retrieved=[], coverage=None)
    assert failure_causes(c, {}, True) == ["answered_instead_of_abstaining"]
    assert failure_class(c, {}, True, passed=False) == "answer_generation_failure"


class _NoSession:
    pass


def test_uncited_answer_is_judged_and_fails_as_uncited(monkeypatch):
    judged: list[list[str]] = []
    monkeypatch.setattr(
        runner,
        "answer_question",
        lambda session, plugin, question, limit=None, mode="p3": Answer(
            question=question,
            answer=MEMORY,
            citations=[],
            retrieved=[
                {
                    "n": 1,
                    "id": "11111111-1111-1111-1111-111111111111",
                    "statement": "DAX is a language.",
                    "status": "VERIFIED",
                    "confidence": 0.8,
                    "topic": "DAX",
                    "score": 0.02,
                }
            ],
            insufficient=True,
        ),
    )
    monkeypatch.setattr(runner, "_item_facts", lambda session, ids: {})
    monkeypatch.setattr(runner, "_coverage", lambda session, plugin, q: COVERED)

    def judge(session, plugin, q, answer, cited_texts):
        judged.append(cited_texts)
        return {"correct": False, "supported_by_citations": False, "hallucinated_claims": [answer[:40]]}

    monkeypatch.setattr(runner, "_judge", judge)
    result = runner.evaluate_question(_NoSession(), None, Q, k=8)
    assert judged and judged[0] == ["(retrieved, not cited) DAX is a language."]
    assert not result.passed and result.failure_class == "uncited_answer"
    assert result.failure_causes[0] == "uncited_answer" and "hallucination" in result.failure_causes
    assert result.checks["answer_kind"]["kind"] == "uncited_answer"
    assert result.checks["abstention"]["abstained"] is False

    # a genuine abstention is still not judged
    judged.clear()
    monkeypatch.setattr(
        runner,
        "answer_question",
        lambda session, plugin, question, limit=None, mode="p3": Answer(
            question=question, answer=DECLINE, insufficient=True
        ),
    )
    result = runner.evaluate_question(_NoSession(), None, Q, k=8)
    assert not judged and result.checks["answer_kind"]["kind"] == "abstention"
    assert result.failure_class == "retrieval_failure"


def test_metrics_report_coverage_uncited_rate_and_classes():
    from knowledge_platform.core.evaluation.runner import compute_metrics
    from knowledge_platform.models import EvaluationResult

    def res(qid, passed, kind, cov, klass):
        return EvaluationResult(
            question_id=qid,
            question=qid,
            answer="",
            checks={
                "abstention": {"ok": True, "abstained": kind == "abstention"},
                "answer_kind": {"ok": True, "kind": kind},
                "coverage": cov,
            },
            judge={},
            passed=passed,
            failure_causes=[],
            failure_class=klass,
            latency_ms=1,
        )

    questions = [EvalQuestion(id=q, question="?") for q in ("a", "b", "c")]
    m = compute_metrics(
        [
            res("a", True, "cited_answer", COVERED, None),
            res("b", False, "uncited_answer", UNCOVERED, "coverage_failure"),
            res("c", False, "abstention", {"ok": True, "value": None}, "retrieval_failure"),
        ],
        questions,
    )
    assert m["coverage"] == 0.5 and m["uncited_rate"] == round(1 / 3, 4)
    assert m["failure_classes"] == {"coverage_failure": 1, "retrieval_failure": 1}
