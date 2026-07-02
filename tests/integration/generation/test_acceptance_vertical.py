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
    assert "createable" in r.outcome.refusals[0].payload["detail"]


def test_empty_conditions_refuse(seeded):
    r = _run(seeded, _intent("acceptance-claim", "positive", "Order__c",
                             acceptance_conditions=[]),
             expect_emit=False)
    assert r.outcome.outcome_kind == OutcomeKind.REFUSAL
    assert "at least one" in r.outcome.refusals[0].payload["detail"]


def test_is_null_on_required_field_refuses(seeded):
    # D-305.1 (review B3): padding fills required fields, so an is_null clause
    # on one would be structurally contradicted at execution — fail closed.
    # Order__c.Req_Code__c is seeded NOT-nillable in field_details.
    r = _run(seeded, _intent("acceptance-claim", "positive", "Order__c",
                             acceptance_conditions=[
                                 {"field": "Order__c.Stage__c",
                                  "predicate": "equals", "value": "Submitted"},
                                 {"field": "Order__c.Req_Code__c",
                                  "predicate": "is_null"}]),
             expect_emit=False)
    assert r.outcome.outcome_kind == OutcomeKind.REFUSAL
    assert "REQUIRED" in r.outcome.refusals[0].payload["detail"]


def test_is_null_only_set_refuses(seeded):
    # D-305.1 (review S4): an is_null-only state would be proven by padding.
    r = _run(seeded, _intent("acceptance-claim", "positive", "Order__c",
                             acceptance_conditions=[
                                 {"field": "Order__c.Priority__c",
                                  "predicate": "is_null"}]),
             expect_emit=False)
    assert r.outcome.outcome_kind == OutcomeKind.REFUSAL
    assert "equals" in r.outcome.refusals[0].payload["detail"]


def test_same_conditions_same_hash(seeded):
    # D-305.1 (review S7): regeneration/dedup depends on CONVERGENCE too.
    def gen():
        return _run(seeded, _intent("acceptance-claim", "positive", "Order__c",
                                    acceptance_conditions=[
                                        {"field": "Order__c.Subtotal__c",
                                         "predicate": "equals", "value": 5000}]))
    r1, r2 = gen(), gen()
    h = lambda r: compute_identity_hash(
        "data_behavior", "acceptance-claim",
        r.emission.asserted_truth, r.emission.semantic_conditions)
    assert h(r1) == h(r2)


# ---------------------------------------------------------------------------
# D-306: update-acceptance — "the CHANGE succeeds" (the stage-progress case)
# ---------------------------------------------------------------------------

def test_update_acceptance_authors_the_update_shape(seeded):
    # TC-020's shape: given the staged initial state (create), the update to
    # the target state must be ACCEPTED — expect_acceptance rides the UPDATE.
    r = _run(seeded, _intent("acceptance-claim", "positive", "Order__c",
                             acceptance_conditions=[
                                 {"field": "Order__c.Stage__c",
                                  "predicate": "equals", "value": "Draft"},
                                 {"field": "Order__c.Subtotal__c",
                                  "predicate": "equals", "value": 5000}],
                             update_conditions=[
                                 {"field": "Order__c.Stage__c",
                                  "predicate": "equals", "value": "Submitted"}]))
    assert r.outcome.outcome_kind == OutcomeKind.DRAFT
    body = r.emission.asserted_truth
    assert body.operation == "update"
    steps = r.emission.observation_realization.steps
    assert [s.step_id for s in steps] == [
        "create-record", "update-record", "read-created", "assert-exists"]
    create, update = steps[0], steps[1]
    # the create stages the INITIAL state, standard D-115.2 grading (no flag)
    assert create.field_values == {"Order__c.Stage__c": "Draft",
                                   "Order__c.Subtotal__c": 5000}
    assert create.expect_acceptance is False
    # the UPDATE carries the acceptance semantics — it IS the assertion
    assert update.field_changes == {"Order__c.Stage__c": "Submitted"}
    assert update.expect_acceptance is True
    assert update.expect_rejection is None
    # D-306.1: the phases are identity-bearing SEPARATELY — the conditions
    # layer carries ONLY the initial state (a satisfiable AND again); the
    # destination rides the v2 body's update_state (phase- and
    # direction-distinct by construction).
    assert body.body_schema_version == 2
    assert {(k, v.value) for k, v in body.update_state.field_values.items()} \
        == {("Order__c.Stage__c", "Submitted")}
    conds = r.emission.semantic_conditions.conditions
    assert [(c.subject.external_id, c.value) for c in conds] == [
        ("Order__c.Stage__c", "Draft"), ("Order__c.Subtotal__c", 5000)]
    assert r.emission.causal_initiation.operation == "update"


def test_update_acceptance_hashes_apart_from_create_acceptance(seeded):
    # "this state saves" vs "given this state, the change succeeds" are
    # different assertions — operation + the update clauses keep them apart.
    base = [{"field": "Order__c.Stage__c", "predicate": "equals", "value": "Draft"}]
    r1 = _run(seeded, _intent("acceptance-claim", "positive", "Order__c",
                              acceptance_conditions=list(base)))
    r2 = _run(seeded, _intent("acceptance-claim", "positive", "Order__c",
                              acceptance_conditions=list(base),
                              update_conditions=[
                                  {"field": "Order__c.Stage__c",
                                   "predicate": "equals", "value": "Submitted"}]))
    h = lambda r: compute_identity_hash(
        "data_behavior", "acceptance-claim",
        r.emission.asserted_truth, r.emission.semantic_conditions)
    assert h(r1) != h(r2)


def test_update_condition_non_equals_refuses(seeded):
    r = _run(seeded, _intent("acceptance-claim", "positive", "Order__c",
                             acceptance_conditions=[
                                 {"field": "Order__c.Stage__c",
                                  "predicate": "equals", "value": "Draft"}],
                             update_conditions=[
                                 {"field": "Order__c.Priority__c",
                                  "predicate": "is_null"}]),
             expect_emit=False)
    assert r.outcome.outcome_kind == OutcomeKind.REFUSAL
    assert "equals" in r.outcome.refusals[0].payload["detail"]


def test_update_condition_unknown_field_refuses(seeded):
    r = _run(seeded, _intent("acceptance-claim", "positive", "Order__c",
                             acceptance_conditions=[
                                 {"field": "Order__c.Stage__c",
                                  "predicate": "equals", "value": "Draft"}],
                             update_conditions=[
                                 {"field": "Order__c.Nope__c",
                                  "predicate": "equals", "value": "X"}]),
             expect_emit=False)
    assert r.outcome.outcome_kind == OutcomeKind.REFUSAL
    assert "BELONG_TO" in r.outcome.refusals[0].payload["detail"]


def test_update_condition_non_updateable_refuses(seeded):
    # Total_With_Tax__c is the seeded CALCULATED field — a PATCH cannot stage it.
    r = _run(seeded, _intent("acceptance-claim", "positive", "Order__c",
                             acceptance_conditions=[
                                 {"field": "Order__c.Stage__c",
                                  "predicate": "equals", "value": "Draft"}],
                             update_conditions=[
                                 {"field": "Order__c.Total_With_Tax__c",
                                  "predicate": "equals", "value": 110}]),
             expect_emit=False)
    assert r.outcome.outcome_kind == OutcomeKind.REFUSAL
    assert "updateable" in r.outcome.refusals[0].payload["detail"]


def test_update_acceptance_recipe_projects_through_the_s4_bridge(seeded):
    # The cross-boundary seam (the D-305.1 lesson): the authored
    # update-acceptance recipe must satisfy the S4 bridge shape, with
    # expect_acceptance SURVIVING projection onto the positive PlannedUpdate.
    from datetime import datetime, timezone
    from uuid import uuid4 as _u
    from primeqa.execution_engine import build_data_recipe_plan
    from primeqa.execution_engine.plan import PlannedUpdate
    from primeqa.test_representation.coordinator import RecipeRead

    r = _run(seeded, _intent("acceptance-claim", "positive", "Order__c",
                             acceptance_conditions=[
                                 {"field": "Order__c.Stage__c",
                                  "predicate": "equals", "value": "Draft"}],
                             update_conditions=[
                                 {"field": "Order__c.Stage__c",
                                  "predicate": "equals", "value": "Submitted"}]))
    e = r.emission
    now = datetime(2026, 7, 2, tzinfo=timezone.utc)
    plan = build_data_recipe_plan(RecipeRead(
        recipe_id=_u(), version_seq=1, valid_from=now, valid_to=None,
        claim_test_id=_u(), claim_version_seq=1,
        trigger_kind=e.trigger_kind, recipe_kind=e.recipe_kind,
        causal_initiation=e.causal_initiation,
        observation_realization=e.observation_realization,
        execution_environment=e.execution_environment,
        priority=0, status="approved", created_at=now, updated_at=now))
    assert [s.kind for s in plan.steps] == ["create", "update", "read", "assert"]
    upd = plan.steps[1]
    assert isinstance(upd, PlannedUpdate)
    assert upd.expect_acceptance is True
    assert upd.expect_rejection is None
    assert upd.setup_step_id == "create-record"


def test_progress_and_regress_hash_apart(seeded):
    # D-306.1 (adversarial-review B1): the change DIRECTION is identity —
    # "given Draft, updating to Submitted succeeds" and the REVERSE regress
    # case swap the same two clauses across phases and MUST hash apart.
    # (The v1 flat concat collided them: the conditions layer is a sorted
    # SET, so the phase split was erased.)
    r_fwd = _run(seeded, _intent("acceptance-claim", "positive", "Order__c",
                                 acceptance_conditions=[
                                     {"field": "Order__c.Stage__c",
                                      "predicate": "equals", "value": "Draft"}],
                                 update_conditions=[
                                     {"field": "Order__c.Stage__c",
                                      "predicate": "equals", "value": "Submitted"}]))
    r_rev = _run(seeded, _intent("acceptance-claim", "positive", "Order__c",
                                 acceptance_conditions=[
                                     {"field": "Order__c.Stage__c",
                                      "predicate": "equals", "value": "Submitted"}],
                                 update_conditions=[
                                     {"field": "Order__c.Stage__c",
                                      "predicate": "equals", "value": "Draft"}]))
    h = lambda r: compute_identity_hash(
        "data_behavior", "acceptance-claim",
        r.emission.asserted_truth, r.emission.semantic_conditions)
    assert h(r_fwd) != h(r_rev)


def test_split_shift_hashes_apart(seeded):
    # D-306.1 (review B1): WHICH fields ride the change is identity — the
    # same clause multiset split differently across phases is a different
    # test (different PATCH → different VRs/automations fire).
    r_a = _run(seeded, _intent("acceptance-claim", "positive", "Order__c",
                               acceptance_conditions=[
                                   {"field": "Order__c.Stage__c",
                                    "predicate": "equals", "value": "Draft"},
                                   {"field": "Order__c.Subtotal__c",
                                    "predicate": "equals", "value": 5000}],
                               update_conditions=[
                                   {"field": "Order__c.Priority__c",
                                    "predicate": "equals", "value": "High"}]))
    r_b = _run(seeded, _intent("acceptance-claim", "positive", "Order__c",
                               acceptance_conditions=[
                                   {"field": "Order__c.Stage__c",
                                    "predicate": "equals", "value": "Draft"}],
                               update_conditions=[
                                   {"field": "Order__c.Subtotal__c",
                                    "predicate": "equals", "value": 5000},
                                   {"field": "Order__c.Priority__c",
                                    "predicate": "equals", "value": "High"}]))
    h = lambda r: compute_identity_hash(
        "data_behavior", "acceptance-claim",
        r.emission.asserted_truth, r.emission.semantic_conditions)
    assert h(r_a) != h(r_b)


def test_update_acceptance_converges(seeded):
    # Same intent twice -> same hash (dedup depends on convergence).
    def gen():
        return _run(seeded, _intent("acceptance-claim", "positive", "Order__c",
                                    acceptance_conditions=[
                                        {"field": "Order__c.Stage__c",
                                         "predicate": "equals", "value": "Draft"}],
                                    update_conditions=[
                                        {"field": "Order__c.Stage__c",
                                         "predicate": "equals", "value": "Submitted"}]))
    r1, r2 = gen(), gen()
    h = lambda r: compute_identity_hash(
        "data_behavior", "acceptance-claim",
        r.emission.asserted_truth, r.emission.semantic_conditions)
    assert h(r1) == h(r2)


def test_vacuous_update_refuses(seeded):
    # D-306.1 (review SF-5): a change identical to the initial state is a
    # no-op PATCH — trivially "accepted" without the transition exercised.
    r = _run(seeded, _intent("acceptance-claim", "positive", "Order__c",
                             acceptance_conditions=[
                                 {"field": "Order__c.Stage__c",
                                  "predicate": "equals", "value": "Draft"}],
                             update_conditions=[
                                 {"field": "Order__c.Stage__c",
                                  "predicate": "equals", "value": "Draft"}]),
             expect_emit=False)
    assert r.outcome.outcome_kind == OutcomeKind.REFUSAL
    assert "no-op" in r.outcome.refusals[0].payload["detail"]


def test_duplicate_update_fields_refuse(seeded):
    # D-306.1 (review N7): two destination values for one field — identity
    # would carry both while the PATCH stages last-wins; refuse.
    r = _run(seeded, _intent("acceptance-claim", "positive", "Order__c",
                             acceptance_conditions=[
                                 {"field": "Order__c.Subtotal__c",
                                  "predicate": "equals", "value": 5000}],
                             update_conditions=[
                                 {"field": "Order__c.Stage__c",
                                  "predicate": "equals", "value": "A"},
                                 {"field": "Order__c.Stage__c",
                                  "predicate": "equals", "value": "B"}]),
             expect_emit=False)
    assert r.outcome.outcome_kind == OutcomeKind.REFUSAL
    assert "duplicate" in r.outcome.refusals[0].payload["detail"]


def test_malformed_update_conditions_refuse_not_crash(seeded):
    # D-306.1 (review N6): a misshaped proposal refuses cleanly.
    r = _run(seeded, _intent("acceptance-claim", "positive", "Order__c",
                             acceptance_conditions=[
                                 {"field": "Order__c.Stage__c",
                                  "predicate": "equals", "value": "Draft"}],
                             update_conditions="Stage='Submitted'"),
             expect_emit=False)
    assert r.outcome.outcome_kind == OutcomeKind.REFUSAL
    assert "list" in r.outcome.refusals[0].payload["detail"]
