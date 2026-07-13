"""C3 — band grounding end-to-end through ``resolve_intent`` (no PG, no LLM).

A per-band automation-effect intent (Tier = Gold) binds the ladder flow via
its grounded arm and the SUBSTRATE stages the create state that makes that
arm fire — the in-band interval witness, replacing whatever the model
proposed on the guard field (per-band identity stability). The real FL03
fixture Metadata drives the flow entity for fidelity."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from uuid import uuid4

from primeqa.generation import governance_core as gc

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "semantic",
                       "fixtures", "pls_fb_flows",
                       "PLS_FB_FL03_Tier_Banding.json")


def _ent(entity_type, api, label=None, attrs=None):
    return SimpleNamespace(id=uuid4(), entity_type=entity_type,
                           sf_api_name=api, display_name=label,
                           attributes=attrs or {})


class _FakeS1:
    def __init__(self, entities, rows_by_object=None, details=None):
        self._entities = entities
        self._rows_by_object = rows_by_object or {}
        self._details = details or {}

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


def _ctx(at=128):
    return SimpleNamespace(semantic_context=SimpleNamespace(s1_version_seq=at),
                           requirement_text="PLS FB Order lifecycle")


def _fl03_entity():
    with open(FIXTURE) as f:
        d = json.load(f)
    md = d.get("Metadata", d)
    return _ent("Flow", "PLS_FB_FL03_Tier_Banding", "Tier Banding",
                attrs={"Metadata": md})


def _order_world(*, amount_scale=2):
    order = _ent("Object", "PLS_FB_Order__c", "PLS FB Order")
    tier = _ent("Field", "PLS_FB_Order__c.PLS_FB_Tier__c", "Tier",
                attrs={"data_type": "Text"})
    amount = _ent("Field", "PLS_FB_Order__c.PLS_FB_Amount__c", "Amount",
                  attrs={"data_type": "Currency"})
    flow = _fl03_entity()
    rows = [SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=tier),
            SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=amount),
            SimpleNamespace(edge_type=gc.EDGE_FLOW, entity=flow)]
    details = {amount.id: {"field_type": "currency", "scale": amount_scale},
               tier.id: {"field_type": "text"}}
    s1 = _FakeS1(entities=[order, tier, amount, flow],
                 rows_by_object={"PLS_FB_Order__c": rows}, details=details)
    return gc.GovernanceCore(s1)


def _state():
    return SimpleNamespace(control_facts=None, groundings=[])


EXCERPT = ("orders are banded into commercial tiers by amount: 250k+ is "
           "Platinum, 50k+ Gold, 10k+ Silver, otherwise Bronze")


def _intent(expected_value, trigger_fields=None):
    d = {"ac_ref": 4, "archetype_hint": "data_behavior",
         "polarity_hint": "positive",
         "claim_kind_hint": "automation-effect-claim",
         "requirement_excerpt": EXCERPT,
         "target_subject_hint": {
             "entity_type": "Object", "sf_api_name": "PLS_FB_Order__c",
             "field_name": "PLS_FB_Order__c.PLS_FB_Tier__c"}}
    if expected_value is not None:
        d["target_subject_hint"]["expected_value"] = expected_value
    if trigger_fields is not None:
        d["target_subject_hint"]["trigger_fields"] = trigger_fields
    return {"requirement_excerpt": EXCERPT, "intent_descriptor": d}


def _staged(g):
    return {ep.external_id.rsplit(".", 1)[-1]: v for ep, v in g.trigger_fields}


# ---------------------------------------------------------------------------
# the grounded path — one fire arm per band, witness substrate-derived
# ---------------------------------------------------------------------------

def test_every_band_grounds_with_its_own_interval_witness():
    expected = {"Platinum": 250000.01, "Gold": 150000,
                "Silver": 30000, "Bronze": 9999.99}
    for band, witness in expected.items():
        core = _order_world()
        state = _state()
        res = core.resolve_intent(intent_input=_intent(band), ctx=_ctx(),
                                  state=state)
        assert res.refusal is None, (band, getattr(res.refusal, "payload",
                                                   None))
        [g] = state.groundings
        assert g.automation.external_id == "PLS_FB_FL03_Tier_Banding", band
        assert g.effect_value == band
        assert _staged(g) == {"PLS_FB_Amount__c": witness}, band


def test_substrate_witness_replaces_the_model_proposed_pair():
    # the model staged its own in-band pick — identity stability demands the
    # deterministic witness win
    core = _order_world()
    state = _state()
    res = core.resolve_intent(
        intent_input=_intent("Gold", trigger_fields=[
            {"field_name": "PLS_FB_Order__c.PLS_FB_Amount__c",
             "value": 61234}]),
        ctx=_ctx(), state=state)
    assert res.refusal is None
    [g] = state.groundings
    assert _staged(g) == {"PLS_FB_Amount__c": 150000}


def test_scale_zero_witnesses():
    core = _order_world(amount_scale=0)
    state = _state()
    core.resolve_intent(intent_input=_intent("Platinum"), ctx=_ctx(),
                        state=state)
    [g] = state.groundings
    assert _staged(g) == {"PLS_FB_Amount__c": 250001}


def test_deterministic_across_repeats():
    outs = []
    for _ in range(3):
        core = _order_world()
        state = _state()
        core.resolve_intent(intent_input=_intent("Silver"), ctx=_ctx(),
                            state=state)
        [g] = state.groundings
        outs.append(tuple(sorted(_staged(g).items())))
    assert len(set(outs)) == 1


# ---------------------------------------------------------------------------
# honesty boundaries
# ---------------------------------------------------------------------------

def test_unknown_band_value_still_refuses():
    # "Diamond" is not an arm — no producer, refuse (ground-or-refuse)
    core = _order_world()
    res = core.resolve_intent(intent_input=_intent("Diamond"), ctx=_ctx(),
                              state=_state())
    assert res.refusal is not None
    assert "produce" in res.refusal.payload.get("detail", "")


def test_unreadable_scale_refuses_with_named_detail():
    core = _order_world(amount_scale=None)
    res = core.resolve_intent(intent_input=_intent("Gold"), ctx=_ctx(),
                              state=_state())
    assert res.refusal is not None
    assert "scale" in res.refusal.payload.get("detail", "")


# ---------------------------------------------------------------------------
# B0.2 — field-miss recovery (the live tier-AC failure class)
# ---------------------------------------------------------------------------

def test_field_miss_offers_ranked_recovery_candidates():
    # the model's guess 'Commercial_Tier__c' must surface PLS_FB_Tier__c as
    # a ranked candidate — the alphabetized inventory line hid every custom
    # name behind '+N more'
    core = _order_world()
    state = _state()
    bad = _intent("Gold")
    bad["intent_descriptor"]["target_subject_hint"]["field_name"] = \
        "PLS_FB_Order__c.Commercial_Tier__c"
    res = core.resolve_intent(intent_input=bad, ctx=_ctx(), state=state)
    assert res.refusal is not None
    detail = res.refusal.payload.get("detail", "")
    assert "PLS_FB_Order__c.PLS_FB_Tier__c" in detail
    assert "re-propose" in detail
    offer = res.refusal.payload.get("candidates")
    assert offer and offer["source"] == "substrate"
    assert offer["proposed"] == "PLS_FB_Order__c.Commercial_Tier__c"
    assert any(c["sf_api_name"] == "PLS_FB_Order__c.PLS_FB_Tier__c"
               for c in offer["candidates"])


# ---------------------------------------------------------------------------
# C3b — value-less ladder enumeration (the requirement never names the tiers)
# ---------------------------------------------------------------------------

def test_valueless_intent_enumerates_every_band_arm():
    core = _order_world()
    state = _state()
    res = core.resolve_intent(intent_input=_intent(None), ctx=_ctx(),
                              state=state)
    assert res.refusal is None, getattr(res.refusal, "payload", None)
    # the 1:1 grounding<->presented invariant finalize's dedup relies on:
    # 4 stashed groundings <-> 4 returned presented candidates
    assert len(res.grounded_candidates) == len(state.groundings) == 4
    # all arms attribute to the SAME intent path (one AC)
    assert len({c.path_id for c in res.grounded_candidates}) == 1
    by_value = {g.effect_value: g for g in state.groundings}
    assert set(by_value) == {"Platinum", "Gold", "Silver", "Bronze"}
    expected = {"Platinum": 250000.01, "Gold": 150000,
                "Silver": 30000, "Bronze": 9999.99}
    for band, g in by_value.items():
        assert g.automation.external_id == "PLS_FB_FL03_Tier_Banding", band
        assert g.automation_primitive == "flow"
        assert _staged(g) == {"PLS_FB_Amount__c": expected[band]}, band


def test_valueless_enumeration_is_deterministic():
    outs = []
    for _ in range(3):
        core = _order_world()
        state = _state()
        core.resolve_intent(intent_input=_intent(None), ctx=_ctx(),
                            state=state)
        outs.append(tuple((g.effect_value,
                           tuple(sorted(_staged(g).items())))
                          for g in state.groundings))
    assert len(set(outs)) == 1 and len(outs[0]) == 4


def test_wrong_value_refusal_discloses_the_arm_values():
    core = _order_world()
    res = core.resolve_intent(intent_input=_intent("Diamond"), ctx=_ctx(),
                              state=_state())
    assert res.refusal is not None
    detail = res.refusal.payload.get("detail", "")
    assert "writes one of" in detail
    for band in ("Bronze", "Gold", "Platinum", "Silver"):
        assert band in detail
    assert "omit expected_value" in detail
