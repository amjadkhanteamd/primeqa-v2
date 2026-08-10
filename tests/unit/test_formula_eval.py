"""Unit tests for the formula-evaluate primitive (D-113).

``evaluate(parse(formula), payload) -> True | False | NonEvaluable`` — the
evaluation counterpart of D-107 ``derive``. Covers the evaluable subset
(comparisons, AND/OR/NOT, ISBLANK/ISNULL/ISPICKVAL), the Kleene three-valued
combination (a determinable result resolves past a non-evaluable sibling), and
the NonEvaluable boundary (org-state funcs / dotted refs / NotParsed /
type-uncertain), mirroring ``derive``'s ``_Undecidable -> NotDerivable`` line.
"""
from __future__ import annotations

from primeqa.semantic.formula import NonEvaluable, evaluate, is_parsed, parse


def _ev(formula: str, payload: dict):
    return evaluate(parse(formula), payload)


# --- comparisons -----------------------------------------------------------

def test_number_comparison_fires_and_not():
    assert _ev("Amount < 100", {"Amount": 99}) is True
    assert _ev("Amount < 100", {"Amount": 150}) is False
    assert _ev("Amount < 100", {"Amount": 100}) is False        # strict
    assert _ev("Amount >= 100", {"Amount": 100}) is True
    assert _ev("Amount = 5", {"Amount": 5}) is True
    assert _ev("Amount <> 5", {"Amount": 7}) is True


def test_loosened_formula_still_violated_is_true():
    # THE precision case the proxy gets wrong: the stored payload (Amount=99,
    # derived from an old `Amount < 100`) STILL violates a loosened `Amount < 200`.
    assert _ev("Amount < 200", {"Amount": 99}) is True


def test_tightened_formula_no_longer_violated_is_false():
    assert _ev("Amount < 50", {"Amount": 99}) is False


def test_literal_on_left_is_oriented():
    # `100 > Amount` re-orients to `Amount < 100`.
    assert _ev("100 > Amount", {"Amount": 99}) is True
    assert _ev("100 > Amount", {"Amount": 150}) is False


# --- functions -------------------------------------------------------------

def test_isblank_isnull():
    assert _ev("ISBLANK(Reason__c)", {"Reason__c": None}) is True
    assert _ev("ISBLANK(Reason__c)", {"Reason__c": ""}) is True
    assert _ev("ISBLANK(Reason__c)", {"Reason__c": "x"}) is False
    assert _ev("ISBLANK(Reason__c)", {}) is True                 # absent = blank
    # D-442 (B1): ISNULL refuses without a KNOWN-nullable field type — SF's
    # "text fields are never null" makes the type load-bearing; unknown
    # type must never yield a guessed verdict.
    from primeqa.semantic.formula import EvalContext
    assert isinstance(_ev("ISNULL(Reason__c)", {"Reason__c": None}),
                      NonEvaluable)
    num_ctx = EvalContext(field_type_of=lambda f: "double")
    assert evaluate(parse("ISNULL(Reason__c)"), {"Reason__c": None},
                    context=num_ctx) is True


def test_ispickval():
    # SF formula string literals are double-quoted.
    assert _ev('ISPICKVAL(Status, "Old")', {"Status": "Old"}) is True
    assert _ev('ISPICKVAL(Status, "Old")', {"Status": "New"}) is False


# --- Kleene three-valued connectives ---------------------------------------

def test_or_short_circuits_past_non_evaluable():
    # ISBLANK(A) is True -> the OR is True even though PRIORVALUE(B) is org-state.
    r = _ev('ISBLANK(A) || PRIORVALUE(B) = "x"', {"A": None})
    assert r is True


def test_or_is_non_evaluable_when_undetermined():
    # ISBLANK(A) is False and the other disjunct is non-evaluable -> NonEvaluable.
    r = _ev('ISBLANK(A) || PRIORVALUE(B) = "x"', {"A": "set"})
    assert isinstance(r, NonEvaluable)


def test_and_false_dominates_over_non_evaluable():
    # Amount<100 is False -> the AND is False even with an org-state conjunct.
    r = _ev("(Amount < 100) && ISCHANGED(X)", {"Amount": 150})
    assert r is False


def test_and_is_non_evaluable_when_undetermined():
    r = _ev("(Amount < 100) && ISCHANGED(X)", {"Amount": 99})
    assert isinstance(r, NonEvaluable)


def test_not():
    assert _ev("NOT(ISBLANK(A))", {"A": None}) is False
    assert _ev("NOT(ISBLANK(A))", {"A": "x"}) is True
    assert isinstance(_ev("NOT(ISCHANGED(X))", {}), NonEvaluable)


# --- the NonEvaluable boundary (mirrors derive's NotDerivable) --------------

def test_org_state_function_non_evaluable():
    r = _ev("ISCHANGED(Status)", {})
    assert isinstance(r, NonEvaluable) and "org-state" in r.reason
    assert isinstance(_ev('PRIORVALUE(Amount) = 5', {"Amount": 5}), NonEvaluable)


def test_cross_object_dotted_ref_non_evaluable():
    r = _ev('Account.Industry = "Tech"', {})
    assert isinstance(r, NonEvaluable) and "cross-object" in r.reason


def test_field_to_field_non_evaluable():
    r = _ev("Amount = OtherAmount", {"Amount": 1})
    assert isinstance(r, NonEvaluable) and "field-to-field" in r.reason


def test_non_numeric_ordering_non_evaluable():
    r = _ev('Name < "M"', {"Name": "A"})
    assert isinstance(r, NonEvaluable) and "ordering" in r.reason


def test_not_parsed_is_non_evaluable():
    res = parse("VLOOKUP($Setup, x, y)")        # unrecognized construct
    assert not is_parsed(res)
    assert isinstance(evaluate(res, {}), NonEvaluable)
    assert evaluate(res, {}).reason == "formula not parsed"
