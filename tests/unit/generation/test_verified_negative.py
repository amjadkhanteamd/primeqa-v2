"""Unit tests for violating-value derivation (D-107, slice 3). Pure — no DB.

The derivable / not-derivable boundary IS the verified-vs-caveated line (slice 4):
each derivable shape yields the correct violating payload; everything where
create-time / single-object certainty fails yields NotDerivable(reason).
"""
from __future__ import annotations

from primeqa.generation.verified_negative import (
    NotDerivable, VerifiedNegative, derive,
)
from primeqa.semantic.formula import parse


def _d(formula):
    return derive(parse(formula))


def _payload(formula):
    r = _d(formula)
    assert isinstance(r, VerifiedNegative), f"{formula!r} -> {r!r}"
    return r.violating_payload


def _reason(formula):
    r = _d(formula)
    assert isinstance(r, NotDerivable), f"{formula!r} -> {r!r}"
    assert r.reason
    return r.reason


# ---------------------------------------------------------------------------
# Derivable
# ---------------------------------------------------------------------------

def test_isblank_to_blank():
    assert _payload("ISBLANK(Reason__c)") == {"Reason__c": None}
    assert _payload("ISNULL(Reason__c)") == {"Reason__c": None}


def test_numeric_comparisons():
    assert _payload("Amount < 0") == {"Amount": -1}
    assert _payload("Amount <= 0") == {"Amount": 0}
    assert _payload("Amount > 100") == {"Amount": 101}
    assert _payload("Amount >= 100") == {"Amount": 100}
    assert _payload("Amount = 5") == {"Amount": 5}
    assert _payload("Amount <> 0") == {"Amount": 1}


def test_literal_field_order_flips_op():
    # 0 > Amount  <=>  Amount < 0  -> -1
    assert _payload("0 > Amount") == {"Amount": -1}


def test_ispickval():
    assert _payload('ISPICKVAL(StageName, "Closed Won")') == {"StageName": "Closed Won"}


def test_string_and_boolean_equality():
    assert _payload('Status__c <> "Open"') == {"Status__c": "Open_x"}
    assert _payload('Status__c = "Open"') == {"Status__c": "Open"}
    assert _payload("Flag__c = TRUE") == {"Flag__c": True}
    assert _payload("Flag__c <> TRUE") == {"Flag__c": False}


def test_and_merges_multi_field():
    p = _payload('AND(ISPICKVAL(StageName, "Closed Lost"), ISBLANK(Loss_Reason__c))')
    assert p == {"StageName": "Closed Lost", "Loss_Reason__c": None}


def test_or_picks_one_derivable_disjunct():
    assert _payload("Amount < 0 || ISBLANK(R__c)") == {"Amount": -1}


def test_not_inverts_comparison():
    # NOT(Amount < 0) -> Amount >= 0 -> 0
    assert _payload("NOT(Amount < 0)") == {"Amount": 0}


def test_not_and_demorgan_picks_derivable():
    # NOT(AND(Amount<0, ISBLANK(R))) = OR(NOT(Amount<0), NOT(ISBLANK)) -> first derivable
    assert _payload("NOT(AND(Amount < 0, ISBLANK(R__c)))") == {"Amount": 0}


# ---------------------------------------------------------------------------
# NotDerivable (the verified bar / caveated-fallback trigger)
# ---------------------------------------------------------------------------

def test_field_to_field_not_derivable():
    assert "field-to-field" in _reason("Amount < Other__c")


def test_org_state_functions_not_derivable():
    assert "org-state" in _reason("Amount <> PRIORVALUE(Amount)")
    assert "org-state" in _reason("ISCHANGED(OwnerId)")
    assert "org-state" in _reason("ISNEW()")


def test_cross_object_not_derivable():
    assert "cross-object" in _reason('Account.Industry = "Tech"')


def test_negated_blank_and_pickval_not_derivable():
    assert "non-blank" in _reason("NOT(ISBLANK(R__c))")
    assert "picklist" in _reason('NOT(ISPICKVAL(S__c, "v"))')


def test_non_numeric_ordering_not_derivable():
    assert "ordering" in _reason('Name < "M"')


def test_conflicting_compound_not_derivable():
    assert "conflicting" in _reason("Amount = 0 && Amount = 5")


def test_bare_field_not_derivable():
    assert "bare field" in _reason("IsLocked__c")


def test_not_parsed_not_derivable():
    # parser fail-loud -> derive returns NotDerivable (slice-4 caveated fallback)
    assert _reason('REGEX(Name, "[0-9]+")') == "formula not parsed"


def test_not_or_requires_all_disjuncts_not_derivable():
    # NOT(OR(Amount<0, ISBLANK(R))) = AND(NOT(Amount<0), NOT(ISBLANK)) -> the
    # NOT(ISBLANK) leg is undecidable -> the whole merge is NotDerivable.
    assert isinstance(_d("NOT(Amount < 0 || ISBLANK(R__c))"), NotDerivable)
