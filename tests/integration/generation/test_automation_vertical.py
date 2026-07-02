"""D-210 — the automation-effect + state-transition vertical (5b5-1).

Real S1 grounding (the seeded Order__c fixture: Flow ``Stamp_Order_Status``
TRIGGERS_ON Order__c; Status__c BELONGS_TO it; Order_Log__c with its lookup
back) + the real S2 Coordinator + the unified persistence transaction. Asserts:

  - a positive state-transition with a verifiable to-state emits the
    create-scoped observe-the-org recipe (create WITHOUT the field → read →
    assert), caveated Layer-1;
  - cross-object triggers and unverifiable names DEFER with a specific detail
    (never guessed);
  - a positive automation-effect grounds on the Flow (not the any-field
    proxy), authors same-record and cross-object shapes, and refuses
    UNGROUNDED on an object with no record-triggered Flow;
  - negative polarity for both kinds defers (prohibition-claim is the built
    rejection vertical).
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from primeqa.generation.enums import AdmissibilityLayer, OutcomeKind, RefusalKind
from primeqa.generation.governance_core import GovernanceCore
from primeqa.generation.persistence import LedgerPersister

from .conftest import FakeTurn, FakeToolTurn, TEST_TENANT_ID, make_request, propose_turn


def _intent(claim_kind, polarity, sf_api_name, *, excerpt="when created, the org sets it",
            **hint_extra):
    tsh = {"entity_type": "Object", "sf_api_name": sf_api_name, **hint_extra}
    return {"requirement_excerpt": excerpt, "intent_descriptor": {
        "archetype_hint": "data_behavior", "polarity_hint": polarity,
        "claim_kind_hint": claim_kind, "target_subject_hint": tsh}}


def _emit_turn(layer="layer_1") -> FakeTurn:
    return FakeTurn([{"type": "tool_use", "id": f"tu_{uuid4().hex[:6]}",
                      "name": "emit_outcome",
                      "input": {"outcome_kind": "draft",
                                "payload": {"admissibility_layer": layer}}}])


def _run(seeded, intent_input, *, expect_emit=True):
    from primeqa.semantic.connection import get_tenant_connection
    from primeqa.semantic.query import SemanticOrgModel
    from primeqa.generation.runtime import GenerationRuntime

    req = make_request(s1_version_seq=seeded["v1"])
    turns = [propose_turn(intent_input)]
    if expect_emit:
        turns.append(_emit_turn())
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        gov = GovernanceCore(SemanticOrgModel(conn))
        result = GenerationRuntime().run(
            request=req, seam=gov, tool_turn_fn=FakeToolTurn(turns),
            persister=LedgerPersister(TEST_TENANT_ID))
    return result.results[0]


# ---------------------------------------------------------------------------
# State-transition: create-scoped positive
# ---------------------------------------------------------------------------

def test_state_transition_emits_observe_the_org_recipe(seeded):
    r = _run(seeded, _intent("state-transition-claim", "positive", "Order__c",
                             field_name="Order__c.Status__c", expected_value="Activated"))
    o = r.outcome
    assert o.outcome_kind == OutcomeKind.DRAFT
    assert o.admissibility_layer == AdmissibilityLayer.LAYER_1
    assert o.caveat_required is True            # no Flow-formula derivation in v1
    body = r.emission.asserted_truth
    assert body.kind == "state-transition-claim"
    assert body.to_state.field_values["Order__c.Status__c"].value == "Activated"
    assert body.from_state.field_values == {}
    steps = r.emission.observation_realization.steps
    assert [s.step_id for s in steps] == ["create-record", "read-created", "assert-value"]
    # the asserted field is NOT set by the create — the org must produce it
    assert steps[0].field_values == {}


def test_state_transition_staged_trigger_emits_paired_shape(seeded):
    # D-222: a verified trigger pair stages the pre-state — the create SETS
    # the trigger field (so the org's automation actually fires) while the
    # asserted to-state field stays org-produced.
    r = _run(seeded, _intent("state-transition-claim", "positive", "Order__c",
                             field_name="Order__c.Status__c", expected_value="Activated",
                             trigger_field="Order__c.Stage__c", trigger_value="Submitted"))
    o = r.outcome
    assert o.outcome_kind == OutcomeKind.DRAFT
    body = r.emission.asserted_truth
    assert body.from_state.field_values["Order__c.Stage__c"].value == "Submitted"
    assert body.to_state.field_values["Order__c.Status__c"].value == "Activated"
    assert {f.external_id for f in body.subject_fields} == {
        "Order__c.Status__c", "Order__c.Stage__c"}
    steps = r.emission.observation_realization.steps
    assert steps[0].field_values == {"Order__c.Stage__c": "Submitted"}
    assert "Order__c.Status__c" not in steps[0].field_values


def test_state_transition_unverified_trigger_falls_back_unstaged(seeded):
    # D-222: an unverifiable LLM-proposed trigger is DROPPED, never guessed —
    # and never regresses the claim to a refusal (the unstaged shape emits).
    r = _run(seeded, _intent("state-transition-claim", "positive", "Order__c",
                             field_name="Order__c.Status__c", expected_value="Activated",
                             trigger_field="Order__c.Nope__c", trigger_value="X"))
    o = r.outcome
    assert o.outcome_kind == OutcomeKind.DRAFT
    body = r.emission.asserted_truth
    assert body.from_state.field_values == {}
    assert r.emission.observation_realization.steps[0].field_values == {}


def test_state_transition_trigger_without_value_falls_back_unstaged(seeded):
    # D-222: a half-pair (field named, no value) is ignored — unstaged shape.
    r = _run(seeded, _intent("state-transition-claim", "positive", "Order__c",
                             field_name="Order__c.Status__c", expected_value="Activated",
                             trigger_field="Order__c.Stage__c"))
    assert r.outcome.outcome_kind == OutcomeKind.DRAFT
    assert r.emission.asserted_truth.from_state.field_values == {}
    assert r.emission.observation_realization.steps[0].field_values == {}


def test_state_transition_cross_object_trigger_emits_two_create_chain(seeded):
    # D-227: a verified trigger object + its lookup back to the subject emit
    # the 2-create chain (create subject, create trigger linked to it, read
    # the subject, assert the to-state).
    r = _run(seeded, _intent("state-transition-claim", "positive", "Order__c",
                             field_name="Order__c.Status__c", expected_value="Activated",
                             trigger_object="Order_Log__c",
                             trigger_lookup_field="Order_Log__c.Order__c"))
    o = r.outcome
    assert o.outcome_kind == OutcomeKind.DRAFT
    steps = r.emission.observation_realization.steps
    assert [s.step_id for s in steps] == [
        "create-subject", "create-trigger", "read-subject", "assert-value"]
    assert steps[1].target_object.external_id == "Order_Log__c"
    assert steps[1].field_values == {"Order_Log__c.Order__c": "$create-subject.id"}
    assert r.emission.causal_initiation.target.external_id == "Order_Log__c"


def test_state_transition_cross_object_without_lookup_defers(seeded):
    # The trigger object alone is not enough — without a verified lookup back
    # to the subject the recipe cannot link the records; defer, never guess.
    r = _run(seeded, _intent("state-transition-claim", "positive", "Order__c",
                             field_name="Order__c.Status__c", expected_value="Activated",
                             trigger_object="Order_Log__c"),
             expect_emit=False)
    o = r.outcome
    assert o.outcome_kind == OutcomeKind.REFUSAL
    assert o.refusal_kind == RefusalKind.EMISSION_DEFERRED
    assert "trigger lookup field" in o.refusals[0].payload["detail"]


def test_state_transition_cross_object_bad_lookup_defers(seeded):
    r = _run(seeded, _intent("state-transition-claim", "positive", "Order__c",
                             field_name="Order__c.Status__c", expected_value="Activated",
                             trigger_object="Order_Log__c",
                             trigger_lookup_field="Order_Log__c.Nope__c"),
             expect_emit=False)
    assert r.outcome.refusal_kind == RefusalKind.EMISSION_DEFERRED
    assert "trigger lookup field" in r.outcome.refusals[0].payload["detail"]


def test_state_transition_unverifiable_field_defers(seeded):
    r = _run(seeded, _intent("state-transition-claim", "positive", "Order__c",
                             field_name="Order__c.Nope__c", expected_value="X"),
             expect_emit=False)
    assert r.outcome.refusal_kind == RefusalKind.EMISSION_DEFERRED
    assert "verifiable to-state" in r.outcome.refusals[0].payload["detail"]


def test_negative_state_transition_defers_to_prohibition_vertical(seeded):
    # grounds via the Lead VR (the negative dim) but has no negative emission
    r = _run(seeded, _intent("state-transition-claim", "negative", "Lead",
                             field_name="Lead.Status", expected_value="X"),
             expect_emit=False)
    assert r.outcome.refusal_kind == RefusalKind.EMISSION_DEFERRED
    assert "prohibition-claim" in r.outcome.refusals[0].payload["detail"]


# ---------------------------------------------------------------------------
# Automation-effect: Flow grounding + the two effect shapes
# ---------------------------------------------------------------------------

def test_automation_effect_same_record(seeded):
    # D-299: name the flow so the bind is deterministic (Order__c now has two
    # flows TRIGGERS_ON it). No trigger_fields -> today's padding-only create.
    r = _run(seeded, _intent("automation-effect-claim", "positive", "Order__c",
                             field_name="Order__c.Status__c", expected_value="Activated",
                             automation_name="Stamp_Order_Status"))
    o = r.outcome
    assert o.outcome_kind == OutcomeKind.DRAFT
    body = r.emission.asserted_truth
    assert body.kind == "automation-effect-claim"
    assert body.automation_primitive == "flow"
    assert body.automation.external_id == "Stamp_Order_Status"
    assert body.expected_effect.kind == "field_change"
    steps = r.emission.observation_realization.steps
    assert [s.step_id for s in steps] == ["create-record", "read-created", "assert-value"]
    # dormant trigger: the create is still padding-only (empty)
    assert steps[0].field_values == {}


def test_automation_effect_binds_the_named_flow_among_many(seeded):
    # D-299: Order__c has TWO flows TRIGGERS_ON it; the requirement names the
    # SECOND — the claim must bind THAT flow, not the first-encountered one.
    r = _run(seeded, _intent("automation-effect-claim", "positive", "Order__c",
                             field_name="Order__c.Status__c", expected_value="Activated",
                             automation_name="Escalate_Order"))
    assert r.outcome.outcome_kind == OutcomeKind.DRAFT
    assert r.emission.asserted_truth.automation.external_id == "Escalate_Order"


def test_automation_effect_named_flow_absent_refuses(seeded):
    # D-299: a requirement-named flow that does NOT TRIGGERS_ON the subject is a
    # genuine grounding miss — refuse, never bind a different flow.
    r = _run(seeded, _intent("automation-effect-claim", "positive", "Order__c",
                             field_name="Order__c.Status__c", expected_value="Activated",
                             automation_name="Ghost_Flow"),
             expect_emit=False)
    assert r.outcome.outcome_kind == OutcomeKind.REFUSAL


def test_automation_effect_entry_condition_sets_the_create(seeded):
    # D-299: the grounded entry-condition trigger makes the create SET that field
    # (so the flow's entry gate fires) while the asserted effect field stays
    # org-produced (absent from the create).
    r = _run(seeded, _intent("automation-effect-claim", "positive", "Order__c",
                             field_name="Order__c.Status__c", expected_value="Activated",
                             automation_name="Stamp_Order_Status",
                             trigger_fields=[{"field_name": "Order__c.Stage__c",
                                              "value": "Submitted"}]))
    assert r.outcome.outcome_kind == OutcomeKind.DRAFT
    steps = r.emission.observation_realization.steps
    assert steps[0].field_values == {"Order__c.Stage__c": "Submitted"}
    assert "Order__c.Status__c" not in steps[0].field_values


def test_automation_effect_unverifiable_trigger_dropped_not_refused(seeded):
    # D-299 drop-never-refuse: an unverifiable entry-condition field is DROPPED
    # (never guessed) and the claim still emits its (now padding-only) shape —
    # an over-proposed trigger never regresses a previously-emittable claim.
    r = _run(seeded, _intent("automation-effect-claim", "positive", "Order__c",
                             field_name="Order__c.Status__c", expected_value="Activated",
                             automation_name="Stamp_Order_Status",
                             trigger_fields=[{"field_name": "Order__c.Nope__c",
                                              "value": "X"}]))
    assert r.outcome.outcome_kind == OutcomeKind.DRAFT
    assert r.emission.observation_realization.steps[0].field_values == {}


def test_automation_effect_effect_field_as_trigger_is_excluded_k16(seeded):
    # k16 truth-bearing guard (adversarial-review S-1): if the requirement lists
    # the EFFECT field itself as a trigger, the substrate DROPS it — the create
    # must never plant the value-under-test (else the assert passes without the
    # Flow firing: a silent wrong-green). Enforced by the substrate, not the
    # prompt. Here the ONLY proposed trigger is the effect field -> shallow shape.
    r = _run(seeded, _intent("automation-effect-claim", "positive", "Order__c",
                             field_name="Order__c.Status__c", expected_value="Activated",
                             automation_name="Stamp_Order_Status",
                             trigger_fields=[{"field_name": "Order__c.Status__c",
                                              "value": "Activated"}]))
    assert r.outcome.outcome_kind == OutcomeKind.DRAFT
    create = r.emission.observation_realization.steps[0]
    assert create.field_values == {}                       # the effect field was dropped
    assert "Order__c.Status__c" not in create.field_values


def test_automation_effect_multi_field_trigger_end_to_end(seeded):
    # D-299 N-1 (adversarial-review): prove the governance->emission composition
    # with MORE THAN ONE trigger pair through the real runtime. Two verified,
    # non-effect fields both land on the create; the effect field stays absent.
    r = _run(seeded, _intent("automation-effect-claim", "positive", "Order__c",
                             field_name="Order__c.Status__c", expected_value="Activated",
                             automation_name="Stamp_Order_Status",
                             trigger_fields=[
                                 {"field_name": "Order__c.Stage__c", "value": "Submitted"},
                                 {"field_name": "Order__c.Priority__c", "value": "High"}]))
    assert r.outcome.outcome_kind == OutcomeKind.DRAFT
    create = r.emission.observation_realization.steps[0]
    assert create.field_values == {"Order__c.Stage__c": "Submitted",
                                   "Order__c.Priority__c": "High"}
    assert "Order__c.Status__c" not in create.field_values


def test_automation_effect_cross_object(seeded):
    r = _run(seeded, _intent("automation-effect-claim", "positive", "Order__c",
                             effect_object="Order_Log__c",
                             effect_lookup_field="Order_Log__c.Order__c"))
    o = r.outcome
    assert o.outcome_kind == OutcomeKind.DRAFT
    steps = r.emission.observation_realization.steps
    assert [s.step_id for s in steps] == ["create-record", "read-effect", "assert-effect"]
    read = steps[1]
    assert read.target.external_id == "Order_Log__c"
    assert "WHERE Order__c = '$create-record.id'" in read.soql
    assert steps[2].predicate.predicate == "exists"


def test_automation_effect_cross_object_with_field(seeded):
    r = _run(seeded, _intent("automation-effect-claim", "positive", "Order__c",
                             effect_object="Order_Log__c",
                             effect_lookup_field="Order_Log__c.Order__c",
                             effect_field="Order_Log__c.Level__c",
                             effect_value="INFO"))
    steps = r.emission.observation_realization.steps
    assert steps[2].predicate.predicate == "equals"
    assert steps[2].predicate.value == "INFO"
    assert "Level__c" in steps[1].soql


def test_automation_effect_bad_lookup_defers(seeded):
    r = _run(seeded, _intent("automation-effect-claim", "positive", "Order__c",
                             effect_object="Order_Log__c",
                             effect_lookup_field="Order_Log__c.Nope__c"),
             expect_emit=False)
    assert r.outcome.refusal_kind == RefusalKind.EMISSION_DEFERRED
    assert "cannot correlate" in r.outcome.refusals[0].payload["detail"]


def test_parent_stamp_emits_parent_first_chain_with_not_null(seeded):
    # D-227: the Flow on Order_Log__c stamps the parent Order__c (via the
    # trigger record's own lookup). Value-less stamp -> not_null assert.
    r = _run(seeded, _intent("automation-effect-claim", "positive", "Order_Log__c",
                             effect_object="Order__c",
                             effect_via_lookup_field="Order_Log__c.Order__c",
                             effect_field="Order__c.Status__c"))
    o = r.outcome
    assert o.outcome_kind == OutcomeKind.DRAFT
    body = r.emission.asserted_truth
    assert body.automation.external_id == "Log_Effects"
    steps = r.emission.observation_realization.steps
    assert [s.step_id for s in steps] == [
        "create-parent", "create-record", "read-effect", "assert-effect"]
    assert steps[0].target_object.external_id == "Order__c"
    assert steps[1].target_object.external_id == "Order_Log__c"
    assert steps[1].field_values == {"Order_Log__c.Order__c": "$create-parent.id"}
    assert "WHERE Id = '$create-parent.id'" in steps[2].soql
    assert steps[3].predicate.predicate == "not_null"


def test_parent_stamp_bad_via_lookup_defers(seeded):
    r = _run(seeded, _intent("automation-effect-claim", "positive", "Order_Log__c",
                             effect_object="Order__c",
                             effect_via_lookup_field="Order_Log__c.Nope__c",
                             effect_field="Order__c.Status__c"),
             expect_emit=False)
    assert r.outcome.refusal_kind == RefusalKind.EMISSION_DEFERRED
    assert "effect_via_lookup_field" in r.outcome.refusals[0].payload["detail"]


def test_parent_stamp_without_effect_field_defers(seeded):
    # A stamp without a named field is unobservable (the parent row trivially
    # exists) — defer with the specific detail.
    r = _run(seeded, _intent("automation-effect-claim", "positive", "Order_Log__c",
                             effect_object="Order__c",
                             effect_via_lookup_field="Order_Log__c.Order__c"),
             expect_emit=False)
    assert r.outcome.refusal_kind == RefusalKind.EMISSION_DEFERRED
    assert "effect_field" in r.outcome.refusals[0].payload["detail"]


def test_automation_effect_without_flow_is_ungrounded(seeded):
    # Invoice has a Field but NO record-triggered Flow: the real dimension
    # (not the any-field proxy) must refuse it
    r = _run(seeded, _intent("automation-effect-claim", "positive", "Invoice",
                             field_name="Invoice.Amount", expected_value="1"),
             expect_emit=False)
    assert r.outcome.outcome_kind == OutcomeKind.REFUSAL
    assert r.outcome.refusal_kind == RefusalKind.UNGROUNDED_CLAIM


# ---------------------------------------------------------------------------
# D-304: the formula automation primitive — a calculated field IS an automation.
# ---------------------------------------------------------------------------

def test_formula_primitive_grounds_on_a_calculated_field(seeded):
    # automation_name resolves to a CALCULATED field (not a Flow): the claim
    # binds it as the automation with primitive='formula'; trigger_fields carry
    # the formula INPUTS (verified as parsed refs); the observed field stays
    # org-produced (absent from the create — k16 via the existing exclude).
    r = _run(seeded, _intent("automation-effect-claim", "positive", "Order__c",
                             field_name="Order__c.Total_With_Tax__c",
                             expected_value=110,
                             automation_name="Order__c.Total_With_Tax__c",
                             trigger_fields=[{"field_name": "Order__c.Subtotal__c",
                                              "value": 100}]))
    assert r.outcome.outcome_kind == OutcomeKind.DRAFT
    body = r.emission.asserted_truth
    assert body.automation_primitive == "formula"
    assert body.automation.external_id == "Order__c.Total_With_Tax__c"
    create = r.emission.observation_realization.steps[0]
    assert create.field_values == {"Order__c.Subtotal__c": 100}
    assert "Order__c.Total_With_Tax__c" not in create.field_values


def test_formula_primitive_keeps_staging_triggers(seeded):
    # D-304 (revised at impl): NON-input trigger fields are KEPT — they are
    # legitimate VR-survival staging (the D-299 class), and the condition
    # parser cannot parse value formulas anyway. BELONGS_TO + the k16 exclude
    # (the observed field itself can never be a trigger) are the guards.
    r = _run(seeded, _intent("automation-effect-claim", "positive", "Order__c",
                             field_name="Order__c.Total_With_Tax__c",
                             expected_value=110,
                             automation_name="Order__c.Total_With_Tax__c",
                             trigger_fields=[
                                 {"field_name": "Order__c.Subtotal__c", "value": 100},
                                 {"field_name": "Order__c.Stage__c", "value": "X"},
                                 {"field_name": "Order__c.Total_With_Tax__c",
                                  "value": 999}]))          # k16: dropped
    assert r.outcome.outcome_kind == OutcomeKind.DRAFT
    create = r.emission.observation_realization.steps[0]
    assert create.field_values == {"Order__c.Subtotal__c": 100,
                                   "Order__c.Stage__c": "X"}
    assert "Order__c.Total_With_Tax__c" not in create.field_values


def test_formula_primitive_refuses_cross_object_shape(seeded):
    # A formula computes on its own record — cross-object hints refuse.
    r = _run(seeded, _intent("automation-effect-claim", "positive", "Order__c",
                             automation_name="Order__c.Total_With_Tax__c",
                             effect_object="Order_Log__c",
                             effect_lookup_field="Order_Log__c.Order__c"),
             expect_emit=False)
    assert r.outcome.outcome_kind == OutcomeKind.REFUSAL
    assert "formula" in r.outcome.refusals[0].payload["detail"]


def test_named_non_calculated_field_still_refuses(seeded):
    # A named automation that is a PLAIN field (not calculated, not a Flow)
    # remains a genuine grounding miss.
    r = _run(seeded, _intent("automation-effect-claim", "positive", "Order__c",
                             field_name="Order__c.Status__c", expected_value="X",
                             automation_name="Order__c.Stage__c"),
             expect_emit=False)
    assert r.outcome.outcome_kind == OutcomeKind.REFUSAL


def test_flow_primitive_stays_flow(seeded):
    # The pre-D-304 flow path is byte-identical: primitive='flow' on the body.
    r = _run(seeded, _intent("automation-effect-claim", "positive", "Order__c",
                             field_name="Order__c.Status__c", expected_value="Activated",
                             automation_name="Stamp_Order_Status"))
    assert r.emission.asserted_truth.automation_primitive == "flow"
