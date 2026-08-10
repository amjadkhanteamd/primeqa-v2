"""D-442 pins: the unsound-construct guards and the percent-space fix.

B1 — ISNULL evaluable only on KNOWN-nullable field types; text-like AND
unknown types refuse (never a guessed verdict). B2 — the empty-literal
ISPICKVAL blank test always refuses. Percent — fraction-space conversion
(÷100), pinned against the LIVE org boundary that proved it: create
Discount=20 does NOT fire `> 0.20`, update 20.01 does.
"""
import pytest

from primeqa.semantic.formula import EvalContext, NonEvaluable, evaluate, parse

pytestmark = pytest.mark.unit


def ev(formula, payload, **ctx):
    return evaluate(parse(formula), payload,
                    context=EvalContext(**ctx) if ctx else None)


def types(mapping):
    return lambda f: mapping.get(f)


# ---------------------------------------------------------------------------
# B1 — ISNULL type guard
# ---------------------------------------------------------------------------

def test_isnull_numeric_types_stay_evaluable():
    for t in ("double", "currency", "percent", "int", "long", "date",
              "datetime"):
        ctx = types({"F__c": t})
        assert ev("ISNULL(F__c)", {"F__c": None}, field_type_of=ctx) is True
        assert ev("ISNULL(F__c)", {"F__c": 5}, field_type_of=ctx) is False


def test_isnull_text_like_types_refuse():
    """SF: 'text fields are never null' — honoured by REFUSAL, not by
    emulating always-False from a secondary source."""
    for t in ("string", "textarea", "picklist", "email", "phone", "url"):
        r = ev("ISNULL(F__c)", {"F__c": ""},
               field_type_of=types({"F__c": t}))
        assert isinstance(r, NonEvaluable)
        assert "refusing rather than guessing" in r.reason


def test_isnull_unknown_type_refuses():
    assert isinstance(
        ev("ISNULL(F__c)", {"F__c": None},
           field_type_of=types({})), NonEvaluable)     # resolver says None
    assert isinstance(
        ev("ISNULL(F__c)", {"F__c": None}), NonEvaluable)   # no resolver


def test_isblank_unaffected_by_the_isnull_guard():
    assert ev("ISBLANK(F__c)", {"F__c": ""}) is True
    assert ev("ISBLANK(F__c)", {"F__c": "x"}) is False


# ---------------------------------------------------------------------------
# B2 — empty-literal ISPICKVAL
# ---------------------------------------------------------------------------

def test_ispickval_empty_literal_always_refuses():
    for payload in ({"S__c": None}, {"S__c": ""}, {"S__c": "A"}, {}):
        r = ev('ISPICKVAL(S__c, "")', payload)
        assert isinstance(r, NonEvaluable)
        assert "blank-test" in r.reason


def test_ispickval_nonempty_literal_unaffected():
    assert ev('ISPICKVAL(S__c, "A")', {"S__c": "A"}) is True
    assert ev('ISPICKVAL(S__c, "A")', {"S__c": "B"}) is False


# ---------------------------------------------------------------------------
# Percent — fraction space, pinned to the live boundary
# ---------------------------------------------------------------------------

_PCT = {"PLS_BM_Discount__c": "percent"}


def test_percent_live_boundary_vr02():
    """The org's own boundary (D-442): staged 20 (=20% =0.20) does NOT
    exceed `> 0.20`; staged 20.01 does."""
    f = "PLS_BM_Discount__c > 0.20"
    assert ev(f, {"PLS_BM_Discount__c": 20},
              field_type_of=types(_PCT)) is False
    assert ev(f, {"PLS_BM_Discount__c": 20.01},
              field_type_of=types(_PCT)) is True


def test_percent_vr08_boundary():
    f = "PLS_BM_Discount__c > 0.25"
    assert ev(f, {"PLS_BM_Discount__c": 25.01},
              field_type_of=types(_PCT)) is True
    assert ev(f, {"PLS_BM_Discount__c": 25},
              field_type_of=types(_PCT)) is False


def test_percent_without_resolver_keeps_raw_compare():
    """No resolver / unknown type -> the raw comparison stands (pre-D-442
    behaviour; residual risk documented in the census)."""
    assert ev("PLS_BM_Discount__c > 0.20", {"PLS_BM_Discount__c": 20}) is True


def test_percent_field_vs_field_converts_each_side_independently():
    f = "A__c > B__c"
    ctx = types({"A__c": "percent", "B__c": "double"})
    # A=150 (percent -> 1.5) vs B=2.0 (double) -> False; raw would say True
    assert ev(f, {"A__c": 150, "B__c": 2.0}, field_type_of=ctx) is False


def test_percent_non_numeric_untouched():
    r = ev("PLS_BM_Discount__c > 0.20", {"PLS_BM_Discount__c": "x"},
           field_type_of=types(_PCT))
    assert isinstance(r, NonEvaluable)
