"""RecordType context hypothesis + control-relevance nomination wiring —
Amendment B (AK 2026-07-09). Governance-level tests over the deterministic
DeveloperName grounding and the nomination helper, with lightweight fakes.
"""
from types import SimpleNamespace as NS
from uuid import uuid4

import primeqa.generation.governance_core as gc
from primeqa.generation import control_relevance as cr
from primeqa.generation.emission import _Endpoint, _GroundedCondition
from primeqa.generation.verified_negative import _RECORD_TYPES_KEY

from tests.unit.generation.test_control_relevance import ALL_VRS, VR08, VR02

RAIL = {"PLS_BM_Enterprise": "012Ent000000000AAA",
        "PLS_BM_Standard": "012Std000000000AAA"}
FM = {_RECORD_TYPES_KEY: RAIL}
VR_ALL = tuple(t for _n, t in ALL_VRS)
EXCERPT = ("Enterprise deals are subject to stricter discount controls than "
           "standard deals.")


def _cond(api, pred, val):
    return _GroundedCondition(
        field=_Endpoint(entity_id=uuid4(), entity_type="Field",
                        external_id=f"PLS_BM_Deal__c.{api}"),
        predicate=pred, value=val)


def _nbhd():
    return [NS(entity=NS(id=uuid4(), entity_type="RecordType",
                         sf_api_name=f"PLS_BM_Deal__c.{d}", sf_id=RAIL[d]))
            for d in RAIL]


# -- deterministic DeveloperName normalization (AK Decision 1) -----------------

def test_normalize_exact_and_prefix_stripped():
    assert gc._normalize_to_devname("Enterprise", RAIL) == "PLS_BM_Enterprise"
    assert gc._normalize_to_devname("standard", RAIL) == "PLS_BM_Standard"
    assert gc._normalize_to_devname("PLS_BM_Enterprise", RAIL) == "PLS_BM_Enterprise"


def test_normalize_refuses_unknown_and_empty():
    assert gc._normalize_to_devname("Partner", RAIL) is None
    assert gc._normalize_to_devname("", RAIL) is None
    assert gc._normalize_to_devname("Enterprise", {}) is None


def test_provable_prefix_needs_underscore_boundary():
    assert gc._provable_devname_prefix(["PLS_BM_Enterprise", "PLS_BM_Standard"]) == "PLS_BM_"
    # No shared _-terminated prefix → no normalization crutch.
    assert gc._provable_devname_prefix(["Enterprise", "Standard"]) == ""


# -- forming the context hypothesis -------------------------------------------

def test_ground_context_from_deal_type_value():
    grounded = [_cond("PLS_BM_Deal_Type__c", "equals", "Enterprise"),
                _cond("PLS_BM_Discount__c", "exceeds", 0.25)]
    dev, ep, ccond = gc._ground_record_type_context(grounded, FM, _nbhd())
    assert dev == "PLS_BM_Enterprise"
    assert ep.entity_type == "RecordType"
    assert ep.external_id == "PLS_BM_Deal__c.PLS_BM_Enterprise"
    assert ccond.value == "Enterprise"


def test_no_context_without_matching_value():
    # A prohibition whose conditions carry no record-type-matching value → None
    # (existing field-overlap path untouched — the VR01/02/04/09 safety property).
    grounded = [_cond("PLS_BM_Deal_Value__c", "less_than", 0)]
    assert gc._ground_record_type_context(grounded, FM, _nbhd()) is None


# -- the full nomination ------------------------------------------------------

def test_nominate_returns_vr08_with_context_condition():
    grounded = [_cond("PLS_BM_Deal_Type__c", "equals", "Enterprise"),
                _cond("PLS_BM_Discount__c", "exceeds", 0.25)]
    res = gc._nominate_record_type_control(VR_ALL, grounded, FM, _nbhd(), EXCERPT)
    assert isinstance(res, tuple)
    vr, ctx = res
    assert vr == VR08
    assert ctx.field.entity_type == "RecordType"
    assert ctx.predicate == "equals" and ctx.value == "PLS_BM_Enterprise"


def test_nominate_uses_excerpt_role_fallback():
    # Predicate doesn't pin a role; the excerpt frame "stricter … controls" → cap.
    grounded = [_cond("PLS_BM_Deal_Type__c", "equals", "Enterprise"),
                _cond("PLS_BM_Discount__c", "matches_pattern", "x")]
    res = gc._nominate_record_type_control(VR_ALL, grounded, FM, _nbhd(), EXCERPT)
    assert isinstance(res, tuple) and res[0] == VR08


def test_nominate_refuses_without_role():
    # No role from predicate and no role frame in the excerpt → refuse (None).
    grounded = [_cond("PLS_BM_Deal_Type__c", "equals", "Enterprise"),
                _cond("PLS_BM_Discount__c", "matches_pattern", "x")]
    res = gc._nominate_record_type_control(
        VR_ALL, grounded, FM, _nbhd(), "Enterprise deals have some discount rule.")
    assert res is None


def test_nominate_none_when_no_record_type_gate_for_devname():
    # "Standard" grounds a context, but no VR gates on RecordType=Standard → None.
    grounded = [_cond("PLS_BM_Deal_Type__c", "equals", "Standard"),
                _cond("PLS_BM_Discount__c", "exceeds", 0.25)]
    assert gc._nominate_record_type_control(VR_ALL, grounded, FM, _nbhd(), EXCERPT) is None


def test_refuse_and_surface_when_both_hypotheses_role_align():
    # A field-hypothesis VR that UNIQUELY aligns a CAP on Discount (a Deal_Type-gated
    # discount cap) AND the record-type nomination of VR08 → both hypotheses role-align
    # distinct controls → refuse-and-surface (never silently pick). Minimal VR set so
    # the field cap is the unique field-overlap winner (VR10 would otherwise tie it).
    grounded = [_cond("PLS_BM_Deal_Type__c", "equals", "Enterprise"),
                _cond("PLS_BM_Discount__c", "exceeds", 0.25)]
    field_cap = ("ISPICKVAL(PLS_BM_Deal_Type__c, \"Enterprise\") && "
                 "PLS_BM_Discount__c > 0.25")   # Deal_Type-gated cap on both fields
    res = gc._nominate_record_type_control(
        (VR08, field_cap), grounded, FM, _nbhd(), EXCERPT)
    assert isinstance(res, str)
    assert "classification-mechanism-ambiguous" in res
