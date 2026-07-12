"""FL02 slice (M2) — transformation grounding end-to-end through
``resolve_intent`` (no PG, no LLM).

A VALUE-LESS same-record automation-effect intent ("the org stores the
canonical form") grounds when exactly one Flow's Behaviour IR carries a
grounded TRANSFORM on the field: the substrate derives a format-valid
canonical witness from the field's own governing REGEX rules, stages the
de-transformed raw, binds the verified rewrite producer, and stashes the
transform provenance. Every honesty boundary refuses with a named detail.
The real FL02 fixture Metadata drives the flow entity for fidelity."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from primeqa.generation import governance_core as gc
from primeqa.generation.verified_negative import regex_matching_value

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "semantic",
                       "fixtures", "pls_fb_flows",
                       "PLS_FB_FL02_Normalize_External_Ref.json")


def _ent(entity_type, api, label=None, attrs=None):
    return SimpleNamespace(id=uuid4(), entity_type=entity_type,
                           sf_api_name=api, display_name=label,
                           attributes=attrs or {})


class _FakeS1:
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


def _fl02_entity():
    with open(FIXTURE) as f:
        d = json.load(f)
    md = d.get("Metadata", d)
    return _ent("Flow", "PLS_FB_FL02_Normalize_External_Ref",
                "Normalize External Ref", attrs={"Metadata": md})


VR01_FORMULA = ('NOT(ISBLANK(PLS_FB_External_Ref__c)) && '
                'NOT(REGEX(PLS_FB_External_Ref__c, "FB-[0-9]{6}"))')


def _order_world(*, flows=(), vr_formula=VR01_FORMULA, vr_active=True):
    order = _ent("Object", "PLS_FB_Order__c", "PLS FB Order")
    ref = _ent("Field", "PLS_FB_Order__c.PLS_FB_External_Ref__c",
               "External Ref", attrs={"data_type": "Text"})
    rows = [SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=ref)]
    if vr_formula is not None:
        vr = _ent("ValidationRule",
                  "PLS_FB_Order__c.PLS_FB_VR01_External_Ref_Format",
                  "External Ref Format",
                  attrs={"formula_text": vr_formula, "is_active": vr_active})
        rows.append(SimpleNamespace(edge_type=gc.EDGE_VALIDATION_RULE,
                                    entity=vr))
    rows += [SimpleNamespace(edge_type=gc.EDGE_FLOW, entity=f) for f in flows]
    s1 = _FakeS1(entities=[order, ref] + list(flows),
                 rows_by_object={"PLS_FB_Order__c": rows})
    return gc.GovernanceCore(s1)


def _state():
    return SimpleNamespace(control_facts=None, groundings=[])


EXCERPT = ("external references are always stored in the company's "
           "canonical uppercase form")


def _intent(expected_value=None):
    d = {"ac_ref": 3, "archetype_hint": "data_behavior",
         "polarity_hint": "positive",
         "claim_kind_hint": "automation-effect-claim",
         "requirement_excerpt": EXCERPT,
         "target_subject_hint": {
             "entity_type": "Object", "sf_api_name": "PLS_FB_Order__c",
             "field_name": "PLS_FB_Order__c.PLS_FB_External_Ref__c"}}
    if expected_value is not None:
        d["target_subject_hint"]["expected_value"] = expected_value
    return {"requirement_excerpt": EXCERPT, "intent_descriptor": d}


# ---------------------------------------------------------------------------
# the grounded path
# ---------------------------------------------------------------------------

def test_valueless_transform_intent_grounds_with_synthesized_witness():
    core = _order_world(flows=[_fl02_entity()])
    state = _state()
    res = core.resolve_intent(intent_input=_intent(), ctx=_ctx(), state=state)
    assert res.refusal is None, getattr(res.refusal, "payload", None)
    [g] = state.groundings
    assert g.automation.external_id == "PLS_FB_FL02_Normalize_External_Ref"
    assert g.effect_field.external_id == "PLS_FB_Order__c.PLS_FB_External_Ref__c"
    assert g.effect_value == "FB-000000"          # synthesized canonical
    assert g.transform_chain == ("TRIM", "UPPER")
    assert g.transform_staged_value == " fb-000000 "   # de-transformed raw
    assert g.transform_source_field == "PLS_FB_External_Ref__c"
    assert g.automation_primitive == "flow"


def test_placeholder_expected_value_takes_the_transform_path():
    """The AC3 arrival shape: '<canonical uppercase normalized>' is scrubbed
    to absent at hint ingress and the transform grounding takes over."""
    core = _order_world(flows=[_fl02_entity()])
    state = _state()
    res = core.resolve_intent(
        intent_input=_intent("<canonical uppercase normalized>"),
        ctx=_ctx(), state=state)
    assert res.refusal is None
    [g] = state.groundings
    assert g.effect_value == "FB-000000"
    assert g.transform_chain == ("TRIM", "UPPER")


def test_deterministic_across_repeats():
    core = _order_world(flows=[_fl02_entity()])
    outs = []
    for _ in range(3):
        state = _state()
        core.resolve_intent(intent_input=_intent(), ctx=_ctx(), state=state)
        [g] = state.groundings
        outs.append((g.effect_value, g.transform_staged_value,
                     g.transform_chain))
    assert len(set(outs)) == 1


# ---------------------------------------------------------------------------
# honesty boundaries — every failure refuses with a named detail
# ---------------------------------------------------------------------------

def _detail(res):
    assert res.refusal is not None, "expected a refusal"
    return res.refusal.payload.get("detail", "")


def test_two_transform_producers_refuse_disambiguation():
    f2 = _fl02_entity()
    f3 = _fl02_entity()
    f3.sf_api_name = "PLS_FB_FL99_Other_Normalizer"
    core = _order_world(flows=[f2, f3])
    res = core.resolve_intent(intent_input=_intent(), ctx=_ctx(),
                              state=_state())
    assert "2 Flows verifiably transform" in _detail(res)


def test_opaque_governing_rule_refuses_witness():
    core = _order_world(flows=[_fl02_entity()],
                        vr_formula="LEN(PLS_FB_External_Ref__c) > 200 || "
                                   "CONTAINS(PLS_FB_External_Ref__c, '!!')")
    res = core.resolve_intent(intent_input=_intent(), ctx=_ctx(),
                              state=_state())
    assert "not readable" in _detail(res)


def test_unsynthesizable_pattern_refuses_witness():
    core = _order_world(
        flows=[_fl02_entity()],
        vr_formula='NOT(REGEX(PLS_FB_External_Ref__c, "[A-Z]{3}(X|Y)"))')
    res = core.resolve_intent(intent_input=_intent(), ctx=_ctx(),
                              state=_state())
    assert "outside the bounded synthesis grammar" in _detail(res)


def test_inactive_rule_does_not_constrain_the_witness():
    core = _order_world(flows=[_fl02_entity()],
                        vr_formula='NOT(REGEX(PLS_FB_External_Ref__c, '
                                   '"[A-Z]{3}(X|Y)"))', vr_active=False)
    state = _state()
    res = core.resolve_intent(intent_input=_intent(), ctx=_ctx(), state=state)
    assert res.refusal is None          # D-301: inactive rules never ground
    [g] = state.groundings
    # the no-format default seed, chain-normalized (canonical must be a
    # fixed point of the transform — it IS the post-save value)
    assert g.effect_value == "SAMPLE VALUE 1"
    assert g.transform_staged_value == " sample value 1 "


def test_no_transform_producer_keeps_the_existing_refusal():
    # an unrelated literal flow keeps Layer-1 admitted (bool(flows)) so the
    # intent reaches the same-record tail — where the value-less refusal
    # stays byte-identical when nothing verifiably rewrites the field
    other = _ent("Flow", "PLS_FB_FL01_Default_Priority", "Default Priority",
                 attrs={"Metadata": {
                     "start": {"object": "PLS_FB_Order__c",
                               "triggerType": "RecordBeforeSave",
                               "recordTriggerType": "Create",
                               "connector": {"targetReference": "Set"}},
                     "assignments": [{
                         "name": "Set",
                         "assignmentItems": [{
                             "assignToReference": "$Record.PLS_FB_Priority__c",
                             "operator": "Assign",
                             "value": {"stringValue": "Standard"}}]}]}})
    core = _order_world(flows=[other])
    res = core.resolve_intent(intent_input=_intent(), ctx=_ctx(),
                              state=_state())
    assert "needs a verifiable effect" in _detail(res)


def test_zero_flows_world_dismisses_at_admissibility():
    core = _order_world(flows=[])       # no automation at all on the subject
    res = core.resolve_intent(intent_input=_intent(), ctx=_ctx(),
                              state=_state())
    assert res.refusal is not None
    assert res.refusal.payload.get("dismissal_reason") == "insufficient_grounding"
    assert res.refusal.payload.get("detail_layer") == "admissibility"


def test_update_phase_transform_intent_stays_refused():
    """v1 is create-scoped: an update-phase normalization intent falls
    through to the existing refusal rather than authoring a wrong shape."""
    core = _order_world(flows=[_fl02_entity()])
    intent = _intent()
    intent["intent_descriptor"]["target_subject_hint"][
        "update_trigger_fields"] = [
        {"field": "PLS_FB_Order__c.PLS_FB_External_Ref__c", "value": "x"}]
    res = core.resolve_intent(intent_input=intent, ctx=_ctx(), state=_state())
    assert res.refusal is not None


# ---------------------------------------------------------------------------
# the synthesizer boundary (D-344 extension)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pattern,expected", [
    ("FB-[0-9]{6}", "FB-000000"),
    (r"INV-\d{4}-[0-9]{2}", "INV-0000-00"),
    ("AB[0-9]{2,5}", "AB00"),
    ("(A|B)[0-9]+", None),          # alternation / unbounded — out of grammar
    ("[A-Z]{3}", None),             # non-digit class — out of grammar
    ("", None),
    (None, None),
])
def test_regex_matching_value_bounds(pattern, expected):
    assert regex_matching_value(pattern) == expected
