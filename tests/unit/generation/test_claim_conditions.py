"""D-383 (SUB-1) — the entry-gate staging becomes claim truth.

Automation-effect claims now author their trigger staging as
``semantic_conditions`` (S2-visible), with the identity re-key pinned as
INTENTIONAL (the D-339/D-353 migration consequence)."""
from __future__ import annotations

from uuid import uuid4

from primeqa.generation.emission import (
    GroundedAutomationEffect, _Endpoint, _trigger_conditions, author_emission)
from primeqa.test_representation.identity_hash import compute_identity_hash


def _ep(api):
    return _Endpoint(entity_id=uuid4(), entity_type="Field", external_id=api)


def _grounding(trigger_fields=(), update_trigger_fields=()):
    return GroundedAutomationEffect(
        archetype="data_behavior", claim_kind="automation-effect-claim",
        version_seq=7,
        subject=_Endpoint(entity_id=uuid4(), entity_type="Object",
                          external_id="PLS_FB_Order__c"),
        automation=_Endpoint(entity_id=uuid4(), entity_type="Flow",
                             external_id="PLS_FB_FL01_Default_Priority"),
        requirement_excerpt="orders default to Standard priority",
        effect_field=_ep("PLS_FB_Order__c.PLS_FB_Priority__c"),
        effect_value="Standard",
        automation_primitive="flow",
        trigger_fields=tuple(trigger_fields),
        update_trigger_fields=tuple(update_trigger_fields))


def test_trigger_staging_becomes_semantic_conditions():
    g = _grounding(
        trigger_fields=((_ep("PLS_FB_Order__c.PLS_FB_Amount__c"), 100),
                        (_ep("PLS_FB_Order__c.PLS_FB_Status__c"), "Draft")),
        update_trigger_fields=((_ep("PLS_FB_Order__c.PLS_FB_Status__c"),
                                "Submitted"),))
    body = _trigger_conditions(g)
    got = {c.subject.external_id: c.value for c in body.conditions}
    # update wins the shared field; all clauses equals; sorted determinism
    assert got == {"PLS_FB_Order__c.PLS_FB_Amount__c": 100,
                   "PLS_FB_Order__c.PLS_FB_Status__c": "Submitted"}
    assert all(c.predicate == "equals" for c in body.conditions)
    assert [c.subject.external_id for c in body.conditions] == sorted(
        c.subject.external_id for c in body.conditions)


def test_bundle_carries_conditions_and_rekeys_intentionally():
    staged = _grounding(
        trigger_fields=((_ep("PLS_FB_Order__c.PLS_FB_Status__c"), "Draft"),))
    bare = _grounding()
    b_staged = author_emission(staged)
    b_bare = author_emission(bare)
    assert len(b_staged.semantic_conditions.conditions) == 1
    assert b_staged.semantic_conditions.conditions[0].value == "Draft"
    assert b_bare.semantic_conditions.conditions == []
    # determinism: same grounding -> same identity
    b2 = author_emission(staged)
    h = lambda b: compute_identity_hash(b.archetype, b.claim_kind,        # noqa: E731
                                        b.asserted_truth,
                                        b.semantic_conditions)
    assert h(b_staged) == h(b2)
    # THE INTENTIONAL RE-KEY (D-383): staged vs unstaged are now DISTINCT
    # identities — pre-D-383 both hashed with empty conditions. Migration =
    # deprecate-then-regen (D-353).
    assert h(b_staged) != h(b_bare)


def test_float_values_coerce_to_identity_safe_strings():
    """D-383.1 (live-caught on env-59): a 250000.01 staged threshold crashed
    persistence — floats are forbidden in identity-bearing content
    (SPEC §6.3.2); the condition value coerces to its shortest-repr string
    exactly like the hint→claim boundary (D-304)."""
    g = _grounding(trigger_fields=(
        (_ep("PLS_FB_Order__c.PLS_FB_Order_Value__c"), 250000.01),))
    body = _trigger_conditions(g)
    [c] = body.conditions
    assert c.value == "250000.01" and isinstance(c.value, str)
    # bools are NOT floats — they pass through
    g2 = _grounding(trigger_fields=(
        (_ep("PLS_FB_Order__c.PLS_FB_Escalated__c"), True),))
    assert _trigger_conditions(g2).conditions[0].value is True


def test_none_values_and_dupes_never_author():
    g = _grounding(trigger_fields=(
        (_ep("PLS_FB_Order__c.A__c"), None),
        (_ep("PLS_FB_Order__c.B__c"), "x"),
        (_ep("PLS_FB_Order__c.B__c"), "y")))
    body = _trigger_conditions(g)
    assert [(c.subject.external_id, c.value) for c in body.conditions] == [
        ("PLS_FB_Order__c.B__c", "x")]