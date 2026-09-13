from knowledge_platform.core.evaluation.checks import (
    CitedItem,
    RetrievedItem,
    check_abstention,
    check_citations,
    check_contradictions,
    check_freshness,
    check_required_concepts,
    check_retrieval_recall,
    failure_causes,
    run_all_checks,
)
from knowledge_platform.core.evaluation.runner import compute_metrics, detect_regression
from knowledge_platform.core.plugins.base import EvalQuestion
from knowledge_platform.models import EvaluationResult


def test_required_concepts_and_abstention():
    assert check_required_concepts("CALCULATE modifies the Filter Context.", ["filter context"])["ok"]
    assert not check_required_concepts("CALCULATE is a function.", ["filter context"])["ok"]
    assert check_abstention("The knowledge base does not contain information on this.", False, True)["ok"]
    assert not check_abstention("DIVIDE returns BLANK.", False, True)["ok"]
    assert check_abstention("DIVIDE returns BLANK.", False, False)["ok"]
    assert not check_abstention("Not enough verified knowledge.", True, False)["ok"]


def test_citations_require_retrieved_and_verified():
    cited = [
        CitedItem(id="a", n=1, status="VERIFIED", evidence_verified=True),
        CitedItem(id="b", n=2, status="VERIFIED"),
    ]
    res = check_citations(cited, {"a", "b"}, "")
    assert not res["ok"] and res["valid"] == 1
    assert check_citations(cited[:1], {"a"}, "")["ok"]
    assert not check_citations([], set(), "")["ok"]


def test_freshness_and_conflict_flags():
    stale = [CitedItem(id="a", n=1, status="STALE", evidence_verified=True)]
    assert not check_freshness(stale, "X does Y.")["ok"]
    assert check_freshness(stale, "X does Y, but this may be outdated.")["ok"]
    conf = [CitedItem(id="a", n=1, status="CONFLICTED", evidence_verified=True)]
    assert not check_contradictions(conf, "X does Y.")["ok"]
    assert check_contradictions(conf, "Sources disagree: X does Y or Z.")["ok"]


def test_retrieval_recall_matches_url_prefixes():
    retrieved = [RetrievedItem(id="a", evidence_urls=["https://learn.microsoft.com/en-us/dax/calculate-function-dax"])]
    assert (
        check_retrieval_recall(retrieved, ["https://learn.microsoft.com/en-us/dax/calculate-function-dax"])["value"]
        == 1.0
    )
    assert check_retrieval_recall(retrieved, ["https://example.com/x"])["value"] == 0.0
    assert check_retrieval_recall(retrieved, [])["value"] is None


def _result(qid: str, passed: bool, checks=None, judge=None, causes=None) -> EvaluationResult:
    return EvaluationResult(
        question_id=qid,
        question=qid,
        answer="",
        checks=checks or {"abstention": {"ok": True, "abstained": False}},
        judge=judge or {},
        passed=passed,
        failure_causes=causes or [],
        latency_ms=10,
    )


def test_failure_causes_and_metrics_and_regression():
    q = EvalQuestion(id="q1", question="?", required_concepts=["blank"], topic="DAX")
    checks = run_all_checks(
        answer="DIVIDE returns nothing special.",
        insufficient_flag=False,
        question=q,
        cited=[CitedItem(id="a", n=1, status="VERIFIED", evidence_verified=True, topic="DAX/Functions")],
        retrieved=[RetrievedItem(id="a", topic="DAX/Functions")],
    )
    causes = failure_causes(
        checks, {"correct": False, "supported_by_citations": True, "hallucinated_claims": []}, False
    )
    assert "missing_concepts" in causes and "judge_incorrect" in causes

    questions = [q, EvalQuestion(id="q2", question="?", expect_abstain=True)]
    results = [
        _result("q1", False, checks=checks, judge={"correct": False, "supported_by_citations": True}, causes=causes),
        _result("q2", True, checks={"abstention": {"ok": True, "abstained": True}}),
    ]
    m = compute_metrics(results, questions)
    assert m["questions"] == 2 and m["passed"] == 1 and m["accuracy"] == 0.5
    assert m["abstention_correctness"] == 1.0
    assert m["citation_correctness"] == 1.0
    assert m["failure_causes"]["missing_concepts"] == 1

    regressed, details = detect_regression(
        {"accuracy": 0.5, "citation_correctness": 1.0}, {"accuracy": 0.9, "citation_correctness": 1.0}, 0.05
    )
    assert regressed and details["metrics"]["accuracy"]["delta"] == -0.4
    assert not detect_regression({"accuracy": 0.9}, {"accuracy": 0.92}, 0.05)[0]
    assert not detect_regression({"accuracy": 0.5}, None, 0.05)[0]


def test_negative_questions_need_negative_knowledge_cited():
    q = EvalQuestion(id="n1", question="?", required_concepts=["not all"], negative=True, topic="DAX")
    positive_only = run_all_checks(
        answer="Not all functions exist everywhere.",
        insufficient_flag=False,
        question=q,
        cited=[CitedItem(id="a", n=1, status="VERIFIED", evidence_verified=True, topic="DAX")],
        retrieved=[RetrievedItem(id="a", topic="DAX")],
    )
    assert positive_only["negative_knowledge"]["value"] == 0.0
    assert "limitation_not_surfaced" in failure_causes(positive_only, {}, False)
    with_negative = run_all_checks(
        answer="Not all functions exist everywhere.",
        insufficient_flag=False,
        question=q,
        cited=[CitedItem(id="b", n=1, status="VERIFIED", evidence_verified=True, topic="DAX", polarity="negative")],
        retrieved=[RetrievedItem(id="b", topic="DAX")],
    )
    assert with_negative["negative_knowledge"] == {
        "ok": True,
        "detail": "negative-knowledge items cited [1]",
        "value": 1.0,
        "cited_negative": [1],
    }
    # informational for ordinary questions, and a metric only over negative questions
    plain = EvalQuestion(id="p1", question="?", topic="DAX")
    assert (
        run_all_checks(answer="x", insufficient_flag=False, question=plain, cited=[], retrieved=[])[
            "negative_knowledge"
        ]["value"]
        is None
    )
    m = compute_metrics(
        [_result("n1", True, checks=with_negative), _result("p1", True, checks={"abstention": {"ok": True}})],
        [q, plain],
    )
    assert m["negative_coverage"] == 1.0
