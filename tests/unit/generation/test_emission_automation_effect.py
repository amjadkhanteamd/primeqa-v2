"""Unit tests for automation-effect emission-authoring (D-210 / D-299).

`author_emission(GroundedAutomationEffect(...))` authors an automation-effect
claim + the observe-the-org data recipe (create the subject WITHOUT the asserted
field -> read -> assert the org-produced value). The grounding fact is
constructed directly here to unit-test the author-capability in isolation,
mirroring `test_emission_positive.py`.

**D-299 drift-guard**: the new `trigger_fields` slot is DORMANT in S1 — an empty
tuple (the default) must author EXACTLY today's shallow recipe (a padding-only
create that sets nothing, so the flow's entry gate never fires — observability,
not correctness). This file pins that byte-identical shape so a later slice that
consumes `trigger_fields` cannot silently regress the empty-tuple path.
"""
from __future__ import annotations

from uuid import uuid4

from primeqa.generation.emission import (
    GroundedAutomationEffect,
    _Endpoint,
    author_emission,
)
from primeqa.test_representation.models.recipes.data_recipe import CreateStep


def _grounded_same_record(*, trigger_fields=()):
    """Same-record automation-effect: a Flow stamps `effect_field` on the
    subject when it is created."""
    return GroundedAutomationEffect(
        archetype="data_behavior", claim_kind="automation-effect-claim",
        version_seq=7,
        subject=_Endpoint(entity_id=uuid4(), entity_type="Object",
                          external_id="Order__c"),
        automation=_Endpoint(entity_id=uuid4(), entity_type="Flow",
                             external_id="Stamp_Order_Status"),
        requirement_excerpt="when an Order is created the Flow stamps Status__c",
        effect_field=_Endpoint(entity_id=uuid4(), entity_type="Field",
                               external_id="Order__c.Status__c"),
        effect_value="Activated",
        trigger_fields=trigger_fields,
    )


def test_default_trigger_fields_is_empty_tuple():
    # D-299 S1: the dormant slot defaults to () — no live producer yet.
    assert GroundedAutomationEffect(
        archetype="data_behavior", claim_kind="automation-effect-claim",
        version_seq=1,
        subject=_Endpoint(entity_id=uuid4(), entity_type="Object", external_id="X"),
        automation=_Endpoint(entity_id=uuid4(), entity_type="Flow", external_id="F"),
        requirement_excerpt="x",
    ).trigger_fields == ()


def test_empty_trigger_fields_authors_todays_shallow_recipe():
    # D-299 drift-guard: trigger_fields=() -> the create sets NOTHING (the org's
    # automation must produce the effect). This is the exact shape asserted by
    # the integration test `test_automation_effect_same_record`.
    b = author_emission(_grounded_same_record(trigger_fields=()))
    body = b.asserted_truth
    assert body.kind == "automation-effect-claim"
    assert body.automation_primitive == "flow"
    steps = b.observation_realization.steps
    assert [s.step_id for s in steps] == ["create-record", "read-created", "assert-value"]
    create = steps[0]
    assert isinstance(create, CreateStep)
    # the padding-only create is EMPTY — nothing set on the subject
    assert create.field_values == {}
    # D-327: the description narrates the trigger STRUCTURALLY — the
    # requirement excerpt is provenance, never identity. Empty trigger_fields
    # → the bare-create narration (no staged clause, no excerpt).
    assert body.triggering_action.description == "creating a Order__c"


def test_trigger_fields_set_the_entry_condition_on_the_create():
    # D-299 S2: a grounded entry-condition trigger makes the create SET those
    # fields so the Flow's entry gate fires — while the asserted effect field
    # stays org-produced (deliberately absent from the create). Keys stay
    # object-qualified (S4 bare-ifies them); values are carried raw.
    trigger = (
        (_Endpoint(entity_id=uuid4(), entity_type="Field",
                   external_id="Order__c.Stage__c"), "Submitted"),
        (_Endpoint(entity_id=uuid4(), entity_type="Field",
                   external_id="Order__c.Priority__c"), "High"),
    )
    b = author_emission(_grounded_same_record(trigger_fields=trigger))
    steps = b.observation_realization.steps
    assert [s.step_id for s in steps] == ["create-record", "read-created", "assert-value"]
    create = steps[0]
    assert create.field_values == {"Order__c.Stage__c": "Submitted",
                                   "Order__c.Priority__c": "High"}
    # the asserted effect field is NOT set by the create — the Flow must produce it
    assert "Order__c.Status__c" not in create.field_values
    # the claim's triggering event names the entry condition (explainability)
    desc = b.asserted_truth.triggering_action.description
    assert "Order__c.Stage__c" in desc and "Order__c.Priority__c" in desc


def test_excerpt_is_not_identity_bearing_same_intent_hashes_together():
    # D-327: two intents that ground to the SAME automation effect (same
    # subject, automation, effect, staged trigger) but carry DIFFERENT
    # requirement-excerpt slices must produce identity-equal claims, so
    # persistence dedup (SPEC §7.7) collapses them instead of minting
    # duplicate tests (the req-302 f91fd866/45180120 pair).
    from primeqa.test_representation.identity_hash import compute_identity_hash

    shared = dict(
        archetype="data_behavior", claim_kind="automation-effect-claim",
        version_seq=7,
        subject=_Endpoint(entity_id=uuid4(), entity_type="Object",
                          external_id="Order__c"),
        automation=_Endpoint(entity_id=uuid4(), entity_type="Flow",
                             external_id="Stamp_Order_Status"),
        effect_field=_Endpoint(entity_id=uuid4(), entity_type="Field",
                               external_id="Order__c.Status__c"),
        effect_value="Activated",
        trigger_fields=(
            (_Endpoint(entity_id=uuid4(), entity_type="Field",
                       external_id="Order__c.Stage__c"), "Submitted"),
        ),
    )
    a = author_emission(GroundedAutomationEffect(
        requirement_excerpt="the Flow stamps Status on submitted orders",
        **shared))
    b = author_emission(GroundedAutomationEffect(
        requirement_excerpt="Status: Activated, Assigned To: Ops",  # different slice
        **shared))
    ha = compute_identity_hash(a.archetype, a.claim_kind,
                               a.asserted_truth, a.semantic_conditions)
    hb = compute_identity_hash(b.archetype, b.claim_kind,
                               b.asserted_truth, b.semantic_conditions)
    assert ha == hb
