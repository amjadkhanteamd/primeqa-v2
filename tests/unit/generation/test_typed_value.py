"""D-346 typed value boundary (formula -> transport)."""
from primeqa.generation.typed_value import to_transport, transport_payload


# --- to_transport (scalar) --------------------------------------------------

def test_percent_scales_to_display_number():
    assert to_transport(0.2, "percent") == 20        # 0.20 formula -> 20% API
    assert to_transport(1.2, "percent") == 120
    assert to_transport(0.155, "percent") == 15.5    # non-integral -> float


def test_percent_scale_quantizes():
    assert to_transport("0.2015", "percent", scale=2) == 20.15


def test_numeric_non_percent_passthrough():
    assert to_transport(1000, "currency") == 1000
    assert to_transport(42, "double") == 42
    assert to_transport(7, "int") == 7


def test_non_numeric_values_untouched_even_on_percent():
    assert to_transport(None, "percent") is None
    assert to_transport("n/a", "percent") == "n/a"
    assert to_transport(True, "percent") is True     # bool is not a number here


def test_future_consumer_types_pass_through_at_v1():
    # named but not yet converted -> identity (the seam, not a silent wrong-green).
    assert to_transport("2024-01-01", "date") == "2024-01-01"
    assert to_transport("PLS_BM_Enterprise", "reference") == "PLS_BM_Enterprise"
    assert to_transport("Home", "picklist") == "Home"


# --- transport_payload (dict) -----------------------------------------------

def test_transport_payload_scales_percent_by_bare_key():
    meta = {"PLS_BM_Discount__c": {"field_type": "percent"}}
    assert transport_payload({"Obj.PLS_BM_Discount__c": 0.2}, meta) == {
        "Obj.PLS_BM_Discount__c": 20}


def test_transport_payload_mixed_and_absent_metadata():
    meta = {"Amount__c": {"field_type": "currency"}}
    assert transport_payload({"Amount__c": 1000, "Other__c": "x"}, meta) == {
        "Amount__c": 1000, "Other__c": "x"}
    assert transport_payload({"d": 0.2}, None) == {"d": 0.2}
