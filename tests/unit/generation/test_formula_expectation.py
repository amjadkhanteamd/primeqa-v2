"""req-302 robustness R2+R3 units: the formula-expectation verifier (pure)
and the numeric effect-value guard.

The live defects these encode: claim 7649c167 asserted the PRE-update LTV
(62.5; the org correctly recomputed 75.0) and claim e15f7c91 shipped the
literal string "<computed>" into an approved percent-field claim."""
from __future__ import annotations

from primeqa.generation.formula_expectation import (
    as_decimal,
    verify_formula_expectation,
)
from primeqa.generation.governance_core import _numeric_effect_guard

LTV = "IF(Property_Value__c > 0, Loan_Amount__c / Property_Value__c, null)"


def _ltv(expected, update=None, **over):
    kw = dict(
        formula_text=LTV, expected_value=expected,
        create_inputs={"Opportunity.Loan_Amount__c": 5000000,
                       "Opportunity.Property_Value__c": 8000000},
        update_inputs=update or {},
        field_type="percent", scale=2, treat_null_as_zero=True)
    kw.update(over)
    return verify_formula_expectation(**kw)


def test_pre_update_match_is_the_7649c167_shape():
    v = _ltv("62.5", update={"Opportunity.Loan_Amount__c": 6000000})
    assert v.status == "pre_update_match"
    assert v.computed_final == "75" and v.computed_create == "62.5"


def test_post_update_value_matches():
    assert _ltv("75", update={"Opportunity.Loan_Amount__c": 6000000}).status \
        == "match"


def test_create_only_match_and_mismatch():
    assert _ltv("62.5").status == "match"
    v = _ltv("63")
    assert v.status == "mismatch" and v.computed_final == "62.5"


def test_percent_times_100_and_scale_rounding():
    # raw 5/8 = 0.625 → API 62.5 (percent ×100); scale=2 HALF_UP.
    v = verify_formula_expectation(
        formula_text="Loan_Amount__c / Property_Value__c",
        expected_value="33.33",
        create_inputs={"Loan_Amount__c": 1, "Property_Value__c": 3},
        update_inputs={}, field_type="percent", scale=2,
        treat_null_as_zero=False)
    assert v.status == "match" and v.computed_final == "33.33"


def test_decimal_exactness_of_currency_multiplication():
    # float would give 100 * 1.1 = 110.00000000000001; Decimal is exact.
    v = verify_formula_expectation(
        formula_text="Subtotal__c * 1.1", expected_value=110,
        create_inputs={"Order__c.Subtotal__c": 100}, update_inputs={},
        field_type="currency", scale=None, treat_null_as_zero=False)
    assert v.status == "match" and v.computed_final == "110"


def test_missing_formula_input_fails_open():
    # Load-bearing: padding-set fields are invisible to governance — an
    # unstaged formula input must NEVER refuse.
    v = _ltv("62.5", create_inputs={"Opportunity.Loan_Amount__c": 5000000})
    assert v.status == "not_evaluable"


def test_unparseable_formula_and_placeholder_fail_open():
    assert _ltv("62.5", formula_text="TEXT(Stage__c) & 'x'").status \
        == "not_evaluable"
    assert _ltv("<computed>").status == "not_evaluable"


def test_division_by_zero_fails_open():
    v = verify_formula_expectation(
        formula_text="A__c / B__c", expected_value="1",
        create_inputs={"A__c": 1, "B__c": 0}, update_inputs={},
        field_type="double", scale=None, treat_null_as_zero=False)
    assert v.status == "not_evaluable"


def test_treat_null_as_zero_both_ways():
    common = dict(formula_text="A__c + B__c", expected_value="5",
                  create_inputs={"A__c": 5, "B__c": None}, update_inputs={},
                  field_type="double", scale=None)
    assert verify_formula_expectation(
        treat_null_as_zero=True, **common).status == "match"
    # without the flag a null propagates → formula result null → not evaluable
    assert verify_formula_expectation(
        treat_null_as_zero=False, **common).status == "not_evaluable"


def test_as_decimal_rejects_bools_and_placeholders():
    assert as_decimal(True) is None
    assert as_decimal("<computed>") is None
    assert as_decimal("62.5") is not None and as_decimal(110) is not None


# --- the R2 guard truth table ------------------------------------------------

class _FieldEnt:
    def __init__(self, api, attrs=None):
        self.id = "fld-1"
        self.sf_api_name = api
        self.attributes = attrs or {}


class _S1Details:
    def __init__(self, details):
        self._d = details

    def get_entity_details(self, entity_id, at_seq):
        return self._d


def test_numeric_guard_refuses_placeholder_on_percent_field():
    ent = _FieldEnt("Opportunity.Loan_to_Value__c", {"type": "percent"})
    detail = _numeric_effect_guard(ent, "<computed>", _S1Details(None), 5)
    assert detail is not None and "<computed>" in detail and "percent" in detail


def test_numeric_guard_passes_numbers_and_unknown_types():
    ent = _FieldEnt("Order__c.Total__c")
    s1 = _S1Details({"field_type": "currency"})
    assert _numeric_effect_guard(ent, "110", s1, 5) is None
    assert _numeric_effect_guard(ent, 110.25, s1, 5) is None
    # unknown type → fail-open
    assert _numeric_effect_guard(
        _FieldEnt("X__c"), "<computed>", _S1Details(None), 5) is None
    # a string field is unguarded (text placeholders are S3's honest loop)
    assert _numeric_effect_guard(
        _FieldEnt("X__c", {"type": "string"}), "<computed>",
        _S1Details(None), 5) is None


def test_numeric_guard_refuses_bool_on_numeric_field():
    ent = _FieldEnt("X__c", {"type": "double"})
    assert _numeric_effect_guard(ent, True, _S1Details(None), 5) is not None
