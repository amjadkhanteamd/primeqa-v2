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
    assert _reason("VLOOKUP($X.Y, A, B)") == "formula not parsed"


def test_regex_positive_not_derivable():
    # D-344: a POSITIVE REGEX match needs true regex synthesis -> stays NotDerivable
    # (bounded on purpose; only the NON-match side derives).
    assert _reason('REGEX(Name, "[0-9]+")') == \
        "REGEX true (matching-value synthesis not derivable)"


def test_regex_non_match_derives_certain_nonblank_value():
    # D-344: NOT(ISBLANK(f)) && NOT(REGEX(f, pat)) fires for a NON-blank value that
    # does NOT match pat -> a CERTAIN violating value (re.fullmatch-verified).
    import re as _re
    f = 'NOT(ISBLANK(Ext__c)) && NOT(REGEX(Ext__c, "^EXT-[0-9]{8}$"))'
    meta = {"Ext__c": {"field_type": "string", "is_createable": True,
                       "is_updateable": True}}
    r = derive(parse(f), meta)
    assert isinstance(r, VerifiedNegative)
    v = r.violating_payload["Ext__c"]
    assert v and _re.fullmatch("^EXT-[0-9]{8}$", v) is None     # non-blank + non-match


def test_regex_non_match_refuses_without_metadata():
    # certainty bar: no field metadata -> refuse (mirrors NOT-ISBLANK).
    f = 'NOT(ISBLANK(Ext__c)) && NOT(REGEX(Ext__c, "^EXT-[0-9]{8}$"))'
    assert isinstance(derive(parse(f)), NotDerivable)


def test_regex_match_everything_pattern_refuses():
    # NOT(REGEX(f, ".*")) can never fire (everything matches) -> NotDerivable.
    r = derive(parse('NOT(REGEX(F__c, ".*"))'),
               {"F__c": {"field_type": "string", "is_createable": True}})
    assert isinstance(r, NotDerivable) and "non-matching" in r.reason


# --- D-348: the RecordType gate (VR08) --------------------------------------

from primeqa.generation.verified_negative import _RECORD_TYPES_KEY   # noqa: E402


def test_recordtype_ref_derives_recordtypeid():
    # RecordType.DeveloperName = "X" -> SET RecordTypeId to X's Id (resolved from
    # the __record_types__ rail); the record then IS record type X.
    f = 'RecordType.DeveloperName = "Enterprise" && Discount__c > 0.25'
    r = derive(parse(f), {_RECORD_TYPES_KEY: {"Enterprise": "012ENT"}})
    assert isinstance(r, VerifiedNegative)
    assert r.violating_payload.get("RecordTypeId") == "012ENT"


def test_recordtype_ref_unknown_devname_refuses():
    f = 'RecordType.DeveloperName = "Missing" && Discount__c > 0.25'
    assert isinstance(
        derive(parse(f), {_RECORD_TYPES_KEY: {"Enterprise": "012ENT"}}), NotDerivable)


def test_recordtype_ref_without_rail_refuses():
    # no record-types rail -> can't resolve the Id -> certainty bar refuses.
    f = 'RecordType.DeveloperName = "Enterprise" && Discount__c > 0.25'
    assert isinstance(derive(parse(f), {}), NotDerivable)


def test_other_cross_object_ref_still_blocked():
    # only RecordType.DeveloperName is special-cased; any other related-record ref
    # stays a non-derivable cross-object read.
    assert isinstance(derive(parse('Account__r.Name = "x"'), {}), NotDerivable)


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


# ---------------------------------------------------------------------------
# D-300: derive_boundary_set — the single-threshold-numeric boundary pair
# (firing + just-inside), DORMANT (no live call site until D-300 S3).
# ---------------------------------------------------------------------------

from primeqa.generation.verified_negative import (  # noqa: E402
    BoundaryMember, derive_boundary_set,
)


def _bset(formula):
    return derive_boundary_set(parse(formula))


def test_boundary_set_threshold_op_value_table():
    # (formula, firing value, just-inside value) for the four threshold ops.
    table = [
        ("Amount > 10000", 10001, 10000),
        ("Amount >= 10000", 10000, 9999),
        ("Amount < 10000", 9999, 10000),
        ("Amount <= 10000", 10000, 10001),
    ]
    for formula, fire, inside in table:
        members = _bset(formula)
        assert len(members) == 2, formula
        firing, just_inside = members
        assert firing == BoundaryMember(
            payload={"Amount": fire}, expect_reject=True, edge="firing"), formula
        assert just_inside == BoundaryMember(
            payload={"Amount": inside}, expect_reject=False,
            edge="just-inside"), formula


def test_boundary_set_literal_field_order_flips_op():
    # 10000 < Amount  <=>  Amount > 10000 -> the same pair
    assert _bset("10000 < Amount") == _bset("Amount > 10000")


def test_boundary_firing_member_equals_derive_payload():
    # The D-300 drift self-check: member[0] IS the primary's payload — the
    # generator and the shipped primary derivation can never diverge silently.
    for formula in ("Amount > 10000", "Amount >= 10000",
                    "Amount < 10000", "Amount <= 10000"):
        members = _bset(formula)
        primary = derive(parse(formula))
        assert isinstance(primary, VerifiedNegative), formula
        assert members[0].payload == primary.violating_payload, formula


def test_boundary_set_refuses_non_threshold_shapes():
    # Equality ops are point constraints — no threshold adjacency.
    assert _bset("Amount = 5") == ()
    assert _bset("Amount <> 0") == ()
    # String/boolean literals: no numeric adjacency.
    assert _bset('Status__c = "Open"') == ()
    # Or / Not compounds: no uniquely-gating threshold.
    assert _bset("OR(Amount > 100, ISBLANK(Reason__c))") == ()
    assert _bset("NOT(Amount > 100)") == ()
    # Function shapes.
    assert _bset("ISBLANK(Reason__c)") == ()
    # Cross-field: no literal to be adjacent to (with or without metadata).
    assert _bset("Loan__c > Property__c") == ()
    assert derive_boundary_set(
        parse("Loan__c > Property__c"),
        {"Loan__c": {"field_type": "currency"},
         "Property__c": {"field_type": "currency"}}) == ()


# ---------------------------------------------------------------------------
# D-328: compound-AND boundary — ONE threshold conjunct + gates staged TRUE
# in both probes.
# ---------------------------------------------------------------------------

def test_boundary_compound_and_stages_gates_in_both_probes():
    # AND(gate, threshold) — the gate rides BOTH payloads, so the just-inside
    # accept is meaningful (gates true; only the value is inside).
    members = _bset("AND(Amount > 100, ISBLANK(Reason__c))")
    assert len(members) == 2
    firing, inside = members
    assert firing.payload == {"Reason__c": None, "Amount": 101}
    assert firing.expect_reject is True and firing.edge == "firing"
    assert inside.payload == {"Reason__c": None, "Amount": 100}
    assert inside.expect_reject is False and inside.edge == "just-inside"


def test_boundary_compound_block_approved_shape():
    # The req-302 ₹50,00,000 approval VR shape: picklist gate + threshold +
    # NOT-ISPICKVAL gate (needs the D-294 picklist rail for the alternative).
    formula = ('AND(ISPICKVAL(StageName, "Approved"), Loan_Amount__c > 5000000, '
               'NOT(ISPICKVAL(Approval_Status__c, "Approved")))')
    meta = {"Approval_Status__c": {"field_type": "picklist",
                                   "picklist_values": ["Pending", "Approved"]}}
    members = derive_boundary_set(parse(formula), meta)
    assert len(members) == 2
    firing, inside = members
    assert firing.payload == {"StageName": "Approved",
                              "Approval_Status__c": "Pending",
                              "Loan_Amount__c": 5000001}
    assert inside.payload == {"StageName": "Approved",
                              "Approval_Status__c": "Pending",
                              "Loan_Amount__c": 5000000}
    assert firing.expect_reject and not inside.expect_reject


def test_boundary_compound_firing_member_equals_derive_payload():
    # The D-300 drift self-check holds on the compound shape too.
    formula = "AND(ISBLANK(Reason__c), Amount >= 10)"
    members = _bset(formula)
    primary = derive(parse(formula))
    assert isinstance(primary, VerifiedNegative)
    assert members[0].payload == primary.violating_payload


def test_boundary_compound_soft_gate_yields_to_threshold_field():
    # A NOT-ISBLANK gate on the SAME field as the threshold: the soft fill
    # yields to the hard boundary values (D-296 merge semantics).
    meta = {"Loan__c": {"field_type": "currency"}}
    members = derive_boundary_set(
        parse("AND(NOT(ISBLANK(Loan__c)), Loan__c > 5000)"), meta)
    assert len(members) == 2
    assert members[0].payload == {"Loan__c": 5001}
    assert members[1].payload == {"Loan__c": 5000}


def test_boundary_compound_refusals():
    # Zero threshold conjuncts.
    assert _bset("AND(ISBLANK(A__c), ISBLANK(B__c))") == ()
    # Two threshold conjuncts — ambiguous, refuse.
    assert _bset("AND(Amount > 100, Loan__c > 5)") == ()
    # An underivable gate refuses the whole set (no picklist rail here).
    assert _bset('AND(NOT(ISPICKVAL(Status__c, "Open")), Amount > 100)') == ()
    # A gate conflicting with the threshold field (equality pin vs boundary
    # values) refuses via the merge.
    assert _bset("AND(Amount = 5, Amount > 100)") == ()
    # Fractional threshold inside a compound refuses (integers only).
    assert _bset("AND(ISBLANK(A__c), Amount > 10.5)") == ()


def test_boundary_set_refuses_pre_scan_gates():
    # The same _pre_scan gates as derive(): org-state funcs, dotted refs,
    # unparsed formulas.
    assert _bset("PRIORVALUE(Amount) > 100") == ()
    assert _bset("Account.Amount__c > 100") == ()
    assert derive_boundary_set(parse("REGEX(Phone, '[0-9]+')")) == ()
    assert derive_boundary_set(None) == ()


def test_boundary_set_refuses_fractional_literals():
    # D-300 review-fix: ±1 adjacency on a fractional literal is unsound
    # (float artifacts + field-scale rounding can make the just-inside value
    # actually fire the VR) — integers only until scale-aware adjacency.
    assert _bset("Amount > 10000.5") == ()
    assert _bset("Amount <= 99.99") == ()
    # integers still arm
    assert len(_bset("Amount > 10000")) == 2
