"""C4 — temporal grounding end-to-end through ``resolve_intent`` (no PG,
no LLM).

A VALUE-LESS same-record intent on a field exactly one Flow verifiably
stamps with RUN_DATE ± offset_days grounds as the TRANSITION shape: the
substrate derives the symbolic RelativeDate expected value AND both sides
of the transition (create stages a picklist alternative that does NOT meet
the entry filter; the update stages the filter value — newly meeting it,
which is what ``doesRequireRecordChangedToMeetCriteria`` promises). The
real FL08 fixture Metadata drives the flow entity for fidelity."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from uuid import uuid4

from primeqa.generation import governance_core as gc
from primeqa.test_representation.temporal import relative_date

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "semantic",
                       "fixtures", "pls_fb_flows",
                       "PLS_FB_FL08_SLA_Stamp.json")


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


def _fl08_entity(api="PLS_FB_FL08_SLA_Stamp"):
    with open(FIXTURE) as f:
        d = json.load(f)
    md = d.get("Metadata", d)
    return _ent("Flow", api, "SLA Stamp", attrs={"Metadata": md})


def _order_world(*, flows=None, status_values=("Draft", "Submitted",
                                               "Confirmed")):
    order = _ent("Object", "PLS_FB_Order__c", "PLS FB Order")
    sla = _ent("Field", "PLS_FB_Order__c.PLS_FB_SLA_Deadline__c",
               "SLA Deadline", attrs={"data_type": "Date"})
    status = _ent("Field", "PLS_FB_Order__c.PLS_FB_Status__c", "Status",
                  attrs={"data_type": "Picklist"})
    flows = [_fl08_entity()] if flows is None else flows
    rows = [SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=sla),
            SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=status)]
    rows += [SimpleNamespace(edge_type=gc.EDGE_FLOW, entity=f) for f in flows]
    pvs_id = uuid4()
    details = {sla.id: {"field_type": "date"},
               status.id: {"field_type": "picklist",
                           "picklist_value_set_entity_id": pvs_id}}
    picklists = {pvs_id: [{"value_api_name": v, "is_active": True}
                          for v in status_values]}
    s1 = _FakeS1(entities=[order, sla, status] + list(flows),
                 rows_by_object={"PLS_FB_Order__c": rows},
                 details=details, picklists=picklists)
    return gc.GovernanceCore(s1)


def _state():
    return SimpleNamespace(control_facts=None, groundings=[])


EXCERPT = ("submitting an order records a service-level deadline based on "
           "submission date")


def _intent(**kw):
    d = {"ac_ref": 8, "archetype_hint": "data_behavior",
         "polarity_hint": "positive",
         "claim_kind_hint": "automation-effect-claim",
         "requirement_excerpt": EXCERPT,
         "target_subject_hint": {
             "entity_type": "Object", "sf_api_name": "PLS_FB_Order__c",
             "field_name": "PLS_FB_Order__c.PLS_FB_SLA_Deadline__c", **kw}}
    return {"requirement_excerpt": EXCERPT, "intent_descriptor": d}


def _pairs(tf):
    return {ep.external_id.rsplit(".", 1)[-1]: v for ep, v in tf}


# ---------------------------------------------------------------------------
# the grounded path — substrate-derived transition + symbolic expected
# ---------------------------------------------------------------------------

def test_valueless_temporal_intent_grounds_the_transition_shape():
    core = _order_world()
    state = _state()
    res = core.resolve_intent(intent_input=_intent(), ctx=_ctx(), state=state)
    assert res.refusal is None, getattr(res.refusal, "payload", None)
    [g] = state.groundings
    assert g.automation.external_id == "PLS_FB_FL08_SLA_Stamp"
    assert g.effect_value == relative_date(5)          # the flow's own offset
    assert g.automation_primitive == "flow"
    # create must NOT meet the filter (first active alternative)…
    assert _pairs(g.trigger_fields) == {"PLS_FB_Status__c": "Draft"}
    # …the update newly meets it
    assert _pairs(g.update_trigger_fields) == {"PLS_FB_Status__c": "Submitted"}


def test_substrate_transition_replaces_model_pairs():
    core = _order_world()
    state = _state()
    res = core.resolve_intent(
        intent_input=_intent(
            trigger_fields=[{"field_name":
                             "PLS_FB_Order__c.PLS_FB_Status__c",
                             "value": "Confirmed"}],
            update_trigger_fields=[{"field_name":
                                    "PLS_FB_Order__c.PLS_FB_Status__c",
                                    "value": "Submitted"}]),
        ctx=_ctx(), state=state)
    assert res.refusal is None
    [g] = state.groundings
    assert _pairs(g.trigger_fields) == {"PLS_FB_Status__c": "Draft"}
    assert _pairs(g.update_trigger_fields) == {"PLS_FB_Status__c": "Submitted"}


def test_deterministic_across_repeats():
    outs = []
    for _ in range(3):
        core = _order_world()
        state = _state()
        core.resolve_intent(intent_input=_intent(), ctx=_ctx(), state=state)
        [g] = state.groundings
        outs.append((json.dumps(g.effect_value, sort_keys=True),
                     tuple(sorted(_pairs(g.trigger_fields).items())),
                     tuple(sorted(_pairs(g.update_trigger_fields).items()))))
    assert len(set(outs)) == 1


# ---------------------------------------------------------------------------
# honesty boundaries
# ---------------------------------------------------------------------------

def _detail(res):
    assert res.refusal is not None, "expected a refusal"
    return res.refusal.payload.get("detail", "")


def test_two_temporal_producers_refuse():
    core = _order_world(flows=[_fl08_entity(),
                               _fl08_entity("PLS_FB_FL98_Other_Stamp")])
    res = core.resolve_intent(intent_input=_intent(), ctx=_ctx(),
                              state=_state())
    assert "2 Flows verifiably stamp" in _detail(res)


def test_no_picklist_alternative_refuses():
    core = _order_world(status_values=("Submitted",))
    res = core.resolve_intent(intent_input=_intent(), ctx=_ctx(),
                              state=_state())
    assert "alternative active picklist value" in _detail(res)


def test_no_temporal_producer_keeps_the_valueless_refusal():
    core = _order_world(flows=[])
    res = core.resolve_intent(intent_input=_intent(), ctx=_ctx(),
                              state=_state())
    assert res.refusal is not None   # the pre-C4 honest refusal survives
