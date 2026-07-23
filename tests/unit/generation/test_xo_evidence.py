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
# B0 recovery on the effect ENDPOINT — the live req-320 miss ('Order__c' for
# 'PLS_FB_Order__c'). The offer must travel STRUCTURED (payload), not only as
# prose, or the D-340 re-prompt has nothing to follow.
# ---------------------------------------------------------------------------

def _offer(res) -> dict:
    return res.refusal.payload.get("candidates")


def test_effect_lookup_field_miss_offers_the_effect_objects_own_fields():
    core = _world()
    res = core.resolve_intent(
        intent_input=_intent(effect_lookup_field="Order__c"),
        ctx=_ctx(), state=_state())
    assert res.refusal is not None
    assert "cannot correlate the effect record" in res.refusal.payload["detail"]
    offer = _offer(res)
    assert offer["entity_type"] == "Field"      # never an automation name
    assert offer["proposed"] == "Order__c"      # re-proposable by the model
    assert offer["source"] == "substrate"
    # the pool is the EFFECT object's BELONGS_TO fields, not the subject's
    assert offer["candidates"][0]["sf_api_name"] == \
        "PLS_FB_Fulfilment_Task__c.PLS_FB_Order__c"
    assert all(c["sf_api_name"].startswith("PLS_FB_Fulfilment_Task__c.")
               for c in offer["candidates"])


def test_effect_field_miss_offers_candidates_and_still_refuses():
    core = _world()
    res = core.resolve_intent(
        intent_input=_intent(effect_field="Statuz__c"),
        ctx=_ctx(), state=_state())
    assert res.refusal is not None
    assert "does not exist on PLS_FB_Fulfilment_Task__c" in \
        res.refusal.payload["detail"]
    offer = _offer(res)
    assert offer["entity_type"] == "Field"
    assert offer["candidates"][0]["sf_api_name"] == \
        "PLS_FB_Fulfilment_Task__c.PLS_FB_Status__c"


def test_effect_endpoint_offer_never_substitutes_silently():
    # B0 law: alternatives, never conclusions — a miss stays a REFUSAL with
    # zero groundings even though the substrate knows the near-miss.
    core = _world()
    state = _state()
    res = core.resolve_intent(
        intent_input=_intent(effect_lookup_field="Order__c"),
        ctx=_ctx(), state=state)
    assert res.next_action == gc.NextAction.REFUSE
    assert state.groundings == []


def test_unrecognizable_effect_endpoint_yields_no_offer():
    # below the similarity bar → NOT a directory listing of the effect object
    core = _world()
    res = core.resolve_intent(
        intent_input=_intent(effect_lookup_field="Zzqqxx__c"),
        ctx=_ctx(), state=_state())
    assert res.refusal is not None
    assert _offer(res) is None


def test_effect_via_lookup_field_miss_offers_the_subjects_own_fields():
    # the D-227 parent-stamp sibling: the via-lookup lives on the SUBJECT,
    # so its offer pool is the subject's fields — not the effect object's
    core = _world()
    res = core.resolve_intent(
        intent_input=_intent(effect_via_lookup_field="Statuz__c",
                             effect_field="PLS_FB_Status__c"),
        ctx=_ctx(), state=_state())
    assert res.refusal is not None
    assert "cannot link the trigger record" in res.refusal.payload["detail"]
    offer = _offer(res)
    assert offer["entity_type"] == "Field"
    assert offer["proposed"] == "Statuz__c"
    assert offer["candidates"][0]["sf_api_name"] == \
        "PLS_FB_Order__c.PLS_FB_Status__c"
    assert all(c["sf_api_name"].startswith("PLS_FB_Order__c.")
               for c in offer["candidates"])


def test_effect_endpoint_offer_is_deterministic():
    tops = set()
    for _ in range(3):
        res = _world().resolve_intent(
            intent_input=_intent(effect_lookup_field="Order__c"),
            ctx=_ctx(), state=_state())
        tops.add(_offer(res)["candidates"][0]["sf_api_name"])
    assert len(tops) == 1


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


# ---------------------------------------------------------------------------
# Fault-path honesty — a fault-handler create is never a main-path producer
# ---------------------------------------------------------------------------

FL13_FIXTURE = os.path.join(os.path.dirname(__file__), "..", "semantic",
                            "fixtures", "pls_fb_flows",
                            "PLS_FB_FL13_Fault_Logged_Ledger.json")


def test_fault_handler_create_never_grounds_as_main_path_producer():
    from primeqa.generation.governance_core import _xo_create_producers
    with open(FL13_FIXTURE) as f:
        d = json.load(f)
    flow = _ent("Flow", "PLS_FB_FL13_Fault_Logged_Ledger", "Fault Ledger",
                attrs={"Metadata": d["Metadata"]})
    # the MAIN-path ledger create is a producer; the on-fault audit-log
    # create is NOT (it cannot be provoked deterministically)
    assert len(_xo_create_producers([flow], "PLS_FB_Ledger_Entry__c")) == 1
    assert _xo_create_producers([flow], "PLS_FB_Audit_Log__c") == []


# ---------------------------------------------------------------------------
# C9 — bounded-eventual observation (FL11: async enrichment log)
# ---------------------------------------------------------------------------

FL11_FIXTURE = os.path.join(os.path.dirname(__file__), "..", "semantic",
                            "fixtures", "pls_fb_flows",
                            "PLS_FB_FL11_Async_Enrichment.json")


def _world_fl11():
    order = _ent("Object", "PLS_FB_Order__c", "PLS FB Order")
    status = _ent("Field", "PLS_FB_Order__c.PLS_FB_Status__c", "Status")
    log = _ent("Object", "PLS_FB_Audit_Log__c", "PLS FB Audit Log")
    l_order = _ent("Field", "PLS_FB_Audit_Log__c.PLS_FB_Order__c", "Order")
    l_kind = _ent("Field", "PLS_FB_Audit_Log__c.PLS_FB_Kind__c", "Kind")
    with open(FL11_FIXTURE) as f:
        d = json.load(f)
    flow = _ent("Flow", "PLS_FB_FL11_Async_Enrichment", "Async Enrichment",
                attrs={"Metadata": d["Metadata"]})
    pvs = uuid4()
    s1 = _FakeS1(
        entities=[order, status, log, l_order, l_kind, flow],
        rows_by_object={
            "PLS_FB_Order__c": [
                SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=status),
                SimpleNamespace(edge_type=gc.EDGE_FLOW, entity=flow)],
            "PLS_FB_Audit_Log__c": [
                SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=e)
                for e in (l_order, l_kind)],
        },
        details={status.id: {"field_type": "picklist",
                             "picklist_value_set_entity_id": pvs}},
        picklists={pvs: [{"value_api_name": v, "is_active": True}
                         for v in ("Draft", "Submitted", "Confirmed")]})
    return gc.GovernanceCore(s1)


FL11_EXCERPT = ("shortly after an order is confirmed, an enrichment audit "
                "log entry appears for it")


def _fl11_intent(**kw):
    d = {"ac_ref": 15, "archetype_hint": "data_behavior",
         "polarity_hint": "positive",
         "claim_kind_hint": "automation-effect-claim",
         "requirement_excerpt": FL11_EXCERPT,
         "target_subject_hint": {
             "entity_type": "Object", "sf_api_name": "PLS_FB_Order__c",
             "effect_object": "PLS_FB_Audit_Log__c", **kw}}
    return {"requirement_excerpt": FL11_EXCERPT, "intent_descriptor": d}


def test_async_create_grounds_with_the_eventual_read():
    core = _world_fl11()
    state = _state()
    res = core.resolve_intent(intent_input=_fl11_intent(), ctx=_ctx(),
                              state=state)
    assert res.refusal is None, getattr(res.refusal, "payload", None)
    [g] = state.groundings
    assert g.automation.external_id == "PLS_FB_FL11_Async_Enrichment"
    assert g.effect_lookup_field.external_id == \
        "PLS_FB_Audit_Log__c.PLS_FB_Order__c"          # substrate-derived
    assert g.eventual_read == {"timeout_s": 120, "poll_s": 5,
                               "reason": "async_after_commit"}
    # entry transition from the async op's guard (Update trigger)
    assert _pairs(g.trigger_fields) == {"PLS_FB_Status__c": "Draft"}
    assert _pairs(g.update_trigger_fields) == \
        {"PLS_FB_Status__c": "Confirmed"}


def test_async_emission_marks_the_read_and_the_narration():
    from primeqa.generation.emission import author_emission
    core = _world_fl11()
    state = _state()
    core.resolve_intent(intent_input=_fl11_intent(
        effect_field="PLS_FB_Kind__c"), ctx=_ctx(), state=state)
    [g] = state.groundings
    assert g.effect_value == "AsyncEnrichment"          # the op's literal
    bundle = author_emission(g)
    read = next(s for s in bundle.observation_realization.steps
                if type(s).__name__ == "ReadStep")
    assert read.eventual == {"timeout_s": 120, "poll_s": 5,
                             "reason": "async_after_commit"}
    assert "asynchronous" in \
        bundle.asserted_truth.triggering_action.description
    # deterministic identity, and DISTINCT from an immediate claim's shape
    from primeqa.test_representation.identity_hash import compute_identity_hash
    b2 = author_emission(g)
    assert compute_identity_hash(bundle.archetype, bundle.claim_kind,
                                 bundle.asserted_truth,
                                 bundle.semantic_conditions) == \
        compute_identity_hash(b2.archetype, b2.claim_kind,
                              b2.asserted_truth, b2.semantic_conditions)


def test_async_absence_refuses_by_name():
    core = _world_fl11()
    res = core.resolve_intent(
        intent_input=_fl11_intent(expected_absence=True,
                                  effect_lookup_field="PLS_FB_Order__c"),
        ctx=_ctx(), state=_state())
    assert res.refusal is not None
    assert "not provable" in res.refusal.payload["detail"]


def test_async_plan_bridge_carries_the_eventual_spec():
    from primeqa.generation.emission import author_emission
    core = _world_fl11()
    state = _state()
    core.resolve_intent(intent_input=_fl11_intent(), ctx=_ctx(), state=state)
    [g] = state.groundings
    bundle = author_emission(g)
    from primeqa.execution_engine.bridge import build_data_recipe_plan
    from types import SimpleNamespace as NS
    rr = NS(recipe_id=uuid4(), version_seq=1, claim_test_id=uuid4(),
            claim_version_seq=None, recipe_kind="data-recipe",
            trigger_kind="data-mutation-trigger",
            causal_initiation=bundle.causal_initiation,
            observation_realization=bundle.observation_realization,
            execution_environment=bundle.execution_environment)
    plan = build_data_recipe_plan(rr)
    read = next(s for s in plan.steps if s.kind == "read")
    assert read.eventual == {"timeout_s": 120, "poll_s": 5,
                             "reason": "async_after_commit"}


# ---------------------------------------------------------------------------
# Completion review (live env-59 finding): the MULTI-PRODUCER world — three
# real flows create PLS_FB_Audit_Log__c (FL09 immediate / FL11 async /
# FL13 on-fault). Isolated single-flow worlds hid two defects:
#   (1) the E1 rebind silently OVERRODE an explicitly named automation
#       (named FL11 -> grounded FL09, async marker dropped);
#   (2) a bare cross-object intent refused as ambiguous with no way for the
#       model to disambiguate (it may never name automations, D-318).
# ---------------------------------------------------------------------------

FL09_FIXTURE = os.path.join(os.path.dirname(__file__), "..", "semantic",
                            "fixtures", "pls_fb_flows",
                            "PLS_FB_FL09_Reopen_Guard.json")


def _world_multi():
    """Order + Audit_Log with FL09 (immediate create), FL11 (async create)
    and FL13 (on-fault create) ALL producing the same effect object."""
    order = _ent("Object", "PLS_FB_Order__c", "PLS FB Order")
    status = _ent("Field", "PLS_FB_Order__c.PLS_FB_Status__c", "Status")
    log = _ent("Object", "PLS_FB_Audit_Log__c", "PLS FB Audit Log")
    l_order = _ent("Field", "PLS_FB_Audit_Log__c.PLS_FB_Order__c", "Order")
    l_kind = _ent("Field", "PLS_FB_Audit_Log__c.PLS_FB_Kind__c", "Kind")
    l_detail = _ent("Field", "PLS_FB_Audit_Log__c.PLS_FB_Detail__c", "Detail")
    ledger = _ent("Object", "PLS_FB_Ledger_Entry__c", "Ledger Entry")
    flows = []
    for api, path in (("PLS_FB_FL09_Reopen_Guard", FL09_FIXTURE),
                      ("PLS_FB_FL11_Async_Enrichment", FL11_FIXTURE),
                      ("PLS_FB_FL13_Fault_Logged_Ledger", FL13_FIXTURE)):
        with open(path) as f:
            flows.append(_ent("Flow", api, api,
                              attrs={"Metadata": json.load(f)["Metadata"]}))
    pvs = uuid4()
    s1 = _FakeS1(
        entities=[order, status, log, l_order, l_kind, l_detail, ledger]
                 + flows,
        rows_by_object={
            "PLS_FB_Order__c": [
                SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=status)]
                + [SimpleNamespace(edge_type=gc.EDGE_FLOW, entity=f)
                   for f in flows],
            "PLS_FB_Audit_Log__c": [
                SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=e)
                for e in (l_order, l_kind, l_detail)],
        },
        details={status.id: {"field_type": "picklist",
                             "picklist_value_set_entity_id": pvs}},
        picklists={pvs: [{"value_api_name": v, "is_active": True}
                         for v in ("Draft", "Submitted", "Confirmed",
                                   "Fulfilled")]})
    return gc.GovernanceCore(s1)


def _log_intent(**kw):
    ex = "an audit log entry records what happened to the order"
    d = {"ac_ref": 20, "archetype_hint": "data_behavior",
         "polarity_hint": "positive",
         "claim_kind_hint": "automation-effect-claim",
         "requirement_excerpt": ex,
         "target_subject_hint": {
             "entity_type": "Object", "sf_api_name": "PLS_FB_Order__c",
             "effect_object": "PLS_FB_Audit_Log__c", **kw}}
    return {"requirement_excerpt": ex, "intent_descriptor": d}


def test_named_automation_is_never_silently_overridden():
    # THE LIVE BUG (env-59): naming FL11 grounded FL09 with eventual dropped.
    core = _world_multi()
    state = _state()
    res = core.resolve_intent(
        intent_input=_log_intent(
            automation_name="PLS_FB_FL11_Async_Enrichment"),
        ctx=_ctx(), state=state)
    assert res.refusal is None, getattr(res.refusal, "payload", None)
    [g] = state.groundings
    assert g.automation.external_id == "PLS_FB_FL11_Async_Enrichment"
    assert g.eventual_read is not None          # the async path is preserved


def test_named_immediate_producer_binds_itself_without_eventual():
    core = _world_multi()
    state = _state()
    res = core.resolve_intent(
        intent_input=_log_intent(automation_name="PLS_FB_FL09_Reopen_Guard"),
        ctx=_ctx(), state=state)
    assert res.refusal is None, getattr(res.refusal, "payload", None)
    [g] = state.groundings
    assert g.automation.external_id == "PLS_FB_FL09_Reopen_Guard"
    assert g.eventual_read is None


def test_effect_field_value_disambiguates_the_async_producer():
    # the model may NOT name automations (D-318) — but it CAN name the
    # effect field/value, and that picks the producer deterministically
    core = _world_multi()
    state = _state()
    res = core.resolve_intent(
        intent_input=_log_intent(effect_field="PLS_FB_Kind__c",
                                 effect_value="AsyncEnrichment"),
        ctx=_ctx(), state=state)
    assert res.refusal is None, getattr(res.refusal, "payload", None)
    [g] = state.groundings
    assert g.automation.external_id == "PLS_FB_FL11_Async_Enrichment"
    assert g.eventual_read is not None
    assert g.effect_value == "AsyncEnrichment"


def test_effect_field_value_disambiguates_the_immediate_producer():
    core = _world_multi()
    state = _state()
    res = core.resolve_intent(
        intent_input=_log_intent(effect_field="PLS_FB_Kind__c",
                                 effect_value="Reopen"),
        ctx=_ctx(), state=state)
    assert res.refusal is None, getattr(res.refusal, "payload", None)
    [g] = state.groundings
    assert g.automation.external_id == "PLS_FB_FL09_Reopen_Guard"
    assert g.eventual_read is None


def test_bare_ambiguous_intent_refuses_disclosing_the_discriminator():
    core = _world_multi()
    res = core.resolve_intent(intent_input=_log_intent(), ctx=_ctx(),
                              state=_state())
    assert res.refusal is not None
    d = res.refusal.payload["detail"]
    # the disclosure is FIELD-based (never automation names — D-318/B0)
    assert "PLS_FB_Kind__c" in d
    assert "AsyncEnrichment" in d and "Reopen" in d
    assert "FL09" not in d and "FL11" not in d


def test_fault_producer_is_not_offered_as_a_discriminator_choice():
    # FL13's create is on a FAULT path — never a main-path producer, so it
    # is neither a candidate nor a disclosed alternative
    core = _world_multi()
    res = core.resolve_intent(intent_input=_log_intent(), ctx=_ctx(),
                              state=_state())
    assert "LedgerFault" not in res.refusal.payload["detail"]


def _world_task_multi():
    """env-59's REAL shape for PLS_FB_Fulfilment_Task__c: FL04 CREATES it
    (Status='Open') and FL05 UPDATES it (Status='Cancelled'). The isolated
    worlds hid this — a Status='Cancelled' claim bound FL04 (whose create
    sets 'Open'), a wrong attribution that would fail as a false red."""
    order = _ent("Object", "PLS_FB_Order__c", "PLS FB Order")
    status = _ent("Field", "PLS_FB_Order__c.PLS_FB_Status__c", "Status")
    task = _ent("Object", "PLS_FB_Fulfilment_Task__c", "Task")
    t_order = _ent("Field", "PLS_FB_Fulfilment_Task__c.PLS_FB_Order__c", "Order")
    t_status = _ent("Field", "PLS_FB_Fulfilment_Task__c.PLS_FB_Status__c",
                    "Status")
    t_type = _ent("Field", "PLS_FB_Fulfilment_Task__c.PLS_FB_Type__c", "Type")
    t_due = _ent("Field", "PLS_FB_Fulfilment_Task__c.PLS_FB_Due_Date__c", "Due")
    flows = []
    for api, path in (("PLS_FB_FL04_Confirmation_Task", FIXTURE),
                      ("PLS_FB_FL05_Cancellation_Sync", FL05_FIXTURE)):
        with open(path) as f:
            flows.append(_ent("Flow", api, api,
                              attrs={"Metadata": json.load(f)["Metadata"]}))
    pvs_o, pvs_t = uuid4(), uuid4()
    s1 = _FakeS1(
        entities=[order, status, task, t_order, t_status, t_type, t_due] + flows,
        rows_by_object={
            "PLS_FB_Order__c": [
                SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=status)]
                + [SimpleNamespace(edge_type=gc.EDGE_FLOW, entity=f)
                   for f in flows],
            "PLS_FB_Fulfilment_Task__c": [
                SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=e)
                for e in (t_order, t_status, t_type, t_due)],
        },
        details={status.id: {"field_type": "picklist",
                             "picklist_value_set_entity_id": pvs_o},
                 t_status.id: {"field_type": "picklist",
                               "picklist_value_set_entity_id": pvs_t}},
        picklists={
            pvs_o: [{"value_api_name": v, "is_active": True}
                    for v in ("Draft", "Submitted", "Confirmed", "Cancelled")],
            pvs_t: [{"value_api_name": v, "is_active": True}
                    for v in ("Open", "Completed", "Cancelled")]})
    return gc.GovernanceCore(s1)


def test_update_producer_wins_its_own_field_value_over_the_creator():
    # THE SECOND LIVE BUG: Status='Cancelled' is FL05's update, NOT FL04's
    # create (which sets 'Open') — binding FL04 here is a wrong attribution
    core = _world_task_multi()
    state = _state()
    res = core.resolve_intent(
        intent_input=_intent(effect_field="PLS_FB_Status__c",
                             effect_value="Cancelled"),
        ctx=_ctx(), state=state)
    assert res.refusal is None, getattr(res.refusal, "payload", None)
    [g] = state.groundings
    assert g.automation.external_id == "PLS_FB_FL05_Cancellation_Sync"
    assert g.premise_children is not None       # the E2 set-update shape
    assert g.premise_children["updated_value"] == "Cancelled"


def test_creator_still_wins_its_own_field_value():
    core = _world_task_multi()
    state = _state()
    res = core.resolve_intent(
        intent_input=_intent(effect_field="PLS_FB_Status__c",
                             effect_value="Open"),
        ctx=_ctx(), state=state)
    assert res.refusal is None, getattr(res.refusal, "payload", None)
    [g] = state.groundings
    assert g.automation.external_id == "PLS_FB_FL04_Confirmation_Task"
    assert g.premise_children is None           # the E1 create shape


def test_bare_existence_intent_still_binds_the_creator_not_the_updater():
    # an update can never make a record APPEAR — a bare "a task appears"
    # claim stays a create-only question (no over-refusal from the updater)
    core = _world_task_multi()
    state = _state()
    res = core.resolve_intent(intent_input=_intent(), ctx=_ctx(), state=state)
    assert res.refusal is None, getattr(res.refusal, "payload", None)
    [g] = state.groundings
    assert g.automation.external_id == "PLS_FB_FL04_Confirmation_Task"


# ---------------------------------------------------------------------------
# D-380 — the rollup FALLBACK at the cross-object refusal gates: the model's
# natural cross-object framing for a child-set aggregate reroutes onto the
# E3 rollup resolution (stripped framing, every E3 law intact)
# ---------------------------------------------------------------------------

def _world_fl07_with_own_flow(*, rollup_flows=1):
    """Like _world_fl07 but the SUBJECT has its OWN (decoy) flow, so the
    no-name branch binds provisionally and no_producer_floor engages — the
    live Order shape (14 own flows, none producing the child effect)."""
    order = _ent("Object", "PLS_FB_Order__c", "PLS FB Order")
    o_total = _ent("Field", "PLS_FB_Order__c.PLS_FB_Order_Total__c",
                   "Order Total")
    o_count = _ent("Field", "PLS_FB_Order__c.PLS_FB_Line_Count__c",
                   "Line Count")
    line = _ent("Object", "PLS_FB_Order_Line__c", "PLS FB Order Line")
    l_order = _ent("Field", "PLS_FB_Order_Line__c.PLS_FB_Order__c", "Order")
    l_total = _ent("Field", "PLS_FB_Order_Line__c.PLS_FB_Line_Total__c",
                   "Line Total")
    with open(FIXTURE) as f:          # FL04 — creates Tasks, NOT the effect
        decoy_meta = json.load(f)
    decoy = _ent("Flow", "PLS_FB_FL04_Confirmation_Task", "Confirmation Task",
                 attrs={"Metadata": decoy_meta["Metadata"]})
    with open(FL07_FIXTURE) as f:
        d = json.load(f)
    entities = [order, o_total, o_count, line, l_order, l_total, decoy]
    for i in range(rollup_flows):
        entities.append(_ent(
            "Flow",
            "PLS_FB_FL07_Order_Rollup" if i == 0 else f"PLS_FB_FL07_Clone{i}",
            "Order Rollup", attrs={"Metadata": d["Metadata"]}))
    s1 = _FakeS1(
        entities=entities,
        rows_by_object={
            "PLS_FB_Order__c": [
                SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=e)
                for e in (o_total, o_count)] + [
                SimpleNamespace(edge_type=gc.EDGE_FLOW, entity=decoy)],
            "PLS_FB_Order_Line__c": [
                SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=e)
                for e in (l_order, l_total)],
        },
        details={l_total.id: {"field_type": "currency"}})
    return gc.GovernanceCore(s1)


def _xo_rollup_intent(field, **kw):
    """The live AC12 framing: subject Order, cross-object slots pointing at
    the child — exactly what the E3 path forbids."""
    base = {"effect_object": "PLS_FB_Order_Line__c",
            "effect_lookup_field": "PLS_FB_Order__c"}
    base.update(kw)
    return _rollup_intent(field, **base)


def test_rollup_fallback_grounds_the_cross_object_framing():
    core = _world_fl07_with_own_flow()
    state = _state()
    state.attempted_interpretation = {"candidate_paths": []}   # runtime shape
    res = core.resolve_intent(
        intent_input=_xo_rollup_intent("PLS_FB_Order__c.PLS_FB_Order_Total__c"),
        ctx=_ctx(), state=state)
    assert res.refusal is None, getattr(res.refusal, "payload", None)
    [g] = state.groundings
    assert g.automation.external_id == "PLS_FB_FL07_Order_Rollup"
    assert g.rollup_spec["fn"] == "Sum"
    # the reframe is disclosed (audit; hash-safe extra ai key)
    [reframe] = state.attempted_interpretation["rollup_reframes"]
    assert reframe["field"] == "PLS_FB_Order__c.PLS_FB_Order_Total__c"
    assert reframe["dropped_slots"] == ["effect_lookup_field", "effect_object"]


def test_rollup_fallback_covers_update_trigger_framing():
    core = _world_fl07_with_own_flow()
    state = _state()
    state.attempted_interpretation = {"candidate_paths": []}   # runtime shape
    res = core.resolve_intent(
        intent_input=_xo_rollup_intent(
            "PLS_FB_Order__c.PLS_FB_Line_Count__c",
            update_trigger_fields=[
                {"field_name": "PLS_FB_Order__c.PLS_FB_Order_Total__c",
                 "value": "10"}]),
        ctx=_ctx(), state=state)
    assert res.refusal is None, getattr(res.refusal, "payload", None)
    [g] = state.groundings
    assert g.rollup_spec["fn"] == "Count"
    [reframe] = state.attempted_interpretation["rollup_reframes"]
    assert "update_trigger_fields" in reframe["dropped_slots"]


def test_rollup_fallback_never_reroutes_absence():
    core = _world_fl07_with_own_flow()
    res = core.resolve_intent(
        intent_input=_xo_rollup_intent(
            "PLS_FB_Order__c.PLS_FB_Order_Total__c", expected_absence=True),
        ctx=_ctx(), state=_state())
    assert res.refusal is not None
    detail = str(res.refusal.payload.get("detail"))
    assert "re-propose value-less" not in detail       # no reframe hint either


def test_rollup_fallback_never_fires_with_a_staged_value():
    core = _world_fl07_with_own_flow()
    res = core.resolve_intent(
        intent_input=_xo_rollup_intent(
            "PLS_FB_Order__c.PLS_FB_Order_Total__c", expected_value="500"),
        ctx=_ctx(), state=_state())
    assert res.refusal is not None
    # ...but the reframe HINT rides the refusal (the D-340 hop can recover)
    assert "re-propose value-less" in str(res.refusal.payload.get("detail"))


def test_rollup_fallback_absent_producer_keeps_refusal_with_hint():
    core = _world_fl07_with_own_flow(rollup_flows=0)
    state = _state()
    res = core.resolve_intent(
        intent_input=_xo_rollup_intent("PLS_FB_Order__c.PLS_FB_Order_Total__c"),
        ctx=_ctx(), state=state)
    assert res.refusal is not None
    assert not getattr(state, "groundings", [])
    assert "re-propose value-less" in str(res.refusal.payload.get("detail"))


def test_rollup_fallback_propagates_the_ambiguity_refusal():
    core = _world_fl07_with_own_flow(rollup_flows=2)
    res = core.resolve_intent(
        intent_input=_xo_rollup_intent("PLS_FB_Order__c.PLS_FB_Order_Total__c"),
        ctx=_ctx(), state=_state())
    assert res.refusal is not None
    assert "roll an aggregate" in str(res.refusal.payload.get("detail"))


# ---------------------------------------------------------------------------
# Live req-320 test-run findings (2026-07-22): two wrong-red generators
# ---------------------------------------------------------------------------

def _world_fl09(*, status_values=("Draft", "Submitted", "Fulfilled")):
    """FL09's real shape: Update-trigger flow whose audit-log create is
    guarded NotEqualTo 'Fulfilled' (the LEAVING-a-state arm; prior was
    Fulfilled). The live claim bf889825 was authored create-only — a
    recipe that can never fire an Update-trigger flow."""
    order = _ent("Object", "PLS_FB_Order__c", "PLS FB Order")
    status = _ent("Field", "PLS_FB_Order__c.PLS_FB_Status__c", "Status")
    reopened = _ent("Field", "PLS_FB_Order__c.PLS_FB_Reopened__c", "Reopened")
    log = _ent("Object", "PLS_FB_Audit_Log__c", "PLS FB Audit Log")
    l_order = _ent("Field", "PLS_FB_Audit_Log__c.PLS_FB_Order__c", "Order")
    l_kind = _ent("Field", "PLS_FB_Audit_Log__c.PLS_FB_Kind__c", "Kind")
    with open(FL09_FIXTURE) as f:
        d = json.load(f)
    flow = _ent("Flow", "PLS_FB_FL09_Reopen_Guard", "Reopen Guard",
                attrs={"Metadata": d["Metadata"]})
    pvs = uuid4()
    s1 = _FakeS1(
        entities=[order, status, reopened, log, l_order, l_kind, flow],
        rows_by_object={
            "PLS_FB_Order__c": [
                SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=status),
                SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=reopened),
                SimpleNamespace(edge_type=gc.EDGE_FLOW, entity=flow)],
            "PLS_FB_Audit_Log__c": [
                SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=e)
                for e in (l_order, l_kind)],
        },
        details={status.id: {"field_type": "picklist",
                             "picklist_value_set_entity_id": pvs},
                 reopened.id: {"field_type": "boolean"}},
        picklists={pvs: [{"value_api_name": v, "is_active": True}
                         for v in status_values]})
    return gc.GovernanceCore(s1)


def _fl09_intent(**kw):
    ex = "when an order leaves the fulfilled state a reopen audit entry is recorded"
    d = {"ac_ref": 14, "archetype_hint": "data_behavior",
         "polarity_hint": "positive",
         "claim_kind_hint": "automation-effect-claim",
         "requirement_excerpt": ex,
         "target_subject_hint": {
             "entity_type": "Object", "sf_api_name": "PLS_FB_Order__c",
             "effect_object": "PLS_FB_Audit_Log__c", **kw}}
    return {"requirement_excerpt": ex, "intent_descriptor": d}


def test_notequalto_guard_derives_the_leave_state_transition():
    # THE FIX for live claim bf889825: create AT the excluded state,
    # update AWAY from it — prior==Fulfilled ∧ current!=Fulfilled for free
    core = _world_fl09()
    state = _state()
    res = core.resolve_intent(
        intent_input=_fl09_intent(effect_field="PLS_FB_Kind__c"),
        ctx=_ctx(), state=state)
    assert res.refusal is None, getattr(res.refusal, "payload", None)
    [g] = state.groundings
    assert g.automation.external_id == "PLS_FB_FL09_Reopen_Guard"
    assert g.effect_value == "Reopen"                  # the op's literal
    assert _pairs(g.trigger_fields) == {"PLS_FB_Status__c": "Fulfilled"}
    assert _pairs(g.update_trigger_fields) == {"PLS_FB_Status__c": "Draft"}


def test_update_trigger_without_derivable_transition_refuses():
    # the wrong-red generator is closed: an Update-trigger producer whose
    # transition cannot be derived (picklist offers no alternative state)
    # REFUSES — it must never author a create-only recipe
    core = _world_fl09(status_values=("Fulfilled",))
    res = core.resolve_intent(
        intent_input=_fl09_intent(effect_field="PLS_FB_Kind__c"),
        ctx=_ctx(), state=_state())
    assert res.refusal is not None
    d = res.refusal.payload["detail"]
    assert "fires on UPDATE only" in d
    assert "create-only" in d


# ---------------------------------------------------------------------------
# D-386 — the D-385 law applied to the STATE-TRANSITION claim kind (the
# residual found verifying D-385 on env-59: regen job 91 minted claim
# 78a2d330 — from_state Status='Confirmed' → Reopened=true with a
# create-only recipe; FL09 sets Reopened only on UPDATE leaving Fulfilled)
# ---------------------------------------------------------------------------

ST_EXCERPT = ("a fulfilled order moved back into an active state is marked "
              "as reopened")


def _st_intent(**kw):
    d = {"ac_ref": 15, "archetype_hint": "data_behavior",
         "polarity_hint": "positive",
         "claim_kind_hint": "state-transition-claim",
         "requirement_excerpt": ST_EXCERPT,
         "target_subject_hint": {
             "entity_type": "Object", "sf_api_name": "PLS_FB_Order__c", **kw}}
    return {"requirement_excerpt": ST_EXCERPT, "intent_descriptor": d}


def test_state_transition_derives_the_update_transition():
    # the bare shape: previously refused with the misframing "no org
    # automation produces … on create / assert as an acceptance-claim" —
    # now the FL09 transition arm derives create(prior) → update(away)
    core = _world_fl09()
    state = _state()
    res = core.resolve_intent(
        intent_input=_st_intent(field_name="PLS_FB_Reopened__c",
                                expected_value=True),
        ctx=_ctx(), state=state)
    assert res.refusal is None, getattr(res.refusal, "payload", None)
    [g] = state.groundings
    assert _pairs(g.transition_create_fields) == {
        "PLS_FB_Status__c": "Fulfilled"}
    assert _pairs(g.transition_update_fields) == {"PLS_FB_Status__c": "Draft"}
    assert g.trigger_field is None and g.trigger_value is None


def test_state_transition_derived_transition_supersedes_the_staged_pair():
    # THE FIX for live claim 78a2d330: the model staged a create-time
    # trigger pair that can never fire the Update-trigger producer — the
    # org-derived transition supersedes it (dropped, never merged)
    core = _world_fl09()
    state = _state()
    res = core.resolve_intent(
        intent_input=_st_intent(field_name="PLS_FB_Reopened__c",
                                expected_value=True,
                                trigger_field="PLS_FB_Status__c",
                                trigger_value="Submitted"),
        ctx=_ctx(), state=state)
    assert res.refusal is None, getattr(res.refusal, "payload", None)
    [g] = state.groundings
    assert g.trigger_field is None and g.trigger_value is None
    assert _pairs(g.transition_create_fields) == {
        "PLS_FB_Status__c": "Fulfilled"}
    assert _pairs(g.transition_update_fields) == {"PLS_FB_Status__c": "Draft"}


def test_state_transition_underivable_update_refuses():
    # picklist offers no alternative to Fulfilled → the update cannot be
    # staged → REFUSE with the named reason; never author create-only
    core = _world_fl09(status_values=("Fulfilled",))
    state = _state()
    res = core.resolve_intent(
        intent_input=_st_intent(field_name="PLS_FB_Reopened__c",
                                expected_value=True,
                                trigger_field="PLS_FB_Status__c",
                                trigger_value="Submitted"),
        ctx=_ctx(), state=state)
    assert res.refusal is not None
    assert not state.groundings
    d = res.refusal.payload["detail"]
    assert "UPDATE only" in d
    assert "cannot be staged" in d


def _world_fl09_raw_writer():
    """An Update-trigger writer whose arm the IR does NOT ground (raw
    recordUpdates inputAssignments idiom, behaviours=unsupported) but whose
    raw effect projection still matches (field, value) — the update-only
    producer with no derivable transition."""
    order = _ent("Object", "PLS_FB_Order__c", "PLS FB Order")
    status = _ent("Field", "PLS_FB_Order__c.PLS_FB_Status__c", "Status")
    reopened = _ent("Field", "PLS_FB_Order__c.PLS_FB_Reopened__c", "Reopened")
    md = {
        "processType": "AutoLaunchedFlow", "status": "Active",
        "start": {"object": "PLS_FB_Order__c", "recordTriggerType": "Update",
                  "triggerType": "RecordAfterSave",
                  "connector": {"targetReference": "Upd"},
                  "filters": [], "filterLogic": None},
        "recordUpdates": [{"name": "Upd", "inputReference": "$Record",
                           "inputAssignments": [{
                               "field": "PLS_FB_Reopened__c",
                               "value": {"booleanValue": True}}]}],
    }
    flow = _ent("Flow", "PLS_FB_Raw_Reopen_Writer", "Raw Reopen Writer",
                attrs={"Metadata": md, "_is_active": True})
    pvs = uuid4()
    s1 = _FakeS1(
        entities=[order, status, reopened, flow],
        rows_by_object={
            "PLS_FB_Order__c": [
                SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=status),
                SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=reopened),
                SimpleNamespace(edge_type=gc.EDGE_FLOW, entity=flow)]},
        details={status.id: {"field_type": "picklist",
                             "picklist_value_set_entity_id": pvs},
                 reopened.id: {"field_type": "boolean"}},
        picklists={pvs: [{"value_api_name": v, "is_active": True}
                         for v in ("Draft", "Submitted", "Fulfilled")]})
    return gc.GovernanceCore(s1)


def test_state_transition_update_only_producer_without_arm_refuses():
    # every verifiable producer fires on Update only and no grounded
    # transition arm exists — a create (bare OR staged) can never fire it
    core = _world_fl09_raw_writer()
    for kw in ({}, {"trigger_field": "PLS_FB_Status__c",
                    "trigger_value": "Submitted"}):
        state = _state()
        res = core.resolve_intent(
            intent_input=_st_intent(field_name="PLS_FB_Reopened__c",
                                    expected_value=True, **kw),
            ctx=_ctx(), state=state)
        assert res.refusal is not None, kw
        assert not state.groundings
        d = res.refusal.payload["detail"]
        assert "UPDATE only" in d
        assert "create-only test can never trigger it" in d


def test_sum_rollup_refuses_a_calculated_source_field():
    # THE FIX for live claim 87f86ec6: env-59's PLS_FB_Line_Total__c is a
    # formula (is_calculated, not createable) — staging it draws
    # INVALID_FIELD_FOR_INSERT_UPDATE at run time; refuse at grounding
    core = _world_fl07()
    l_total = next(e for e in core._admit._s1._entities
                   if e.sf_api_name ==
                   "PLS_FB_Order_Line__c.PLS_FB_Line_Total__c")
    core._admit._s1._details[l_total.id] = {
        "field_type": "currency", "is_calculated": True,
        "is_createable": False, "is_updateable": False}
    res = core.resolve_intent(
        intent_input=_rollup_intent("PLS_FB_Order__c.PLS_FB_Order_Total__c"),
        ctx=_ctx(), state=_state())
    assert res.refusal is not None
    assert "calculated/not writable" in res.refusal.payload["detail"]


def test_count_rollup_unaffected_by_unwritable_sum_source():
    # the Count twin stages nothing on the source field — it must keep
    # grounding even when Line_Total is a formula (live: c6c4d1e1 PASSED)
    core = _world_fl07()
    l_total = next(e for e in core._admit._s1._entities
                   if e.sf_api_name ==
                   "PLS_FB_Order_Line__c.PLS_FB_Line_Total__c")
    core._admit._s1._details[l_total.id] = {
        "field_type": "currency", "is_calculated": True,
        "is_createable": False, "is_updateable": False}
    state = _state()
    res = core.resolve_intent(
        intent_input=_rollup_intent("PLS_FB_Order__c.PLS_FB_Line_Count__c"),
        ctx=_ctx(), state=state)
    assert res.refusal is None, getattr(res.refusal, "payload", None)
    [g] = state.groundings
    assert g.rollup_spec["fn"] == "Count"
