"""S7 intent classification — pure-unit table (D-163.1).

No DB, no LLM. Pins `classify_intent`: each intent's keywords classify to it,
highest-distinct-count wins a mixed question, ties break by the fixed priority
order, off-topic → None, and matching is inflection-aware (via knowledge._text).
"""
from __future__ import annotations

import pytest

from primeqa.conversation import QuestionContext, classify_intent

pytestmark = pytest.mark.unit

_CTX = QuestionContext(tenant_id=1)


@pytest.mark.parametrize("question, expected", [
    ("Why did these tests fail?", "failure_cause"),
    ("What was the root cause of the error?", "failure_cause"),
    ("Which tests have drifted since the org changed?", "grounding_drift"),
    ("Are these tests stale or broken now?", "grounding_drift"),
    ("What's affected if we change this object?", "impact"),
    ("What depends on Account downstream?", "impact"),
])
def test_each_intent_classifies(question, expected):
    intent = classify_intent(question, _CTX)
    assert intent is not None and intent.kind == expected
    assert intent.matched_keywords  # non-empty


def test_off_topic_and_empty_return_none():
    assert classify_intent("What's the weather today?", _CTX) is None
    assert classify_intent("", _CTX) is None
    assert classify_intent("   ", _CTX) is None


def test_matching_is_inflection_aware():
    # failed / drifting / affects match their bases via knowledge._text.
    assert classify_intent("the test failed", _CTX).kind == "failure_cause"
    assert classify_intent("is it drifting?", _CTX).kind == "grounding_drift"
    assert classify_intent("what does this affect", _CTX).kind == "impact"


def test_highest_distinct_count_wins_mixed_question():
    # failure_cause matches why/fail/error (3); grounding_drift matches drift (1).
    intent = classify_intent("why did it fail with an error and maybe drift", _CTX)
    assert intent.kind == "failure_cause"
    assert {"why", "fail", "error"} <= set(intent.matched_keywords)


def test_tie_breaks_by_fixed_priority():
    # one keyword each: "cause" (failure_cause) vs "impact" (impact) → the
    # failure_cause > impact priority order resolves the 1-1 tie.
    intent = classify_intent("what is the cause of this impact", _CTX)
    assert intent.kind == "failure_cause"


def test_impact_classification_is_keyword_only():
    # the impact TARGET rides the bounded context (a picker), not NL extraction —
    # classification only needs an impact keyword.
    ctx = QuestionContext(tenant_id=1, object_api_name="Account")
    assert classify_intent("what's the impact", ctx).kind == "impact"
