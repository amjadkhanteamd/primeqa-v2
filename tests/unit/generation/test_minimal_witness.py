"""P1 (Amendment B): the minimally violating witness — one reusable primitive
(typed_value.minimal_increment) composed by derivation and the boundary set.

The invariant (AK): a derived witness should be minimally violating where an
ordered boundary is available — the nearest representable value past the
boundary, derived from the field's precision in the correct value domain,
never an arbitrary ±1 (the 125% trap: sound in the formula but exposed to
field precision / other controls / domain plausibility at run time).
"""
from decimal import Decimal

from primeqa.generation.typed_value import minimal_increment, to_transport
from primeqa.generation.verified_negative import derive, derive_boundary_set
from primeqa.semantic.formula import parse

VR08 = "RecordType.DeveloperName = \"PLS_BM_Enterprise\" && PLS_BM_Discount__c > 0.25"
RAIL = {
    "PLS_BM_Discount__c": {"field_type": "percent", "scale": 2,
                           "is_createable": True, "is_updateable": True},
    "__record_types__": {"PLS_BM_Enterprise": "012Ent000000000AAA",
                         "PLS_BM_Standard": "012Std000000000AAA"},
}


# -- the primitive -------------------------------------------------------------

def test_minimal_increment_percent_formula_domain():
    # Display scale 2 on a percent → formula-domain step 10^-(2+2).
    assert minimal_increment("percent", 2) == Decimal("0.0001")


def test_minimal_increment_plain_numeric():
    assert minimal_increment("currency", 2) == Decimal("0.01")
    assert minimal_increment("double", 0) == Decimal("1")


def test_minimal_increment_refuses_unknown():
    assert minimal_increment("percent", None) is None
    assert minimal_increment(None, 2) is None
    assert minimal_increment("text", 2) is None
    assert minimal_increment("double", -1) is None


# -- derivation composes it (the reject witness) --------------------------------

def test_vr08_witness_is_minimally_violating():
    neg = derive(parse(VR08), RAIL)
    # 0.25 + 0.0001 — the nearest representable violation, not 1.25.
    assert neg.violating_payload["PLS_BM_Discount__c"] == 0.2501
    assert neg.violating_payload["RecordTypeId"] == "012Ent000000000AAA"


def test_witness_transports_to_display_2501():
    assert to_transport(0.2501, "percent", 2) == 25.01


def test_no_scale_keeps_pre_p1_fallback():
    # Without scale metadata the ±1 fallback is byte-identical (certainty bar).
    neg = derive(parse("PLS_BM_Deal_Value__c > 1000000"), {})
    assert neg.violating_payload["PLS_BM_Deal_Value__c"] == 1000001


# -- the boundary set composes it (BoundaryPair over a decimal threshold) -------

def test_boundary_set_vr08_fires_and_just_inside():
    members = derive_boundary_set(parse(VR08), RAIL)
    assert len(members) == 2
    firing = next(m for m in members if m.expect_reject)
    inside = next(m for m in members if not m.expect_reject)
    # Enterprise held constant in BOTH probes (the D-328 gate discipline).
    assert firing.payload["RecordTypeId"] == "012Ent000000000AAA"
    assert inside.payload["RecordTypeId"] == "012Ent000000000AAA"
    # 25.01% rejects; exactly 25.00% accepts ("Exactly 25% is allowed").
    assert firing.payload["PLS_BM_Discount__c"] == 0.2501
    assert inside.payload["PLS_BM_Discount__c"] == 0.25


def test_boundary_set_invariant_firing_equals_derive():
    # D-300 drift self-check holds under P1: the firing probe IS derive()'s payload.
    members = derive_boundary_set(parse(VR08), RAIL)
    firing = next(m for m in members if m.expect_reject)
    assert firing.payload == derive(parse(VR08), RAIL).violating_payload


def test_decimal_threshold_without_scale_still_refuses():
    # A fractional literal with no scale metadata keeps the D-300 refusal.
    assert derive_boundary_set(parse("PLS_BM_Discount__c > 0.25"), {}) == ()


def test_integer_threshold_without_meta_unchanged():
    members = derive_boundary_set(parse("PLS_BM_Deal_Value__c > 1000000"), {})
    assert len(members) == 2
    firing = next(m for m in members if m.expect_reject)
    assert firing.payload["PLS_BM_Deal_Value__c"] == 1000001
