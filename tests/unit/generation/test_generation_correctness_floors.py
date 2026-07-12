"""Correctness floors unmasked by the B0 exit gate (no PG, no LLM).

Three pre-existing wrong-green generators became reachable once B0's grounded
recovery let generation converge past name-resolution failures. Each was
live-observed as a stored defective claim on the FB-V1 benchmark requirement
(req-320) before being fixed; these tests pin the closed floors:

  1. placeholder literals — "<higher tier>"-style model placeholders stored
     as literal expected values (value-claim / automation-effect);
  2. the approval arm of the create-scoped state-transition causality floor —
     an approval process cannot fire on a bare create, so it can never be the
     producer of a create-scoped transition (a vacuous stage-the-value claim
     was credited as the FL01 default-priority AC);
  3. cross-object producer attribution — a cross-object effect NO flow
     verifiably produces must refuse, never bind the first-encountered
     flows[0] (a Task-creation effect was attributed to the before-save
     priority-default flow, twice).
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from primeqa.generation import governance_core as gc


def _ent(entity_type, api, label=None, attrs=None):
    return SimpleNamespace(id=uuid4(), entity_type=entity_type,
                           sf_api_name=api, display_name=label,
                           attributes=attrs or {})


class _FakeS1:
    """Exact-name resolution + a fixed neighborhood for one subject object."""

    def __init__(self, entities, rows_by_object=None):
        self._entities = entities
        self._rows_by_object = rows_by_object or {}

    def get_entities(self, entity_type, at_seq, filters=None):
        out = [e for e in self._entities if e.entity_type == entity_type]
        if filters and "sf_api_name" in filters:
            out = [e for e in out if e.sf_api_name == filters["sf_api_name"]]
        return out

    def get_related(self, subject_id, edge_types, direction, at_seq):
        for obj_api, rows in self._rows_by_object.items():
            owner = next((e for e in self._entities
                          if e.sf_api_name == obj_api), None)
            if owner is not None and owner.id == subject_id:
                return rows
        return []


def _ctx(at=128):
    return SimpleNamespace(semantic_context=SimpleNamespace(s1_version_seq=at),
                           requirement_text="PLS FB Order lifecycle")


# ---------------------------------------------------------------------------
# 1. placeholder-literal normalization
# ---------------------------------------------------------------------------

def test_placeholder_detection():
    assert gc._is_placeholder_value("<UNKNOWN>")
    assert gc._is_placeholder_value("<higher tier>")
    assert gc._is_placeholder_value("<canonical uppercase normalized>")
    assert not gc._is_placeholder_value("Standard")
    assert not gc._is_placeholder_value("a < b > c")
    assert not gc._is_placeholder_value(100000)
    assert not gc._is_placeholder_value(None)


def test_scrub_placeholder_values_normalizes_value_slots_only():
    hint = {
        "entity_type": "Object", "sf_api_name": "PLS_FB_Order__c",
        "field_name": "PLS_FB_Order__c.PLS_FB_Tier__c",
        "expected_value": "<higher tier>",
        "effect_value": "<UNKNOWN>",
        "automation_name": "<UNKNOWN>",           # sentinel semantics kept
        "rejection_conditions": [{"field": "f", "value": "<UNKNOWN>"}],
    }
    out = gc._scrub_placeholder_values(hint)
    assert out["expected_value"] is None
    assert out["effect_value"] is None
    assert out["automation_name"] == "<UNKNOWN>"
    assert out["rejection_conditions"][0]["value"] == "<UNKNOWN>"
    assert hint["expected_value"] == "<higher tier>"   # input not mutated
    real = gc._scrub_placeholder_values({"expected_value": "Standard"})
    assert real["expected_value"] == "Standard"


# ---------------------------------------------------------------------------
# 2 + 3. producer floors, driven end-to-end through resolve_intent
# ---------------------------------------------------------------------------

def _order_world(*, approvals=(), flows=()):
    order = _ent("Object", "PLS_FB_Order__c", "PLS FB Order")
    priority = _ent("Field", "PLS_FB_Order__c.PLS_FB_Priority__c", "Priority",
                    attrs={"data_type": "Picklist"})
    rows = [SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=priority)]
    rows += [SimpleNamespace(edge_type=gc.EDGE_FLOW, entity=f) for f in flows]
    rows += [SimpleNamespace(edge_type=gc.EDGE_FLOW, entity=a) for a in approvals]
    s1 = _FakeS1(entities=[order, priority, _ent("Object", "Task", "Task")]
                 + list(flows) + list(approvals),
                 rows_by_object={"PLS_FB_Order__c": rows})
    return gc.GovernanceCore(s1)


def _state():
    return SimpleNamespace(control_facts=None, groundings=[])


def test_create_scoped_transition_refuses_with_only_an_approval():
    """The approval arm is gone: an active approval process on the subject is
    NOT a producer for a create-scoped transition (approvals fire only on
    explicit submission, never on a bare create)."""
    appr = _ent("ApprovalProcess", "PLS_FB_Large_Order_Approval",
                "Large Order Approval", attrs={"_is_active": True})
    core = _order_world(approvals=[appr])
    state = _state()
    res = core.resolve_intent(
        intent_input={
            "requirement_excerpt": ("an order raised without a stated priority "
                                    "shows a priority of Standard once saved"),
            "intent_descriptor": {
                "ac_ref": 1, "archetype_hint": "data_behavior",
                "polarity_hint": "positive",
                "claim_kind_hint": "state-transition-claim",
                "requirement_excerpt": ("an order raised without a stated "
                                        "priority shows Standard once saved"),
                "target_subject_hint": {
                    "entity_type": "Object", "sf_api_name": "PLS_FB_Order__c",
                    "field_name": "PLS_FB_Order__c.PLS_FB_Priority__c",
                    "expected_value": "Standard"}}},
        ctx=_ctx(), state=state)
    assert res.refusal is not None, "expected a refusal, got a grounded result"
    detail = res.refusal.payload.get("detail", "")
    assert "cannot fire on a bare create" in detail
    assert res.refusal.payload.get("detail_source") == "substrate"
    assert not state.groundings, "nothing may be stashed for emission"


def test_cross_object_effect_without_producer_refuses_not_flows0():
    """A cross-object effect NO flow on the subject verifiably produces must
    REFUSE — never bind the first-encountered flow. Live-observed wrong-green:
    'a Task record is created' attributed to PLS_FB_FL01_Default_Priority (a
    before-save priority default) because flows[0] rode the cross-object
    emission path past the same-record-only no-producer check."""
    fl01 = _ent("Flow", "PLS_FB_FL01_Default_Priority", "Default Priority",
                attrs={"Metadata": {"assignments": [{"name": "x"}]}})
    core = _order_world(flows=[fl01])
    state = _state()
    excerpt = ("when an order is confirmed a fulfilment task appears "
               "for the operations team")
    res = core.resolve_intent(
        intent_input={
            "requirement_excerpt": excerpt,
            "intent_descriptor": {
                "ac_ref": 9, "archetype_hint": "data_behavior",
                "polarity_hint": "positive",
                "claim_kind_hint": "automation-effect-claim",
                "requirement_excerpt": excerpt,
                "target_subject_hint": {
                    "entity_type": "Object", "sf_api_name": "PLS_FB_Order__c",
                    "effect_object": "Task", "effect_lookup_field": "WhatId"}}},
        ctx=_ctx(), state=state)
    assert res.refusal is not None, "expected a refusal, got a grounded result"
    detail = res.refusal.payload.get("detail", "")
    assert "cannot be attributed" in detail
    assert res.refusal.payload.get("detail_source") == "substrate"
    assert not state.groundings, "nothing may be stashed for emission"
