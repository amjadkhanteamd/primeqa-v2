"""D-305 — the acceptance vertical (lever 7c).

Real S1 grounding through the runtime: an acceptance-claim grounds its
business-state clauses (BELONGS_TO + stageability + writability), authors the
prohibition-mirror bundle (multi-field create with ``expect_acceptance=True``
→ read → assert exists), and the clauses are IDENTITY-BEARING (distinct
values = distinct claims — the TC-007/TC-008 boundary-pair requirement).
"""
from __future__ import annotations

from primeqa.generation.enums import OutcomeKind

from .test_automation_vertical import _intent, _run
from primeqa.test_representation.identity_hash import compute_identity_hash


def _acceptance(seeded, conditions, **extra):
    return _run(seeded, _intent("acceptance-claim", "positive", "Order__c",
                                acceptance_conditions=conditions, **extra),
                expect_emit=extra.pop("expect_emit", True))


def test_acceptance_authors_the_prohibition_mirror(seeded):
    r = _run(seeded, _intent("acceptance-claim", "positive", "Order__c",
                             acceptance_conditions=[
                                 {"field": "Order__c.Stage__c",
                                  "predicate": "equals", "value": "Submitted"},
                                 {"field": "Order__c.Priority__c",
                                  "predicate": "is_null"}]))
    assert r.outcome.outcome_kind == OutcomeKind.DRAFT
    body = r.emission.asserted_truth
    assert body.kind == "acceptance-claim"
    assert body.operation == "create"
    assert body.target.external_id == "Order__c"
    # the clauses are identity-bearing state
    conds = r.emission.semantic_conditions.conditions
    assert {(c.subject.external_id, c.predicate) for c in conds} == {
        ("Order__c.Stage__c", "equals"), ("Order__c.Priority__c", "is_null")}
    # the recipe: ONE create staging only the equals clause, the assertion
    steps = r.emission.observation_realization.steps
    assert [s.step_id for s in steps] == [
        "create-record", "read-created", "assert-exists"]
    create = steps[0]
    assert create.expect_acceptance is True
    assert create.expect_rejection is None
    assert create.field_values == {"Order__c.Stage__c": "Submitted"}
    assert steps[2].predicate.predicate == "exists"


def test_distinct_values_are_distinct_claims(seeded):
    # The TC-007/TC-008 requirement: just-below and at-threshold differ ONLY
    # by value and MUST hash apart (values are identity-bearing here).
    r1 = _run(seeded, _intent("acceptance-claim", "positive", "Order__c",
                              acceptance_conditions=[
                                  {"field": "Order__c.Subtotal__c",
                                   "predicate": "equals", "value": 7999999}]))
    r2 = _run(seeded, _intent("acceptance-claim", "positive", "Order__c",
                              acceptance_conditions=[
                                  {"field": "Order__c.Subtotal__c",
                                   "predicate": "equals", "value": 8000000}]))
    h1 = compute_identity_hash("data_behavior", "acceptance-claim",
                               r1.emission.asserted_truth,
                               r1.emission.semantic_conditions)
    h2 = compute_identity_hash("data_behavior", "acceptance-claim",
                               r2.emission.asserted_truth,
                               r2.emission.semantic_conditions)
    assert h1 != h2


def test_unknown_condition_field_refuses(seeded):
    r = _run(seeded, _intent("acceptance-claim", "positive", "Order__c",
                             acceptance_conditions=[
                                 {"field": "Order__c.Nope__c",
                                  "predicate": "equals", "value": "X"}]),
             expect_emit=False)
    assert r.outcome.outcome_kind == OutcomeKind.REFUSAL


def test_unstageable_predicate_refuses(seeded):
    r = _run(seeded, _intent("acceptance-claim", "positive", "Order__c",
                             acceptance_conditions=[
                                 {"field": "Order__c.Stage__c",
                                  "predicate": "not_equals", "value": "X"}]),
             expect_emit=False)
    assert r.outcome.outcome_kind == OutcomeKind.REFUSAL
    assert "stageable" in r.outcome.refusals[0].payload["detail"]


def test_nonwritable_condition_field_refuses(seeded):
    # Total_With_Tax__c is the seeded CALCULATED field — an equals clause on it
    # cannot be staged (D-294 writability bar).
    r = _run(seeded, _intent("acceptance-claim", "positive", "Order__c",
                             acceptance_conditions=[
                                 {"field": "Order__c.Total_With_Tax__c",
                                  "predicate": "equals", "value": 110}]),
             expect_emit=False)
    assert r.outcome.outcome_kind == OutcomeKind.REFUSAL
    assert "writable" in r.outcome.refusals[0].payload["detail"]


def test_empty_conditions_refuse(seeded):
    r = _run(seeded, _intent("acceptance-claim", "positive", "Order__c",
                             acceptance_conditions=[]),
             expect_emit=False)
    assert r.outcome.outcome_kind == OutcomeKind.REFUSAL
    assert "at least one" in r.outcome.refusals[0].payload["detail"]
