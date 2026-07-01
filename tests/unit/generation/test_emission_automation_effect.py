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
