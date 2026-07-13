"""Completion Program E1 — cross-object CREATE evidence via typed effect ops
(no PG, no LLM). The real FL04 fixture Metadata drives the flow entity.

When exactly ONE flow's typed IR creates the effect object, the substrate
derives: the correlation (the op's subject_ref-Id assignment), the asserted
value for a named-but-unvalued effect field (literal or relative-date — the
org-defines-the-value class extended cross-object), the attribution, and —
for an Update-trigger producer — the create→update transition from its
EqualTo entry guard."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from uuid import uuid4

from primeqa.generation import governance_core as gc
from primeqa.test_representation.temporal import relative_date

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "semantic",
                       "fixtures", "pls_fb_flows",
                       "PLS_FB_FL04_Confirmation_Task.json")


def _ent(entity_type, api, label=None, attrs=None):
    return SimpleNamespace(id=uuid4(), entity_type=entity_type,
                           sf_api_name=api, display_name=label,
                           attributes=attrs or {})


class _FakeS1:
    def __init__(self, entities, rows_by_object=None, details=None,
                 picklists=None):
        self._entities = entities
        self._rows_by_object = rows_by_object or {}
        self._details = details or {}
        self._picklists = picklists or {}

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

    def get_entity_details(self, entity_id, at_seq):
        return self._details.get(entity_id, {})

    def get_picklist_values(self, pvs_id, at_seq):
        return self._picklists.get(pvs_id, [])


def _ctx(at=128):
    return SimpleNamespace(semantic_context=SimpleNamespace(s1_version_seq=at),
                           requirement_text="PLS FB Order lifecycle")


def _world(*, status_values=("Draft", "Submitted", "Confirmed")):
    order = _ent("Object", "PLS_FB_Order__c", "PLS FB Order")
    status = _ent("Field", "PLS_FB_Order__c.PLS_FB_Status__c", "Status",
                  attrs={"data_type": "Picklist"})
    task = _ent("Object", "PLS_FB_Fulfilment_Task__c", "PLS FB Fulfilment Task")
    t_order = _ent("Field", "PLS_FB_Fulfilment_Task__c.PLS_FB_Order__c",
                   "Order")
    t_status = _ent("Field", "PLS_FB_Fulfilment_Task__c.PLS_FB_Status__c",
                    "Status")
    t_type = _ent("Field", "PLS_FB_Fulfilment_Task__c.PLS_FB_Type__c", "Type")
    t_due = _ent("Field", "PLS_FB_Fulfilment_Task__c.PLS_FB_Due_Date__c",
                 "Due Date")
    with open(FIXTURE) as f:
        d = json.load(f)
    flow = _ent("Flow", "PLS_FB_FL04_Confirmation_Task", "Confirmation Task",
                attrs={"Metadata": d["Metadata"]})
    pvs = uuid4()
    s1 = _FakeS1(
        entities=[order, status, task, t_order, t_status, t_type, t_due,
                  flow],
        rows_by_object={
            "PLS_FB_Order__c": [
                SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=status),
                SimpleNamespace(edge_type=gc.EDGE_FLOW, entity=flow)],
            "PLS_FB_Fulfilment_Task__c": [
                SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=e)
                for e in (t_order, t_status, t_type, t_due)],
        },
        details={status.id: {"field_type": "picklist",
                             "picklist_value_set_entity_id": pvs}},
        picklists={pvs: [{"value_api_name": v, "is_active": True}
                         for v in status_values]})
    return gc.GovernanceCore(s1)


def _state():
    return SimpleNamespace(control_facts=None, groundings=[])


EXCERPT = ("when an order is confirmed, a fulfilment task appears for the "
           "operations team, linked to the order")


def _intent(**kw):
    d = {"ac_ref": 9, "archetype_hint": "data_behavior",
         "polarity_hint": "positive",
         "claim_kind_hint": "automation-effect-claim",
         "requirement_excerpt": EXCERPT,
         "target_subject_hint": {
             "entity_type": "Object", "sf_api_name": "PLS_FB_Order__c",
             "effect_object": "PLS_FB_Fulfilment_Task__c", **kw}}
    return {"requirement_excerpt": EXCERPT, "intent_descriptor": d}


def _pairs(tf):
    return {ep.external_id.rsplit(".", 1)[-1]: v for ep, v in tf}


# ---------------------------------------------------------------------------
# the grounded path — everything substrate-derived
# ---------------------------------------------------------------------------

def test_bare_cross_object_intent_grounds_fully_substrate_derived():
    # no lookup, no value, no trigger staging proposed — the typed op
    # supplies correlation + the Update-trigger transition
    core = _world()
    state = _state()
    res = core.resolve_intent(intent_input=_intent(), ctx=_ctx(), state=state)
    assert res.refusal is None, getattr(res.refusal, "payload", None)
    [g] = state.groundings
    assert g.automation.external_id == "PLS_FB_FL04_Confirmation_Task"
    assert g.effect_object.external_id == "PLS_FB_Fulfilment_Task__c"
    assert g.effect_lookup_field.external_id == \
        "PLS_FB_Fulfilment_Task__c.PLS_FB_Order__c"     # substrate-derived
    # the transition: create NOT-Confirmed, update INTO Confirmed
    assert _pairs(g.trigger_fields) == {"PLS_FB_Status__c": "Draft"}
    assert _pairs(g.update_trigger_fields) == {"PLS_FB_Status__c": "Confirmed"}


def test_named_unvalued_effect_field_gets_the_op_literal():
    core = _world()
    state = _state()
    res = core.resolve_intent(
        intent_input=_intent(effect_field="PLS_FB_Status__c"),
        ctx=_ctx(), state=state)
    assert res.refusal is None, getattr(res.refusal, "payload", None)
    [g] = state.groundings
    assert g.effect_field.external_id == \
        "PLS_FB_Fulfilment_Task__c.PLS_FB_Status__c"
    assert g.effect_value == "Open"                     # the op's literal


def test_relative_date_effect_field_gets_the_symbolic_expected():
    core = _world()
    state = _state()
    res = core.resolve_intent(
        intent_input=_intent(effect_field="PLS_FB_Due_Date__c"),
        ctx=_ctx(), state=state)
    assert res.refusal is None, getattr(res.refusal, "payload", None)
    [g] = state.groundings
    assert g.effect_value == relative_date(3)           # FL04's own offset


def test_deterministic_across_repeats():
    outs = []
    for _ in range(3):
        core = _world()
        state = _state()
        core.resolve_intent(intent_input=_intent(
            effect_field="PLS_FB_Type__c"), ctx=_ctx(), state=state)
        [g] = state.groundings
        outs.append((g.effect_value,
                     tuple(sorted(_pairs(g.trigger_fields).items())),
                     tuple(sorted(_pairs(g.update_trigger_fields).items()))))
    assert len(set(outs)) == 1
    assert outs[0][0] == "Confirmation"


# ---------------------------------------------------------------------------
# honesty boundaries
# ---------------------------------------------------------------------------

def test_absence_never_stages_the_firing_transition():
    core = _world()
    state = _state()
    res = core.resolve_intent(
        intent_input=_intent(expected_absence=True,
                             trigger_fields=[
                                 {"field_name":
                                  "PLS_FB_Order__c.PLS_FB_Status__c",
                                  "value": "Draft"}]),
        ctx=_ctx(), state=state)
    assert res.refusal is None, getattr(res.refusal, "payload", None)
    [g] = state.groundings
    assert g.expected_absence is True
    assert g.update_trigger_fields == ()    # no transition under absence


def test_unassigned_effect_field_still_refuses_unvalued():
    # a field the op does NOT assign keeps the honest D-335 refusal
    core = _world()
    res = core.resolve_intent(
        intent_input=_intent(effect_field="PLS_FB_Order__c"),
        ctx=_ctx(), state=_state())
    assert res.refusal is not None
    assert "needs its expected_value" in res.refusal.payload.get("detail")


# ---------------------------------------------------------------------------
# E2 — set-update evidence (FL05: cancelling cancels the open tasks)
# ---------------------------------------------------------------------------

FL05_FIXTURE = os.path.join(os.path.dirname(__file__), "..", "semantic",
                            "fixtures", "pls_fb_flows",
                            "PLS_FB_FL05_Cancellation_Sync.json")


def _world_fl05(task_status_values=("Open", "Completed", "Cancelled")):
    order = _ent("Object", "PLS_FB_Order__c", "PLS FB Order")
    status = _ent("Field", "PLS_FB_Order__c.PLS_FB_Status__c", "Status",
                  attrs={"data_type": "Picklist"})
    task = _ent("Object", "PLS_FB_Fulfilment_Task__c", "PLS FB Fulfilment Task")
    t_order = _ent("Field", "PLS_FB_Fulfilment_Task__c.PLS_FB_Order__c",
                   "Order")
    t_status = _ent("Field", "PLS_FB_Fulfilment_Task__c.PLS_FB_Status__c",
                    "Status")
    with open(FL05_FIXTURE) as f:
        d = json.load(f)
    flow = _ent("Flow", "PLS_FB_FL05_Cancellation_Sync", "Cancellation Sync",
                attrs={"Metadata": d["Metadata"]})
    pvs_o, pvs_t = uuid4(), uuid4()
    s1 = _FakeS1(
        entities=[order, status, task, t_order, t_status, flow],
        rows_by_object={
            "PLS_FB_Order__c": [
                SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=status),
                SimpleNamespace(edge_type=gc.EDGE_FLOW, entity=flow)],
            "PLS_FB_Fulfilment_Task__c": [
                SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=e)
                for e in (t_order, t_status)],
        },
        details={status.id: {"field_type": "picklist",
                             "picklist_value_set_entity_id": pvs_o},
                 t_status.id: {"field_type": "picklist",
                               "picklist_value_set_entity_id": pvs_t}},
        picklists={
            pvs_o: [{"value_api_name": v, "is_active": True}
                    for v in ("Draft", "Submitted", "Cancelled")],
            pvs_t: [{"value_api_name": v, "is_active": True}
                    for v in task_status_values]})
    return gc.GovernanceCore(s1)


def test_set_update_grounds_with_premise_children():
    core = _world_fl05()
    state = _state()
    res = core.resolve_intent(intent_input=_intent(), ctx=_ctx(), state=state)
    assert res.refusal is None, getattr(res.refusal, "payload", None)
    [g] = state.groundings
    assert g.automation.external_id == "PLS_FB_FL05_Cancellation_Sync"
    assert g.effect_value == "Cancelled"       # the op's assignment literal
    pc = g.premise_children
    assert pc["count"] == 2
    assert dict(pc["template"]) == {"PLS_FB_Status__c": "Open"}
    assert pc["distractor"] == ("PLS_FB_Status__c", "Completed")
    assert pc["updated_value"] == "Cancelled"
    # the entry transition: create NOT-Cancelled, update INTO Cancelled
    assert _pairs(g.trigger_fields) == {"PLS_FB_Status__c": "Draft"}
    assert _pairs(g.update_trigger_fields) == \
        {"PLS_FB_Status__c": "Cancelled"}


def test_set_update_emission_authors_the_count_shape():
    from primeqa.generation.emission import author_emission
    core = _world_fl05()
    state = _state()
    core.resolve_intent(intent_input=_intent(), ctx=_ctx(), state=state)
    [g] = state.groundings
    bundle = author_emission(g)
    steps = bundle.observation_realization.steps
    kinds = [type(s).__name__ for s in steps]
    # subject + 2 matching + distractor + update + read + assert
    assert kinds == ["CreateStep", "CreateStep", "CreateStep", "CreateStep",
                     "UpdateStep", "ReadStep", "AssertStep"]
    read = steps[5]
    assert "PLS_FB_Status__c = 'Cancelled'" in read.soql
    assert "$create-record.id" in read.soql
    assert steps[6].predicate.predicate == "count_equals"
    assert steps[6].predicate.value == 2
    # the distractor child stages the THIRD state
    assert any(v == "Completed"
               for v in steps[3].field_values.values())
    # deterministic identity across authorings
    b2 = author_emission(g)
    from primeqa.test_representation.identity_hash import compute_identity_hash
    h1 = compute_identity_hash(bundle.archetype, bundle.claim_kind,
                               bundle.asserted_truth,
                               bundle.semantic_conditions)
    h2 = compute_identity_hash(b2.archetype, b2.claim_kind,
                               b2.asserted_truth, b2.semantic_conditions)
    assert h1 == h2


def test_set_update_absence_refuses_honestly():
    # absence never takes the set-update path: an update op cannot ground
    # "no record appears" (nothing creates the object), and without a
    # create producer the correlation lookup stays underivable — refusal,
    # never a fabricated suppression recipe
    core = _world_fl05()
    res = core.resolve_intent(
        intent_input=_intent(expected_absence=True, trigger_fields=[
            {"field_name": "PLS_FB_Order__c.PLS_FB_Status__c",
             "value": "Draft"}]),
        ctx=_ctx(), state=_state())
    assert res.refusal is not None
    assert "correlate" in res.refusal.payload["detail"]


def test_set_update_refuses_without_a_distractor_state():
    # the task picklist offers no third state (only the template value and
    # the updated value) — the differential distractor is underivable, so
    # the set-update path refuses with a named reason
    core = _world_fl05(task_status_values=("Open", "Cancelled"))
    state = _state()
    res = core.resolve_intent(intent_input=_intent(), ctx=_ctx(), state=state)
    assert res.refusal is not None
    assert state.groundings == []
    assert "distractor" in res.refusal.payload["detail"].lower()
