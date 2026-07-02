"""Unit: the shape-tolerant VR attribute extractors (D-203.1).

Two attribute shapes coexist in entities.attributes JSONB: the designed
ValidationRuleAttributes projection (formula_text — seeds + pre-cutover sync)
and the post-cutover sync's raw Tooling record (Metadata.errorConditionFormula).
The extractors accept both; readers (S3 verified-gate, S6 attribution) must
never silently lose formulas again."""
from __future__ import annotations

from primeqa.semantic.entity_attributes import vr_error_message, vr_formula_text

_RAW = {  # the post-cutover sync shape (verbatim keys from a real synced row)
    "Id": "03dF9000000d0AAAA", "Active": True,
    "FullName": "Opportunity.Amount",
    "Metadata": {"active": True, "errorConditionFormula": "Amount  > 10000",
                 "errorMessage": "Amount too large"},
    "ErrorMessage": "Amount too large", "ValidationName": "Amount",
}
_DESIGNED = {"formula_text": "ISBLANK(Reason__c)",
             "error_message": "reason required", "error_display_field": None}


def test_designed_shape():
    assert vr_formula_text(_DESIGNED) == "ISBLANK(Reason__c)"
    assert vr_error_message(_DESIGNED) == "reason required"


def test_raw_tooling_shape():
    assert vr_formula_text(_RAW) == "Amount  > 10000"
    assert vr_error_message(_RAW) == "Amount too large"


def test_designed_wins_when_both_present():
    both = {**_RAW, "formula_text": "X > 1"}
    assert vr_formula_text(both) == "X > 1"


def test_empty_and_none_safe():
    assert vr_formula_text(None) is None
    assert vr_formula_text({}) is None
    assert vr_formula_text({"Metadata": None}) is None
    assert vr_error_message({}) is None


# ---------------------------------------------------------------------------
# D-301: vr_is_active — shape-tolerant active flag.
# ---------------------------------------------------------------------------

def test_vr_is_active_designed_shape():
    from primeqa.semantic.entity_attributes import vr_is_active
    assert vr_is_active({"is_active": True}) is True
    assert vr_is_active({"is_active": False}) is False


def test_vr_is_active_raw_tooling_shape():
    from primeqa.semantic.entity_attributes import vr_is_active
    # the live env-59 shape: top-level Active + Metadata.active
    assert vr_is_active({"Active": False, "Metadata": {"active": False}}) is False
    assert vr_is_active({"Active": True, "Metadata": {"active": True}}) is True
    # Metadata-only fallback
    assert vr_is_active({"Metadata": {"active": False}}) is False


def test_vr_is_active_missing_defaults_true():
    from primeqa.semantic.entity_attributes import vr_is_active
    # an attribute-less row must not silently demote negatives to caveated
    assert vr_is_active({}) is True
    assert vr_is_active(None) is True
    assert vr_is_active({"formula_text": "Amount > 0"}) is True


# ---------------------------------------------------------------------------
# D-304: field_is_calculated / field_formula_text — two-shape tolerance.
# ---------------------------------------------------------------------------

def test_field_is_calculated_shapes():
    from primeqa.semantic.entity_attributes import field_is_calculated
    assert field_is_calculated({"is_calculated": True}) is True       # designed
    assert field_is_calculated({"calculated": True}) is True          # raw describe
    assert field_is_calculated({"calculated": False}) is False
    assert field_is_calculated({}) is False                           # plain field
    assert field_is_calculated(None) is False


def test_field_formula_text_shapes():
    from primeqa.semantic.entity_attributes import field_formula_text
    assert field_formula_text({"formula": "A + B"}) == "A + B"        # designed
    # the live env-59 raw shape (probe-verified on Loan_to_Value__c)
    assert field_formula_text(
        {"calculatedFormula": "IF(P > 0, L / P, null)"}) == "IF(P > 0, L / P, null)"
    assert field_formula_text({}) is None
    assert field_formula_text(None) is None
