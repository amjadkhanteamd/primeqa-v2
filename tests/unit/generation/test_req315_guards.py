"""Phase-1 tactical guards for the req-315 benchmark (truthful/executable tests).

Pure-function coverage for the two guards whose logic is not otherwise pinned by
a grounding-path test:
  - 1.3 percent formula->API value conversion (_to_transport_payload), the T8
    false-accept fix (a percent VR's derived formula value must be ×100 into the
    API domain or the rule never fires);
  - 1.1 value-claim type-validity floor (_value_type_invalid), the T5 executable-
    value fix.
"""
from decimal import Decimal

from primeqa.generation.emission import _to_transport_payload
from primeqa.generation.governance_core import _value_type_invalid


# --- 1.3 percent conversion -------------------------------------------------

def test_transport_percent_scales_by_100():
    meta = {"PLS_BM_Discount__c": {"field_type": "percent"}}
    # 0.20 in formula domain is 20% -> API value 20 (so `Discount > 0.20` fires).
    assert _to_transport_payload({"PLS_BM_Discount__c": 0.2}, meta) == {
        "PLS_BM_Discount__c": 20}
    assert _to_transport_payload({"PLS_BM_Discount__c": 1.2}, meta) == {
        "PLS_BM_Discount__c": 120}


def test_transport_percent_non_integral_stays_float():
    meta = {"d": {"field_type": "percent"}}
    assert _to_transport_payload({"d": 0.155}, meta) == {"d": 15.5}


def test_transport_object_qualified_key_resolves_bare_meta():
    # payloads may be object-qualified; metadata is bare-keyed.
    meta = {"PLS_BM_Discount__c": {"field_type": "percent"}}
    assert _to_transport_payload(
        {"PLS_BM_Deal__c.PLS_BM_Discount__c": 0.2}, meta) == {
        "PLS_BM_Deal__c.PLS_BM_Discount__c": 20}


def test_transport_non_percent_passthrough():
    meta = {"Amount__c": {"field_type": "currency"},
            "Stage__c": {"field_type": "picklist"}}
    payload = {"Amount__c": 1000, "Stage__c": "Draft"}
    assert _to_transport_payload(payload, meta) == payload


def test_transport_none_and_nonnumeric_untouched():
    meta = {"d": {"field_type": "percent"}}
    assert _to_transport_payload({"d": None}, meta) == {"d": None}
    assert _to_transport_payload({"d": "n/a"}, meta) == {"d": "n/a"}


def test_transport_absent_metadata_passthrough():
    assert _to_transport_payload({"d": 0.2}, None) == {"d": 0.2}
    assert _to_transport_payload({"d": 0.2}, {}) == {"d": 0.2}


# --- 1.1 value-claim type-validity floor ------------------------------------

def test_value_type_invalid_non_numeric_on_numeric_field():
    assert _value_type_invalid("abc", {"field_type": "double"}) is not None
    assert _value_type_invalid("<UNKNOWN>", {"field_type": "currency"}) is not None


def test_value_type_valid_number_on_numeric_field():
    assert _value_type_invalid("100", {"field_type": "currency"}) is None
    assert _value_type_invalid(20, {"field_type": "percent"}) is None


def test_value_type_offlist_picklist_refuses():
    meta = {"field_type": "picklist", "picklist_values": ("SMB", "Enterprise")}
    assert _value_type_invalid("Nope", meta) is not None
    assert _value_type_invalid("Enterprise", meta) is None


def test_value_type_absent_metadata_or_text_passes():
    assert _value_type_invalid("anything", None) is None
    assert _value_type_invalid("anything", {"field_type": "string"}) is None
