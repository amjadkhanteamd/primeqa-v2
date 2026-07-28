"""D-412 value-membership validator — every verdict branch pinned.

The three-verdict contract (D-399.1) is the thing under test: a capture gap
must yield CANNOT_VALIDATE, never INVALID — a false refusal is silent, a
wrong-red is loud, and the validator must never trade the loud failure for
the silent one.
"""
from __future__ import annotations

import pytest

from primeqa.generation.value_membership import (
    FieldCaptureIndex,
    ValueMembershipError,
    Verdict,
    _FieldCapture,
    extract_field_literals,
)


def _index(**fields):
    return FieldCaptureIndex({k: v for k, v in fields.items()})


def _fc(cap, values=(), ft="picklist"):
    return _FieldCapture(field_type=ft, capture=cap, values=tuple(values))


LOAN = {"Opportunity.Loan_Type__c": _fc("inline", [
    ("Home", "Home", True), ("Personal", "Personal", True),
    ("Business", "Business", True),
])}


# ---------------------------------------------------------------- VALID --

def test_valid_on_api_name_match():
    idx = _index(**LOAN)
    [c] = idx.check_literal("Opportunity.Loan_Type__c", "Home")
    assert c.verdict == Verdict.VALID and c.detail == "api_name"


def test_valid_on_label_only_match_and_reports_it():
    """api 'BestCase' / label 'Best Case': a literal quoting the LABEL is a
    member — but the check must say it matched on the label, because the
    transport payload may still need the api-name spelling."""
    idx = _index(**{"Opportunity.ForecastCategory": _fc("inline_standard", [
        ("BestCase", "Best Case", True),
    ])})
    [c] = idx.check_literal("Opportunity.ForecastCategory", "Best Case")
    assert c.verdict == Verdict.VALID and c.detail == "label"


# -------------------------------------------------------------- INVALID --

def test_invalid_absent_is_the_hallucination_verdict():
    idx = _index(**LOAN)
    [c] = idx.check_literal("Opportunity.Loan_Type__c", "Home Loan")
    assert c.verdict == Verdict.INVALID and c.detail == "absent"


def test_invalid_inactive_is_org_drift_not_hallucination():
    """Present-but-inactive is a DIFFERENT verdict detail from absent: the
    value existed and the org retired it (drift — org owner's problem), vs a
    value that never existed (hallucination — generation's problem). The two
    must never collapse."""
    idx = _index(**{"Case.Status": _fc("inline_standard", [
        ("New", "New", True), ("Retired", "Retired", False),
    ])})
    [c] = idx.check_literal("Case.Status", "Retired")
    assert c.verdict == Verdict.INVALID and c.detail == "inactive"


def test_no_values_refuses_every_literal():
    """no_values asserts a known-EMPTY set (org-verified honest absence,
    D-408) — a member of the empty set does not exist, so refusal is correct
    and it is NOT a capture gap."""
    idx = _index(**{"Location.LocationType": _fc("no_values")})
    [c] = idx.check_literal("Location.LocationType", "Warehouse")
    assert c.verdict == Verdict.INVALID and c.detail == "absent"


# ------------------------------------------------------ CANNOT_VALIDATE --

def test_truncated_capture_never_refuses():
    """inline_truncated stores a disclosed SUBSET (200-cap): 'not in the
    stored 200' cannot distinguish 'org lacks it' from 'we truncated it
    away'. Refusing here would be a silent false refusal — the exact
    inversion D-399.1 forbids."""
    idx = _index(**{"User.TimeZoneSidKey": _fc("inline_truncated", [
        ("Pacific/Apia", "(GMT+13:00) Apia", True),
    ])})
    # A value that IS NOT in the stored subset — still no refusal.
    [c] = idx.check_literal("User.TimeZoneSidKey", "Mars/Olympus")
    assert c.verdict == Verdict.CANNOT_VALIDATE
    assert c.detail == "inline_truncated"


def test_null_capture_is_pre_migration_draw_no_conclusion():
    idx = _index(**{"Account.Industry": _fc(None, [
        ("Banking", "Banking", True),
    ])})
    # Even a literal PRESENT in stored values: NULL capture means the stored
    # rows themselves are unattested — no conclusion in either direction.
    [c] = idx.check_literal("Account.Industry", "Banking")
    assert c.verdict == Verdict.CANNOT_VALIDATE and c.detail == "null_capture"


def test_unknown_future_mark_cannot_validate():
    idx = _index(**{"X.Y": _fc("from_some_future_source", [("A", "A", True)])})
    [c] = idx.check_literal("X.Y", "A")
    assert c.verdict == Verdict.CANNOT_VALIDATE
    assert c.detail == "from_some_future_source"


# ------------------------------------------------------------- scoping --

def test_unknown_field_is_out_of_jurisdiction():
    """Field grounding is the grounding validator's job; membership says
    nothing about fields it does not know."""
    assert _index(**LOAN).check_literal("Case.Priority", "High") == []


def test_none_and_bool_literals_are_skipped():
    idx = _index(**LOAN)
    assert idx.check_literal("Opportunity.Loan_Type__c", None) == []
    assert idx.check_literal("Opportunity.Loan_Type__c", True) == []


def test_multipicklist_splits_on_semicolon_per_part_verdicts():
    idx = _index(**{"Contact.BuyerAttributes": _fc(
        "inline_standard",
        [("Economic", "Economic", True), ("Technical", "Technical", True)],
        ft="multipicklist")})
    checks = idx.check_literal("Contact.BuyerAttributes",
                               "Economic; Imaginary")
    assert [(c.value, c.verdict) for c in checks] == [
        ("Economic", Verdict.VALID), ("Imaginary", Verdict.INVALID)]


# ---------------------------------------------------------- extraction --

def test_extracts_structured_shapes_not_prose():
    """field_values / field_changes maps, literal wrappers and subject/value
    condition nodes are extracted; prose sentences NEVER are — a value that
    exists only inside triggering_action.description must not surface."""
    claim = {
        "triggering_action": {
            "description":
                "creating a Opportunity with Loan_Type__c='PROSE ONLY'"},
        "to_state": {"field_values": {"Opportunity.StageName": "Approved"}},
        "conds": [{"subject": {"entity_type": "Field",
                               "external_id": "Case.Priority"},
                   "value": "High", "predicate": "equals"}],
    }
    recipe = {"steps": [
        {"kind": "create",
         "field_values": {"PLS_FB_Order__c.PLS_FB_Status__c":
                          {"kind": "literal", "value": "Draft"}}},
        {"kind": "update",
         "field_changes": {"Opportunity.Amount": 500000}},
    ]}
    pairs = extract_field_literals(claim, recipe)
    assert ("Opportunity.StageName", "Approved") in pairs
    assert ("Case.Priority", "High") in pairs
    assert ("PLS_FB_Order__c.PLS_FB_Status__c", "Draft") in pairs
    assert ("Opportunity.Amount", 500000) in pairs
    assert not any("PROSE" in str(v) for _f, v in pairs)


def test_extraction_dedups_on_field_and_value():
    body = {"a": {"X.F": "V"}, "b": {"X.F": "V"}}
    assert extract_field_literals(body) == [("X.F", "V")]


# ------------------------------------------------------------ rollups --

def test_claim_rollup_invalid_dominates_then_cannot_validate():
    idx = _index(**LOAN,
                 **{"User.TimeZoneSidKey": _fc("inline_truncated")})
    r = idx.validate({"x": {"Opportunity.Loan_Type__c": "Home Loan",
                            "User.TimeZoneSidKey": "Mars/Olympus"}})
    assert r.verdict == Verdict.INVALID
    r2 = idx.validate({"x": {"Opportunity.Loan_Type__c": "Home",
                             "User.TimeZoneSidKey": "Mars/Olympus"}})
    assert r2.verdict == Verdict.CANNOT_VALIDATE
    r3 = idx.validate({"x": {"Opportunity.Loan_Type__c": "Home"}})
    assert r3.verdict == Verdict.VALID


def test_claim_with_no_enumerated_literals_is_vacuously_valid():
    assert _index(**LOAN).validate({"x": {"Opportunity.Amount": 1}}).verdict \
        == Verdict.VALID


# ----------------------------------------------------------- fail-loud --

class _BrokenConn:
    def execute(self, *_a, **_k):
        raise RuntimeError("column picklist_capture does not exist")


class _EmptyConn:
    class _R:
        def mappings(self):
            return self
        def all(self):
            return []
    def execute(self, *_a, **_k):
        return self._R()


def test_load_fails_loud_when_capture_unreadable():
    with pytest.raises(ValueMembershipError, match="cannot read capture"):
        FieldCaptureIndex.load(_BrokenConn(), "00000000-0000-0000-0000-0")


def test_load_fails_loud_on_zero_picklist_fields():
    """An empty index would validate everything vacuously — a silent
    wrong-green of the validator's own. Refuse instead."""
    with pytest.raises(ValueMembershipError, match="ZERO picklist fields"):
        FieldCaptureIndex.load(_EmptyConn(), "00000000-0000-0000-0000-0")
