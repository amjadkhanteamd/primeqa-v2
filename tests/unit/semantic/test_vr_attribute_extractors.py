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
