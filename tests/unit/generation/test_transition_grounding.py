"""C5 — prior-state transition grounding end-to-end through
``resolve_intent`` (no PG, no LLM).

A value-ful intent on a field only an UPDATE-trigger flow verifiably writes
grounds as the transition shape: the arm's PRIOR-state guard becomes the
create (Status=Fulfilled), the current guard becomes the update (NotEqualTo
→ a picklist alternative), and the entry filter's IsChanged fields must
genuinely change between the two. The real FL09 fixture Metadata drives the
flow entity for fidelity."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from uuid import uuid4

from primeqa.generation import governance_core as gc

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "semantic",
                       "fixtures", "pls_fb_flows",
                       "PLS_FB_FL09_Reopen_Guard.json")


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


def _fl09_entity(api="PLS_FB_FL09_Reopen_Guard"):
    with open(FIXTURE) as f:
        d = json.load(f)
    md = d.get("Metadata", d)
    return _ent("Flow", api, "Reopen Guard", attrs={"Metadata": md})


def _order_world(*, flows=None,
                 status_values=("Draft", "Submitted", "Fulfilled",
                                "Cancelled")):
    order = _ent("Object", "PLS_FB_Order__c", "PLS FB Order")
    reopened = _ent("Field", "PLS_FB_Order__c.PLS_FB_Reopened__c",
                    "Reopened", attrs={"data_type": "Checkbox"})
    status = _ent("Field", "PLS_FB_Order__c.PLS_FB_Status__c", "Status",
                  attrs={"data_type": "Picklist"})
    flows = [_fl09_entity()] if flows is None else flows
    rows = [SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=reopened),
            SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=status)]
    rows += [SimpleNamespace(edge_type=gc.EDGE_FLOW, entity=f) for f in flows]
    pvs_id = uuid4()
    details = {reopened.id: {"field_type": "boolean"},
               status.id: {"field_type": "picklist",
                           "picklist_value_set_entity_id": pvs_id}}
    picklists = {pvs_id: [{"value_api_name": v, "is_active": True}
                          for v in status_values]}
    s1 = _FakeS1(entities=[order, reopened, status] + list(flows),
                 rows_by_object={"PLS_FB_Order__c": rows},
                 details=details, picklists=picklists)
    return gc.GovernanceCore(s1)


def _state():
    return SimpleNamespace(control_facts=None, groundings=[])


EXCERPT = "an order that leaves Fulfilled state is marked as reopened"


def _intent(expected_value=True, **kw):
    d = {"ac_ref": 13, "archetype_hint": "data_behavior",
         "polarity_hint": "positive",
         "claim_kind_hint": "automation-effect-claim",
         "requirement_excerpt": EXCERPT,
         "target_subject_hint": {
             "entity_type": "Object", "sf_api_name": "PLS_FB_Order__c",
             "field_name": "PLS_FB_Order__c.PLS_FB_Reopened__c",
             "expected_value": expected_value, **kw}}
    return {"requirement_excerpt": EXCERPT, "intent_descriptor": d}


def _pairs(tf):
    return {ep.external_id.rsplit(".", 1)[-1]: v for ep, v in tf}


# ---------------------------------------------------------------------------
# the grounded path
# ---------------------------------------------------------------------------

def test_transition_intent_grounds_prior_state_as_the_create():
    core = _order_world()
    state = _state()
    res = core.resolve_intent(intent_input=_intent(), ctx=_ctx(), state=state)
    assert res.refusal is None, getattr(res.refusal, "payload", None)
    [g] = state.groundings
    assert g.automation.external_id == "PLS_FB_FL09_Reopen_Guard"
    assert g.effect_value is True
    assert g.automation_primitive == "flow"
    # prior guard (Status EqualTo Fulfilled) IS the create…
    assert _pairs(g.trigger_fields) == {"PLS_FB_Status__c": "Fulfilled"}
    # …the update leaves it (NotEqualTo → first alternative), a real change
    assert _pairs(g.update_trigger_fields) == {"PLS_FB_Status__c": "Draft"}


def test_substrate_transition_replaces_model_pairs():
    core = _order_world()
    state = _state()
    res = core.resolve_intent(
        intent_input=_intent(
            trigger_fields=[{"field_name": "PLS_FB_Order__c.PLS_FB_Status__c",
                             "value": "Submitted"}],
            update_trigger_fields=[
                {"field_name": "PLS_FB_Order__c.PLS_FB_Status__c",
                 "value": "Cancelled"}]),
        ctx=_ctx(), state=state)
    assert res.refusal is None
    [g] = state.groundings
    assert _pairs(g.trigger_fields) == {"PLS_FB_Status__c": "Fulfilled"}
    assert _pairs(g.update_trigger_fields) == {"PLS_FB_Status__c": "Draft"}


def test_deterministic_across_repeats():
    outs = []
    for _ in range(3):
        core = _order_world()
        state = _state()
        core.resolve_intent(intent_input=_intent(), ctx=_ctx(), state=state)
        [g] = state.groundings
        outs.append((tuple(sorted(_pairs(g.trigger_fields).items())),
                     tuple(sorted(_pairs(g.update_trigger_fields).items()))))
    assert len(set(outs)) == 1


# ---------------------------------------------------------------------------
# honesty boundaries
# ---------------------------------------------------------------------------

def _detail(res):
    assert res.refusal is not None, "expected a refusal"
    return res.refusal.payload.get("detail", "")


def test_two_transition_producers_refuse():
    core = _order_world(flows=[_fl09_entity(),
                               _fl09_entity("PLS_FB_FL97_Other_Guard")])
    res = core.resolve_intent(intent_input=_intent(), ctx=_ctx(),
                              state=_state())
    assert "2 Update-trigger Flows" in _detail(res)


def test_no_update_alternative_refuses():
    # a one-value picklist cannot leave the prior state
    core = _order_world(status_values=("Fulfilled",))
    res = core.resolve_intent(intent_input=_intent(), ctx=_ctx(),
                              state=_state())
    assert "no alternative active picklist value" in _detail(res)


def test_wrong_effect_value_still_refuses():
    # Reopened=False is not what the arm writes — no producer, refuse
    core = _order_world()
    res = core.resolve_intent(intent_input=_intent(expected_value=False),
                              ctx=_ctx(), state=_state())
    assert res.refusal is not None
