"""D-337 — the authoring-time staged-state VR-conflict guard, end to end.

Real S1 grounding through the runtime: a claim whose STAGED values provably
fire one of the subject's ACTIVE validation rules REFUSES at grounding
(emission-deferred, the detail naming the rule), across the staged
surfaces — automation-effect ``trigger_fields`` / ``update_trigger_fields``,
acceptance conditions, and approval-arc prohibition conditions. Controls
prove the same intents DRAFT when the staged values satisfy the rule
(Kleene: only provable violations refuse).

Fixtures: the isolated ``Rebate__c`` island (VR ``Amount__c < 0``, a Flow,
one active approval) and ``Quote`` (VR ``Total__c < 0``, field ``Total__c``).
"""
from __future__ import annotations

from primeqa.generation.enums import OutcomeKind

from .test_automation_vertical import _intent, _run


def _detail(r) -> str:
    return r.outcome.refusals[0].payload["detail"]


# ---------------------------------------------------------------------------
# automation-effect: staged trigger_fields (same-record)
# ---------------------------------------------------------------------------

def test_trigger_state_firing_a_vr_refuses(seeded):
    r = _run(seeded, _intent(
        "automation-effect-claim", "positive", "Rebate__c",
        automation_name="Stamp_Rebate_Status",
        field_name="Rebate__c.Status__c", expected_value="Approved",
        trigger_fields=[{"field_name": "Rebate__c.Amount__c", "value": -5}]),
        expect_emit=False)
    assert r.outcome.outcome_kind == OutcomeKind.REFUSAL
    assert "Rebate__c.NonNegativeAmount" in _detail(r)
    assert "staged create state" in _detail(r)


def test_trigger_state_satisfying_the_vr_drafts(seeded):
    r = _run(seeded, _intent(
        "automation-effect-claim", "positive", "Rebate__c",
        automation_name="Stamp_Rebate_Status",
        field_name="Rebate__c.Status__c", expected_value="Approved",
        trigger_fields=[{"field_name": "Rebate__c.Amount__c", "value": 5}]))
    assert r.outcome.outcome_kind == OutcomeKind.DRAFT
    # the staged pair rides the recipe unchanged (the guard never rewrites)
    create = r.emission.observation_realization.steps[0]
    assert create.field_values == {"Rebate__c.Amount__c": 5}


def test_update_overlay_firing_a_vr_refuses_naming_the_update(seeded):
    # The req-302 live-catch shape: the create stages a VALID state; the
    # D-306 update phase drives it into provable violation — the org would
    # reject the update, so the recompute is never observed.
    r = _run(seeded, _intent(
        "automation-effect-claim", "positive", "Rebate__c",
        automation_name="Stamp_Rebate_Status",
        field_name="Rebate__c.Status__c", expected_value="Approved",
        trigger_fields=[{"field_name": "Rebate__c.Amount__c", "value": 5}],
        update_trigger_fields=[
            {"field_name": "Rebate__c.Amount__c", "value": -5}]),
        expect_emit=False)
    assert r.outcome.outcome_kind == OutcomeKind.REFUSAL
    assert "Rebate__c.NonNegativeAmount" in _detail(r)
    assert "staged update state" in _detail(r)


# ---------------------------------------------------------------------------
# acceptance: staged equals conditions
# ---------------------------------------------------------------------------

def test_acceptance_conditions_firing_a_vr_refuse(seeded):
    # An acceptance claim asserting the org ACCEPTS a state its own rule
    # provably rejects is self-contradictory against org config.
    r = _run(seeded, _intent(
        "acceptance-claim", "positive", "Quote",
        acceptance_conditions=[{"field": "Quote.Total__c",
                                "predicate": "equals", "value": -5}]),
        expect_emit=False)
    assert r.outcome.outcome_kind == OutcomeKind.REFUSAL
    assert "Quote.NonNegativeTotal" in _detail(r)


def test_acceptance_conditions_satisfying_the_vr_draft(seeded):
    r = _run(seeded, _intent(
        "acceptance-claim", "positive", "Quote",
        acceptance_conditions=[{"field": "Quote.Total__c",
                                "predicate": "equals", "value": 100}]))
    assert r.outcome.outcome_kind == OutcomeKind.DRAFT


# ---------------------------------------------------------------------------
# approval-arc prohibition: staged conditions (the create must SUCCEED)
# ---------------------------------------------------------------------------

def _arc_intent(amount):
    return _intent(
        "prohibition-claim", "negative", "Rebate__c",
        operation="modify_record",
        approval_actions=["submit", "approve"],
        attempted_change={"field_name": "Rebate__c.Status__c",
                          "value": "Reopened"},
        rejection_conditions=[{"field": "Rebate__c.Amount__c",
                               "predicate": "equals", "value": amount}])


def test_arc_conditions_firing_a_vr_refuse(seeded):
    # The arc's setup create must succeed (the actions + the rejected update
    # ARE the test) — staged conditions that provably fire ANY active VR
    # bounce the create before the arc ever runs.
    r = _run(seeded, _arc_intent(-5), expect_emit=False)
    assert r.outcome.outcome_kind == OutcomeKind.REFUSAL
    assert "Rebate__c.NonNegativeAmount" in _detail(r)
    assert "staged create state" in _detail(r)


def test_arc_conditions_satisfying_the_vr_draft(seeded):
    r = _run(seeded, _arc_intent(5))
    assert r.outcome.outcome_kind == OutcomeKind.DRAFT
    body = r.emission.asserted_truth
    assert list(body.approval_actions) == ["submit", "approve"]
