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


# ---------------------------------------------------------------------------
# D-203 — derive_update: the 2-step pair (setup = satisfy(False),
# violating changes = satisfy(True))
# ---------------------------------------------------------------------------

from primeqa.generation.verified_negative import (  # noqa: E402
    VerifiedUpdateNegative, derive_update,
)


def _du(formula):
    return derive_update(parse(formula))


def _pair(formula):
    r = _du(formula)
    assert isinstance(r, VerifiedUpdateNegative), f"{formula!r} -> {r!r}"
    return r.setup_payload, r.violating_changes


def _du_reason(formula):
    r = _du(formula)
    assert isinstance(r, NotDerivable), f"{formula!r} -> {r!r}"
    return r.reason


def test_update_numeric_comparisons_derive_both_directions():
    setup, violating = _pair("Amount > 1000000")
    assert violating == {"Amount": 1000001}
    assert setup == {"Amount": 1000000}          # <= bound: does not violate
    setup, violating = _pair("Discount__c >= 40")
    assert violating == {"Discount__c": 40}
    assert setup == {"Discount__c": 39}


def test_update_equality_derives_both_directions():
    setup, violating = _pair("Amount__c = 0")
    assert violating == {"Amount__c": 0}
    assert setup == {"Amount__c": 1}             # any value <> 0


def test_update_isblank_not_derivable():
    # Only one direction derives (no certain non-blank value) — the graded
    # fallback to create-rejected handles it upstream.
    assert "ISBLANK" in _du_reason("ISBLANK(Reason__c)")


def test_update_ispickval_not_derivable():
    assert "ISPICKVAL" in _du_reason('ISPICKVAL(StageName, "Closed Won")')


def test_update_org_state_functions_pre_scanned_out():
    assert "ISCHANGED" in _du_reason("ISCHANGED(StageName)")
    assert "PRIORVALUE" in _du_reason('PRIORVALUE(StageName) = "Closed Won"')


def test_update_cross_object_pre_scanned_out():
    assert "cross-object" in _du_reason('Account.Industry = "Banking"')


def test_update_compound_and_merges_both_sides():
    setup, violating = _pair("Amount > 100 && Quantity__c > 5")
    assert violating == {"Amount": 101, "Quantity__c": 6}
    # NOT(AND) = OR(NOT ops): the first derivable disjunct suffices for setup.
    assert setup == {"Amount": 100}


# ---------------------------------------------------------------------------
# D-294 — cross-field comparison derives with numeric field metadata. Metadata-
# free field-to-field still refuses (the pre-D-294 bar). Correctness is
# construction-proof: `evaluate` is NonEvaluable for field-to-field, so the
# ordered pair satisfies the operator by construction.
# ---------------------------------------------------------------------------

_XF = {"Loan__c": {"field_type": "currency", "is_calculated": False},
       "Property__c": {"field_type": "double", "is_calculated": False}}


def test_cross_field_derives_ordered_pair_with_numeric_metadata():
    r = derive(parse("Loan__c > Property__c"), _XF)
    assert isinstance(r, VerifiedNegative)
    assert r.violating_payload == {"Loan__c": 1, "Property__c": 0}   # 1 > 0 fires
    assert derive(parse("Loan__c < Property__c"), _XF).violating_payload \
        == {"Loan__c": 0, "Property__c": 1}


def test_cross_field_update_pair_nonviolating_setup_then_violating():
    r = derive_update(parse("Loan__c > Property__c"), _XF)
    assert isinstance(r, VerifiedUpdateNegative)
    assert r.setup_payload == {"Loan__c": 0, "Property__c": 1}       # 0 > 1 FALSE (setup)
    assert r.violating_changes == {"Loan__c": 1, "Property__c": 0}   # 1 > 0 TRUE (fires)


def test_cross_field_refuses_without_metadata():
    # the pre-D-294 behaviour — metadata-free field-to-field still refuses (the bar).
    r = derive(parse("Loan__c > Property__c"))
    assert isinstance(r, NotDerivable) and "field-to-field" in r.reason


def test_cross_field_refuses_non_numeric_operand():
    meta = {"A__c": {"field_type": "text"}, "B__c": {"field_type": "currency"}}
    assert "non-numeric" in derive(parse("A__c > B__c"), meta).reason


def test_cross_field_refuses_calculated_field():
    meta = {"A__c": {"field_type": "currency", "is_calculated": True},
            "B__c": {"field_type": "currency", "is_calculated": False}}
    # calculated -> non-writable -> refuse (unified writability guard)
    assert "non-writable" in derive(parse("A__c > B__c"), meta).reason


def test_cross_field_dotted_ref_is_prescanned_cross_object():
    # a self-qualified formula is a DOTTED ref -> pre-scanned as cross-object
    # (NotDerivable) before the cross-field branch; cross-field is bare-bare only.
    meta = {"Loan__c": {"field_type": "currency"}, "Property__c": {"field_type": "currency"}}
    r = derive(parse("Opportunity.Loan__c > Opportunity.Property__c"), meta)
    assert isinstance(r, NotDerivable) and "cross-object" in r.reason


# ---------------------------------------------------------------------------
# D-294 — NOT(ISBLANK) ("must be empty") + bare boolean derive with field
# metadata; metadata-free still refuses (the pre-D-294 bar). Each metadata-backed
# payload is evaluate-self-checked (polarity) before it is returned.
# ---------------------------------------------------------------------------

_TXT = {"Reason__c": {"field_type": "text", "length": 255}}
_BOOL = {"KYC_Done__c": {"field_type": "boolean"}}


def test_not_isblank_derives_nonblank_value_with_metadata():
    assert derive(parse("NOT(ISBLANK(Reason__c))"), _TXT).violating_payload \
        == {"Reason__c": "PQA"}
    # a numeric field -> a non-zero (unambiguously non-blank) value
    assert derive(parse("NOT(ISBLANK(Amt__c))"),
                  {"Amt__c": {"field_type": "currency"}}).violating_payload == {"Amt__c": 1}


def test_not_isblank_update_pair_setup_blank_violate_nonblank():
    # 2-step: setup blank (does NOT fire the must-be-empty rule) -> update non-blank (fires)
    r = derive_update(parse("NOT(ISBLANK(Reason__c))"), _TXT)
    assert isinstance(r, VerifiedUpdateNegative)
    assert r.setup_payload == {"Reason__c": None}
    assert r.violating_changes == {"Reason__c": "PQA"}


# ---------------------------------------------------------------------------
# D-296 — the compound cross-field / NOT-ISBLANK reconciliation (soft merge).
# The real env-59 Loan_Exceeds_Property_Value shape:
#   AND(NOT(ISBLANK(a)), NOT(ISBLANK(b)), a > b)
# was NotDerivable pre-D-296 (the two NOT-ISBLANK fillers `1` collided with the
# cross-field ordered pair `(1, 0)` in _merge); the _SoftFill soft-merge now
# lets the hard non-blank cross-field value subsume the soft NOT-ISBLANK filler.
# Metadata keyed by the VERBATIM mixed-case field name (the derive side is uncased).
# ---------------------------------------------------------------------------

_EXCEEDS = ("AND(NOT(ISBLANK(Loan_Amount__c)), NOT(ISBLANK(Property_Value__c)), "
            "Loan_Amount__c > Property_Value__c)")
_XF_COMPOUND = {
    "Loan_Amount__c": {"field_type": "currency", "is_calculated": False},
    "Property_Value__c": {"field_type": "currency", "is_calculated": False},
}


def test_compound_cross_field_notisblank_derives():
    r = derive(parse(_EXCEEDS), _XF_COMPOUND)
    assert isinstance(r, VerifiedNegative), r
    # 1 > 0 fires the rule; both fields non-blank (0 is a non-blank number).
    assert r.violating_payload == {"Loan_Amount__c": 1, "Property_Value__c": 0}


def test_compound_cross_field_no_softfill_escapes():
    from primeqa.generation.verified_negative import _SoftFill
    payload = derive(parse(_EXCEEDS), _XF_COMPOUND).violating_payload
    assert not any(isinstance(v, _SoftFill) for v in payload.values())


def test_compound_cross_field_update_pair_derives():
    from primeqa.generation.verified_negative import _SoftFill
    r = derive_update(parse(_EXCEEDS), _XF_COMPOUND)
    assert isinstance(r, VerifiedUpdateNegative), r
    assert r.violating_changes == {"Loan_Amount__c": 1, "Property_Value__c": 0}
    assert not any(isinstance(v, _SoftFill) for v in r.setup_payload.values())
    assert not any(isinstance(v, _SoftFill) for v in r.violating_changes.values())


def test_compound_post_merge_value_satisfies_not_isblank():
    # the EMITTED Property_Value = 0 (the cross-field value, not the filler 1) must
    # still satisfy NOT(ISBLANK(...)) — the polarity holds on the merged value.
    from primeqa.semantic.formula import evaluate
    assert evaluate(parse("NOT(ISBLANK(Property_Value__c))"),
                    {"Property_Value__c": 0}) is True


def test_compound_cross_field_refuses_without_metadata():
    # no metadata -> the cross-field conjunct can't synthesize -> NotDerivable (bar).
    assert isinstance(derive(parse(_EXCEEDS)), NotDerivable)


def test_notisblank_and_isblank_same_field_conflict_still_refuses():
    # a genuine contradiction (must-be-nonblank AND must-be-blank on one field) is a
    # blank hard vs a soft non-blank -> still conflicts -> NotDerivable (refuse floor).
    r = derive(parse("AND(NOT(ISBLANK(A__c)), ISBLANK(A__c))"),
               {"A__c": {"field_type": "currency"}})
    assert isinstance(r, NotDerivable)


def test_bare_boolean_derives_true_with_metadata():
    assert derive(parse("KYC_Done__c"), _BOOL).violating_payload == {"KYC_Done__c": True}
    assert derive(parse("NOT(KYC_Done__c)"), _BOOL).violating_payload == {"KYC_Done__c": False}


def test_conditional_must_be_empty_compound_merges():
    p = derive(parse('AND(ISPICKVAL(Stage__c,"Closed"), NOT(ISBLANK(Reason__c)))'),
               _TXT).violating_payload
    assert p == {"Stage__c": "Closed", "Reason__c": "PQA"}


def test_not_isblank_and_bare_boolean_refuse_without_metadata():
    assert isinstance(derive(parse("NOT(ISBLANK(Reason__c))")), NotDerivable)
    assert isinstance(derive(parse("KYC_Done__c")), NotDerivable)


def test_not_isblank_refuses_unsynthesizable_type():
    r = derive(parse("NOT(ISBLANK(Tags__c))"), {"Tags__c": {"field_type": "multipicklist"}})
    assert isinstance(r, NotDerivable) and "non-blank" in r.reason


def test_bare_field_non_boolean_still_refuses():
    r = derive(parse("Amount__c"), {"Amount__c": {"field_type": "currency"}})
    assert isinstance(r, NotDerivable) and "bare field" in r.reason


# ---------------------------------------------------------------------------
# D-294 — NOT(ISPICKVAL) ("field must be X") derives an alternative active
# picklist value with metadata; metadata-free / single-value / inline refuses.
# ---------------------------------------------------------------------------

_PK = {"Stage__c": {"field_type": "picklist",
                    "picklist_values": ("Prospecting", "Closed", "Won")}}


def test_not_ispickval_derives_alternative_value():
    # NOT(ISPICKVAL(Stage,"Closed")) fires when Stage != "Closed" -> pick an alt.
    r = derive(parse('NOT(ISPICKVAL(Stage__c,"Closed"))'), _PK)
    assert r.violating_payload == {"Stage__c": "Prospecting"}   # first active != forbidden


def test_not_ispickval_refuses_without_metadata():
    assert isinstance(derive(parse('NOT(ISPICKVAL(Stage__c,"Closed"))')), NotDerivable)


def test_not_ispickval_refuses_when_only_forbidden_value():
    r = derive(parse('NOT(ISPICKVAL(S__c,"X"))'),
               {"S__c": {"field_type": "picklist", "picklist_values": ("X",)}})
    assert isinstance(r, NotDerivable) and "picklist" in r.reason


def test_not_ispickval_refuses_inline_picklist_none():
    # an inline picklist S1 did not capture -> picklist_values None -> refuse.
    r = derive(parse('NOT(ISPICKVAL(S__c,"X"))'),
               {"S__c": {"field_type": "picklist", "picklist_values": None}})
    assert isinstance(r, NotDerivable)


def test_plain_ispickval_unchanged_with_metadata():
    # the already-derivable positive shape is untouched by the metadata rail.
    assert derive(parse('ISPICKVAL(Stage__c,"Closed")'), _PK).violating_payload \
        == {"Stage__c": "Closed"}


# ---------------------------------------------------------------------------
# D-294 writability guard — a violating payload can only SET a writable field.
# is_calculated (formula/rollup) or neither-createable-nor-updateable refuses for
# ALL four shapes (not just cross-field); create-only / update-only still derive.
# ---------------------------------------------------------------------------

def test_all_shapes_refuse_calculated_field():
    assert isinstance(derive(parse("NOT(ISBLANK(F__c))"),
        {"F__c": {"field_type": "text", "is_calculated": True}}), NotDerivable)
    assert isinstance(derive(parse("Flag__c"),
        {"Flag__c": {"field_type": "boolean", "is_calculated": True}}), NotDerivable)
    assert isinstance(derive(parse('NOT(ISPICKVAL(S__c,"X"))'),
        {"S__c": {"field_type": "picklist", "picklist_values": ("X", "Y"),
                  "is_calculated": True}}), NotDerivable)
    assert isinstance(derive(parse("A__c > B__c"),
        {"A__c": {"field_type": "currency", "is_calculated": True},
         "B__c": {"field_type": "currency"}}), NotDerivable)


def test_shapes_refuse_field_neither_createable_nor_updateable():
    nw = {"field_type": "text", "is_createable": False, "is_updateable": False}
    assert isinstance(derive(parse("NOT(ISBLANK(F__c))"), {"F__c": nw}), NotDerivable)
    # create-only (or update-only) is still writable -> derives (no over-refusal)
    assert isinstance(derive(parse("NOT(ISBLANK(F__c))"),
        {"F__c": {"field_type": "text", "is_createable": True, "is_updateable": False}}),
        VerifiedNegative)
