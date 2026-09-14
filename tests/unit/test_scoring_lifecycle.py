import pytest

from knowledge_platform.core.quality.scoring import score
from knowledge_platform.core.versioning.lifecycle import (
    IllegalTransition,
    status_for_level,
    transition,
    verification_level,
)
from knowledge_platform.models import ItemStatus, KnowledgeItem


class FakeSession:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)


def test_score_is_explainable_and_bounded():
    conf, factors = score(
        source_authority=95,
        evidence_verified=True,
        distinct_sources=2,
        statement="CALCULATE evaluates an expression in a modified filter context.",
        code=None,
        topic_matched=True,
    )
    assert 0.0 <= conf <= 1.0
    assert factors["rule_version"]
    assert set(factors["weights"]) <= set(factors)
    low, _ = score(
        source_authority=10, evidence_verified=False, distinct_sources=0, statement="x", code=None, topic_matched=False
    )
    assert low < conf


def test_verification_levels():
    vl = verification_level
    assert vl(distinct_sources=0, max_authority=0, validator_passed=False, human_approved=False) == 0
    assert vl(distinct_sources=1, max_authority=50, validator_passed=False, human_approved=False) == 1
    assert vl(distinct_sources=1, max_authority=95, validator_passed=False, human_approved=False) == 2
    assert vl(distinct_sources=2, max_authority=50, validator_passed=False, human_approved=False) == 3
    assert vl(distinct_sources=1, max_authority=95, validator_passed=True, human_approved=False) == 4
    assert vl(distinct_sources=1, max_authority=95, validator_passed=True, human_approved=True) == 5
    assert status_for_level(2, has_conflict=False) == ItemStatus.VERIFIED
    assert status_for_level(1, has_conflict=False) == ItemStatus.SUPPORTED
    assert status_for_level(4, has_conflict=True) == ItemStatus.CONFLICTED


def test_transitions_are_enforced_and_audited():
    s = FakeSession()
    item = KnowledgeItem(status=ItemStatus.EXTRACTED)
    assert transition(s, item, ItemStatus.CANDIDATE, reason="passed gates")
    assert item.status == ItemStatus.CANDIDATE
    assert len(s.added) == 1 and s.added[0].to_status == ItemStatus.CANDIDATE
    with pytest.raises(IllegalTransition):
        transition(s, item, ItemStatus.VERIFIED)
    transition(s, item, ItemStatus.REJECTED, reason="dup")
    with pytest.raises(IllegalTransition):
        transition(s, item, ItemStatus.CANDIDATE)  # terminal state
    assert transition(s, item, ItemStatus.CANDIDATE, force=True)  # human override


def test_freshness_uses_the_last_confirmation_not_a_new_verification():
    from datetime import UTC, datetime, timedelta

    from knowledge_platform.core.quality.scoring import freshness

    now = datetime(2026, 9, 14, tzinfo=UTC)
    assert freshness(now - timedelta(days=3), is_stale=False, now=now) == 1.0
    assert freshness(now - timedelta(days=45), is_stale=False, now=now) == 0.7
    assert freshness(now - timedelta(days=200), is_stale=False, now=now) == 0.2
    assert freshness(None, is_stale=False, now=now) == 0.5
    assert freshness(now, is_stale=True, now=now) == 0.0
