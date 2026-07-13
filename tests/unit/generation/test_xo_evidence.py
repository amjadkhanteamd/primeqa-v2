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


# ---------------------------------------------------------------------------
# E3 — roll-up evidence (FL07: order totals reflect the sum of its lines)
# ---------------------------------------------------------------------------

FL07_FIXTURE = os.path.join(os.path.dirname(__file__), "..", "semantic",
                            "fixtures", "pls_fb_flows",
                            "PLS_FB_FL07_Order_Rollup.json")


def _world_fl07(*, line_total_type="currency", with_flow=True):
    order = _ent("Object", "PLS_FB_Order__c", "PLS FB Order")
    o_total = _ent("Field", "PLS_FB_Order__c.PLS_FB_Order_Total__c",
                   "Order Total")
    o_count = _ent("Field", "PLS_FB_Order__c.PLS_FB_Line_Count__c",
                   "Line Count")
    line = _ent("Object", "PLS_FB_Order_Line__c", "PLS FB Order Line")
    l_order = _ent("Field", "PLS_FB_Order_Line__c.PLS_FB_Order__c", "Order")
    l_total = _ent("Field", "PLS_FB_Order_Line__c.PLS_FB_Line_Total__c",
                   "Line Total")
    with open(FL07_FIXTURE) as f:
        d = json.load(f)
    flow = _ent("Flow", "PLS_FB_FL07_Order_Rollup", "Order Rollup",
                attrs={"Metadata": d["Metadata"]})
    entities = [order, o_total, o_count, line, l_order, l_total]
    if with_flow:
        entities.append(flow)
    s1 = _FakeS1(
        entities=entities,
        rows_by_object={
            "PLS_FB_Order__c": [
                SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=e)
                for e in (o_total, o_count)],
            "PLS_FB_Order_Line__c": [
                SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=e)
                for e in (l_order, l_total)],
        },
        details={l_total.id: {"field_type": line_total_type}})
    return gc.GovernanceCore(s1)


ROLLUP_EXCERPT = ("the order total and line count always reflect the "
                  "order's lines")


def _rollup_intent(field, **kw):
    d = {"ac_ref": 11, "archetype_hint": "data_behavior",
         "polarity_hint": "positive",
         "claim_kind_hint": "automation-effect-claim",
         "requirement_excerpt": ROLLUP_EXCERPT,
         "target_subject_hint": {
             "entity_type": "Object", "sf_api_name": "PLS_FB_Order__c",
             "field_name": field, **kw}}
    return {"requirement_excerpt": ROLLUP_EXCERPT, "intent_descriptor": d}


def test_rollup_sum_grounds_with_derived_expectation():
    core = _world_fl07()
    state = _state()
    res = core.resolve_intent(
        intent_input=_rollup_intent("PLS_FB_Order__c.PLS_FB_Order_Total__c"),
        ctx=_ctx(), state=state)
    assert res.refusal is None, getattr(res.refusal, "payload", None)
    [g] = state.groundings
    assert g.automation.external_id == "PLS_FB_FL07_Order_Rollup"
    rs = g.rollup_spec
    assert rs["child_object"] == "PLS_FB_Order_Line__c"
    assert rs["lookup"] == "PLS_FB_Order__c"
    assert rs["fn"] == "Sum"
    assert rs["count"] == 2
    assert dict(rs["staged"]) == {"PLS_FB_Line_Total__c": 137}
    assert rs["expected"] == 274                    # 2 × the staged constant
    assert g.effect_value == 274


def test_rollup_count_grounds_without_staged_source():
    core = _world_fl07()
    state = _state()
    res = core.resolve_intent(
        intent_input=_rollup_intent("PLS_FB_Order__c.PLS_FB_Line_Count__c"),
        ctx=_ctx(), state=state)
    assert res.refusal is None, getattr(res.refusal, "payload", None)
    [g] = state.groundings
    rs = g.rollup_spec
    assert rs["fn"] == "Count"
    assert rs["staged"] == ()                       # nothing to stage per row
    assert rs["expected"] == 2 and g.effect_value == 2


def test_rollup_emission_authors_the_two_parent_shape():
    from primeqa.generation.emission import author_emission
    core = _world_fl07()
    state = _state()
    core.resolve_intent(intent_input=_rollup_intent("PLS_FB_Order__c.PLS_FB_Order_Total__c"),
                        ctx=_ctx(), state=state)
    [g] = state.groundings
    bundle = author_emission(g)
    steps = bundle.observation_realization.steps
    kinds = [type(s).__name__ for s in steps]
    # parent + 2 children + parent-2 + distractor child + read + assert
    assert kinds == ["CreateStep", "CreateStep", "CreateStep", "CreateStep",
                     "CreateStep", "ReadStep", "AssertStep"]
    assert steps[1].field_values[
        "PLS_FB_Order_Line__c.PLS_FB_Order__c"] == "$create-record.id"
    assert steps[4].field_values[
        "PLS_FB_Order_Line__c.PLS_FB_Order__c"] == "$create-parent-2.id"
    assert "WHERE Id = '$create-record.id'" in steps[5].soql
    assert steps[6].predicate.predicate == "equals"
    assert steps[6].predicate.value == 274
    # deterministic identity
    from primeqa.test_representation.identity_hash import compute_identity_hash
    b2 = author_emission(g)
    assert compute_identity_hash(bundle.archetype, bundle.claim_kind,
                                 bundle.asserted_truth,
                                 bundle.semantic_conditions) == \
        compute_identity_hash(b2.archetype, b2.claim_kind,
                              b2.asserted_truth, b2.semantic_conditions)


def test_rollup_refuses_value_ful_and_nonnumeric_source():
    # a proposed expected_value can never verifiably match an aggregate the
    # EVIDENCE parameterizes — the intent refuses (value-less is the shape)
    core = _world_fl07()
    res = core.resolve_intent(
        intent_input=_rollup_intent("PLS_FB_Order__c.PLS_FB_Order_Total__c",
                                    expected_value="500"),
        ctx=_ctx(), state=_state())
    assert res.refusal is not None
    # a text-typed source field cannot take a staged numeric per-row value
    core2 = _world_fl07(line_total_type="text")
    res2 = core2.resolve_intent(
        intent_input=_rollup_intent("PLS_FB_Order__c.PLS_FB_Order_Total__c"),
        ctx=_ctx(), state=_state())
    assert res2.refusal is not None
    assert "numeric" in res2.refusal.payload["detail"]


def test_rollup_absent_flow_still_refuses():
    core = _world_fl07(with_flow=False)
    res = core.resolve_intent(
        intent_input=_rollup_intent("PLS_FB_Order__c.PLS_FB_Order_Total__c"),
        ctx=_ctx(), state=_state())
    assert res.refusal is not None


# ---------------------------------------------------------------------------
# Composition — FL12→SF01: the caller's subflow closes the open tasks
# (collection-update idiom composed into the caller frame, E2 evidence)
# ---------------------------------------------------------------------------

FL12_FIXTURE = os.path.join(os.path.dirname(__file__), "..", "semantic",
                            "fixtures", "pls_fb_flows",
                            "PLS_FB_FL12_Fulfilment_Orchestrator.json")
SF01_FIXTURE = os.path.join(os.path.dirname(__file__), "..", "semantic",
                            "fixtures", "pls_fb_flows",
                            "PLS_FB_SF01_Close_Tasks.json")


def _world_fl12():
    order = _ent("Object", "PLS_FB_Order__c", "PLS FB Order")
    status = _ent("Field", "PLS_FB_Order__c.PLS_FB_Status__c", "Status",
                  attrs={"data_type": "Picklist"})
    task = _ent("Object", "PLS_FB_Fulfilment_Task__c", "PLS FB Fulfilment Task")
    t_order = _ent("Field", "PLS_FB_Fulfilment_Task__c.PLS_FB_Order__c",
                   "Order")
    t_status = _ent("Field", "PLS_FB_Fulfilment_Task__c.PLS_FB_Status__c",
                    "Status")
    with open(FL12_FIXTURE) as f:
        d12 = json.load(f)
    with open(SF01_FIXTURE) as f:
        dsf = json.load(f)
    fl12 = _ent("Flow", "PLS_FB_FL12_Fulfilment_Orchestrator",
                "Fulfilment Orchestrator", attrs={"Metadata": d12["Metadata"]})
    # SF01 is autolaunched: reachable ONLY via the org-wide Flow read,
    # never via any TRIGGERS_ON neighborhood
    sf01 = _ent("Flow", "PLS_FB_SF01_Close_Tasks", "Close Tasks",
                attrs={"Metadata": dsf["Metadata"]})
    pvs_o, pvs_t = uuid4(), uuid4()
    s1 = _FakeS1(
        entities=[order, status, task, t_order, t_status, fl12, sf01],
        rows_by_object={
            "PLS_FB_Order__c": [
                SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=status),
                SimpleNamespace(edge_type=gc.EDGE_FLOW, entity=fl12)],
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
                    for v in ("Draft", "Submitted", "Fulfilled")],
            pvs_t: [{"value_api_name": v, "is_active": True}
                    for v in ("Open", "Completed", "Cancelled")]})
    return gc.GovernanceCore(s1)


FL12_EXCERPT = ("when an order is fulfilled, its open fulfilment tasks are "
                "closed out")


def _fl12_intent(**kw):
    d = {"ac_ref": 13, "archetype_hint": "data_behavior",
         "polarity_hint": "positive",
         "claim_kind_hint": "automation-effect-claim",
         "requirement_excerpt": FL12_EXCERPT,
         "target_subject_hint": {
             "entity_type": "Object", "sf_api_name": "PLS_FB_Order__c",
             "effect_object": "PLS_FB_Fulfilment_Task__c", **kw}}
    return {"requirement_excerpt": FL12_EXCERPT, "intent_descriptor": d}


def test_composed_subflow_update_grounds_with_caller_attribution():
    core = _world_fl12()
    state = _state()
    res = core.resolve_intent(intent_input=_fl12_intent(), ctx=_ctx(),
                              state=state)
    assert res.refusal is None, getattr(res.refusal, "payload", None)
    [g] = state.groundings
    # attribution: the CALLER — the record-triggered flow the org fires
    assert g.automation.external_id == "PLS_FB_FL12_Fulfilment_Orchestrator"
    assert g.effect_lookup_field.external_id == \
        "PLS_FB_Fulfilment_Task__c.PLS_FB_Order__c"
    assert g.effect_value == "Completed"          # SF01's per-item literal
    pc = g.premise_children
    assert dict(pc["template"]) == {"PLS_FB_Status__c": "Open"}
    assert pc["distractor"] == ("PLS_FB_Status__c", "Cancelled")
    assert pc["updated_value"] == "Completed"
    # entry transition from the CALL-SITE guard (Status=Fulfilled):
    # create NOT-Fulfilled, update INTO Fulfilled
    assert _pairs(g.trigger_fields) == {"PLS_FB_Status__c": "Draft"}
    assert _pairs(g.update_trigger_fields) == \
        {"PLS_FB_Status__c": "Fulfilled"}


def test_composed_grounding_is_deterministic():
    outs = []
    for _ in range(3):
        core = _world_fl12()
        state = _state()
        core.resolve_intent(intent_input=_fl12_intent(), ctx=_ctx(),
                            state=state)
        [g] = state.groundings
        outs.append((g.automation.external_id, g.effect_value,
                     tuple(sorted(_pairs(g.trigger_fields).items())),
                     g.premise_children["distractor"]))
    assert len(set(outs)) == 1


def test_composed_absence_intent_still_refuses():
    # the SUB-3 law: an absence shape must never ride the provisional
    # flows[0] binding into a wrong attribution — refusal, with the
    # cannot-be-attributed detail
    core = _world_fl12()
    res = core.resolve_intent(
        intent_input=_fl12_intent(expected_absence=True, trigger_fields=[
            {"field_name": "PLS_FB_Order__c.PLS_FB_Status__c",
             "value": "Draft"}]),
        ctx=_ctx(), state=_state())
    assert res.refusal is not None
    assert "cannot be attributed" in res.refusal.payload["detail"]


# ---------------------------------------------------------------------------
# FL06 — premise-conditioned same-record effect (the duplicate-check idiom)
# ---------------------------------------------------------------------------

FL06_FIXTURE = os.path.join(os.path.dirname(__file__), "..", "semantic",
                            "fixtures", "pls_fb_flows",
                            "PLS_FB_FL06_Duplicate_Flag.json")


def _world_fl06(*, status_values=("Draft", "Submitted", "Cancelled")):
    order = _ent("Object", "PLS_FB_Order__c", "PLS FB Order")
    status = _ent("Field", "PLS_FB_Order__c.PLS_FB_Status__c", "Status")
    ext = _ent("Field", "PLS_FB_Order__c.PLS_FB_External_Ref__c",
               "External Ref")
    dup = _ent("Field", "PLS_FB_Order__c.PLS_FB_Duplicate_Flag__c",
               "Duplicate Flag")
    with open(FL06_FIXTURE) as f:
        d = json.load(f)
    flow = _ent("Flow", "PLS_FB_FL06_Duplicate_Flag", "Duplicate Flag",
                attrs={"Metadata": d["Metadata"]})
    pvs = uuid4()
    s1 = _FakeS1(
        entities=[order, status, ext, dup, flow],
        rows_by_object={
            "PLS_FB_Order__c": [
                SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=e)
                for e in (status, ext, dup)] + [
                SimpleNamespace(edge_type=gc.EDGE_FLOW, entity=flow)],
        },
        details={status.id: {"field_type": "picklist",
                             "picklist_value_set_entity_id": pvs}},
        picklists={pvs: [{"value_api_name": v, "is_active": True}
                         for v in status_values]})
    return gc.GovernanceCore(s1)


FL06_EXCERPT = ("an order with the same external reference as an existing "
                "non-cancelled order is flagged as a duplicate")


def _fl06_intent(**kw):
    d = {"ac_ref": 14, "archetype_hint": "data_behavior",
         "polarity_hint": "positive",
         "claim_kind_hint": "automation-effect-claim",
         "requirement_excerpt": FL06_EXCERPT,
         "target_subject_hint": {
             "entity_type": "Object", "sf_api_name": "PLS_FB_Order__c",
             "field_name": "PLS_FB_Order__c.PLS_FB_Duplicate_Flag__c", **kw}}
    return {"requirement_excerpt": FL06_EXCERPT, "intent_descriptor": d}


def test_premise_conditioned_flag_grounds_with_sibling_staging():
    core = _world_fl06()
    state = _state()
    res = core.resolve_intent(intent_input=_fl06_intent(), ctx=_ctx(),
                              state=state)
    assert res.refusal is None, getattr(res.refusal, "payload", None)
    [g] = state.groundings
    assert g.automation.external_id == "PLS_FB_FL06_Duplicate_Flag"
    assert g.effect_value is True                     # the arm's value
    ps = g.premise_sibling
    # sibling: NotEqualTo Cancelled -> first alternative (Draft) + the
    # correlation witness on External_Ref
    assert dict(ps["staged"]) == {"PLS_FB_Status__c": "Draft",
                                  "PLS_FB_External_Ref__c": "PQAW137X"}
    assert ps["correlation"] == ("PLS_FB_External_Ref__c",
                                 "PLS_FB_External_Ref__c", "PQAW137X")
    # the subject create carries the SAME witness (the correlation)
    assert _pairs(g.trigger_fields) == {"PLS_FB_External_Ref__c": "PQAW137X"}


def test_premise_conditioned_emission_creates_sibling_first():
    from primeqa.generation.emission import author_emission
    core = _world_fl06()
    state = _state()
    core.resolve_intent(intent_input=_fl06_intent(expected_value=True),
                        ctx=_ctx(), state=state)
    [g] = state.groundings
    bundle = author_emission(g)
    steps = bundle.observation_realization.steps
    kinds = [type(s).__name__ for s in steps]
    assert kinds == ["CreateStep", "CreateStep", "ReadStep", "AssertStep"]
    assert steps[0].step_id == "create-sibling"
    assert steps[0].field_values[
        "PLS_FB_Order__c.PLS_FB_External_Ref__c"] == "PQAW137X"
    assert steps[1].field_values[
        "PLS_FB_Order__c.PLS_FB_External_Ref__c"] == "PQAW137X"
    assert steps[3].predicate.predicate == "equals"
    assert steps[3].predicate.value is True


def test_premise_conditioned_refuses_without_a_template_alternative():
    # the Status picklist offers ONLY the excluded state — the sibling
    # cannot be staged to match the premise; named refusal
    core = _world_fl06(status_values=("Cancelled",))
    res = core.resolve_intent(intent_input=_fl06_intent(), ctx=_ctx(),
                              state=_state())
    assert res.refusal is not None
    assert "no alternative state" in res.refusal.payload["detail"]


def test_premise_conditioned_wrong_value_refuses():
    # the arm writes True; a claim of False matches no producer
    core = _world_fl06()
    res = core.resolve_intent(intent_input=_fl06_intent(expected_value=False),
                              ctx=_ctx(), state=_state())
    assert res.refusal is not None
