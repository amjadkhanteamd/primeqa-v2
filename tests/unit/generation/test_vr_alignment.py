"""Pure unit tests for D-295 VR-to-claim alignment (no PG, no LLM).

The scorer selects the ONE validation rule whose fields match a prohibition
claim's grounded conditions, so each prohibition grounds on its OWN rule rather
than the first-derivable generic VR on a multi-VR object (the req-302 defect:
both AC1 and AC4 grounded on the generic ``Amount > 10000``). The VR formulas
below are the REAL env-59 Opportunity rules for req-302 (fetched from prod
tenant_1), so these tests prove selection on the actual business rules.

**Slice 2 (armed):** ``_align_vr_to_conditions`` narrows a multi-VR candidate set
to the aligned VR, or returns ``()`` (no rule matches → the D-293 gate refuses).
Condition-free / single-VR (or zero-VR) sets pass through unchanged (the
degenerate guard), so those cases stay byte-identical to pre-D-295.
"""
from __future__ import annotations

from primeqa.generation import governance_core as gc
from primeqa.generation.emission import _Endpoint, _GroundedCondition

# --- the REAL env-59 Opportunity validation rules (req-302) -----------------
VR_AMOUNT = "Amount  > 10000"
VR_BLOCK_APPROVED = (
    'AND(\n  ISPICKVAL(StageName, "Approved"),\n'
    "  Loan_Amount__c > 5000000,\n"
    '  NOT(ISPICKVAL(Approval_Status__c, "Approved"))\n)'
)
VR_CREDIT_ASSESSMENT = (
    'AND(\n  ISPICKVAL(StageName, "Credit Assessment"),\n'
    "  OR(\n    NOT(KYC_Complete__c),\n    ISBLANK(Credit_Score__c)\n  )\n)"
)
VR_HOME_LOAN = (
    'AND(\n  ISPICKVAL(Loan_Type__c, "Home"),\n'
    "  OR(\n    ISBLANK(Loan_Amount__c),\n    ISBLANK(Property_Value__c),\n"
    "    ISBLANK(Annual_Income__c)\n  )\n)"
)
VR_LOAN_EXCEEDS = (
    "AND(\n  NOT(ISBLANK(Loan_Amount__c)),\n  NOT(ISBLANK(Property_Value__c)),\n"
    "  Loan_Amount__c > Property_Value__c\n)"
)

# neighborhood order the derivation would see (incidental S1 traversal order)
ALL_VRS = (
    VR_AMOUNT, VR_BLOCK_APPROVED, VR_CREDIT_ASSESSMENT, VR_HOME_LOAN, VR_LOAN_EXCEEDS,
)


def _cond(qualified_field: str, predicate: str = "equals",
          value="x") -> _GroundedCondition:
    """A grounded condition on ``Object.Field`` (object-qualified external_id,
    as governance builds them)."""
    return _GroundedCondition(
        field=_Endpoint(entity_id=1, entity_type="Field", external_id=qualified_field),
        predicate=predicate, value=value)


# --- _vr_formula_fields (the RIGHT side of the overlap) ---------------------

def test_vr_formula_fields_extraction_on_real_rules():
    assert gc._vr_formula_fields(VR_AMOUNT) == frozenset({"amount"})
    assert gc._vr_formula_fields(VR_BLOCK_APPROVED) == frozenset(
        {"stagename", "loan_amount__c", "approval_status__c"})
    assert gc._vr_formula_fields(VR_CREDIT_ASSESSMENT) == frozenset(
        {"stagename", "kyc_complete__c", "credit_score__c"})
    assert gc._vr_formula_fields(VR_HOME_LOAN) == frozenset(
        {"loan_type__c", "loan_amount__c", "property_value__c", "annual_income__c"})
    assert gc._vr_formula_fields(VR_LOAN_EXCEEDS) == frozenset(
        {"loan_amount__c", "property_value__c"})


def test_vr_formula_fields_recurses_into_functions():
    # ISBLANK / ISPICKVAL / NOT wrap the FieldRef; walk must surface it.
    assert gc._vr_formula_fields("ISBLANK(Foo__c)") == frozenset({"foo__c"})
    assert gc._vr_formula_fields('NOT(ISPICKVAL(Bar__c, "X"))') == frozenset({"bar__c"})


def test_vr_formula_fields_unparseable_is_empty():
    # An unparseable formula contributes NO fields (zero overlap → downstream refuse),
    # never a crash — parse() returns NotParsed, never raises.
    assert gc._vr_formula_fields("REGEX(Name, '[0-9]+') && CASE(x,1,2,3)") == frozenset()
    assert gc._vr_formula_fields("") == frozenset()


def test_vr_formula_fields_dotted_takes_bare_tail():
    # path[-1] — a cross-object dotted ref reduces to its bare tail (a spurious
    # same-name match only refuses downstream, never greens a wrong-rule test).
    assert gc._vr_formula_fields("ISBLANK(Account.Industry)") == frozenset({"industry"})


# --- _claim_condition_fields (the LEFT side of the overlap) -----------------

def test_claim_condition_fields_strips_object_prefix_and_lowercases():
    conds = [_cond("Opportunity.Loan_Type__c"), _cond("Opportunity.Loan_Amount__c")]
    assert gc._claim_condition_fields(conds) == frozenset(
        {"loan_type__c", "loan_amount__c"})


def test_claim_condition_fields_empty():
    assert gc._claim_condition_fields([]) == frozenset()
    assert gc._claim_condition_fields(None) == frozenset()


# --- _best_aligned_vr (the selection core) ----------------------------------

def test_best_aligned_ac1_picks_home_loan():
    # AC1 conditions (Loan_Type + Loan_Amount): Home_Loan scores 2, Loan_Exceeds 1.
    conds = [_cond("Opportunity.Loan_Type__c"), _cond("Opportunity.Loan_Amount__c")]
    assert gc._best_aligned_vr(ALL_VRS, conds) == VR_HOME_LOAN


def test_best_aligned_ac4_picks_credit_assessment():
    conds = [_cond("Opportunity.StageName"), _cond("Opportunity.KYC_Complete__c")]
    assert gc._best_aligned_vr(ALL_VRS, conds) == VR_CREDIT_ASSESSMENT


def test_best_aligned_ac9_picks_block_approved():
    conds = [_cond("Opportunity.Loan_Amount__c"),
             _cond("Opportunity.Approval_Status__c"), _cond("Opportunity.StageName")]
    assert gc._best_aligned_vr(ALL_VRS, conds) == VR_BLOCK_APPROVED


def test_best_aligned_never_the_generic_amount_rule():
    # The whole point: no business claim should ground on Amount > 10000.
    for conds in (
        [_cond("Opportunity.Loan_Type__c"), _cond("Opportunity.Loan_Amount__c")],
        [_cond("Opportunity.StageName"), _cond("Opportunity.KYC_Complete__c")],
    ):
        assert gc._best_aligned_vr(ALL_VRS, conds) != VR_AMOUNT


def test_best_aligned_cardinality_not_jaccard():
    # A broad VR covering BOTH claim fields must beat a narrow VR covering one,
    # even though Jaccard (intersection/union) would invert the ranking:
    #   broad {a,b,c,d,e} ∩ {a,b}=2, Jaccard 2/5=0.40
    #   narrow {a}        ∩ {a,b}=1, Jaccard 1/2=0.50  → Jaccard wrongly prefers narrow
    broad = "AND(ISBLANK(A__c), ISBLANK(B__c), ISBLANK(C__c), ISBLANK(D__c), ISBLANK(E__c))"
    narrow = "ISBLANK(A__c)"
    conds = [_cond("Obj.A__c"), _cond("Obj.B__c")]
    assert gc._best_aligned_vr((narrow, broad), conds) == broad  # cardinality → broad


def test_best_aligned_refuses_on_ambiguous_multi_tie():
    # Loan_Amount alone scores 1 against Home_Loan / Loan_Exceeds / Block_Approved —
    # a 3-way tie. D-295.1: an ambiguous grounding REFUSES (None) rather than
    # guessing (the removed tightest-VR tie-break would have picked Loan_Exceeds).
    conds = [_cond("Opportunity.Loan_Amount__c")]
    assert gc._best_aligned_vr(ALL_VRS, conds) is None


def test_best_aligned_entailment_resolves_two_way_tie_order_independent():
    # D-350: {ISBLANK(A), NOT(ISBLANK(A))} tie on field-overlap {a__c}, but the claim
    # asserts A__c="x" (non-blank) → the rejection state NECESSARILY fires
    # NOT(ISBLANK(A)) and never ISBLANK(A). Entailment resolves what field-overlap
    # could not; order is never a signal.
    a1 = "ISBLANK(A__c)"
    a2 = "NOT(ISBLANK(A__c))"        # same field set {a__c} → both score 1 → tie
    conds = [_cond("Obj.A__c")]      # equals "x"
    assert gc._best_aligned_vr((a1, a2), conds) == a2
    assert gc._best_aligned_vr((a2, a1), conds) == a2     # order never a signal


def test_best_aligned_refuses_genuine_entailment_ambiguity():
    # D-350 refuse-on-non-unique floor: when >=2 tied VRs NECESSARILY fire under the
    # asserted state, selection refuses rather than guess. An is_null claim fires BOTH
    # ISBLANK and ISNULL → 2 entail, no cross-field pair to break → None.
    b1 = "ISBLANK(A__c)"
    b2 = "ISNULL(A__c)"
    conds = [_cond("Obj.A__c", predicate="is_null", value=None)]
    assert gc._best_aligned_vr((b1, b2), conds) is None


def test_entailment_selects_sole_necessary_vr():
    # Two VRs tie on {stage__c}; the claim asserts Stage="Approved" → only the
    # ISPICKVAL(...,"Approved") rule NECESSARILY fires → unique select.
    v_appr = 'ISPICKVAL(Stage__c, "Approved")'
    v_draft = 'ISPICKVAL(Stage__c, "Draft")'
    conds = [_cond("Obj.Stage__c", value="Approved")]
    assert gc._best_aligned_vr((v_appr, v_draft), conds) == v_appr


def test_entailment_in_set_all_members_fire_selects():
    # in_set {Approved, Contract Review}: a rule firing on BOTH members necessarily
    # fires → select; a rule firing on neither is contradicted.
    v = '(ISPICKVAL(Stage__c, "Approved") || ISPICKVAL(Stage__c, "Contract Review"))'
    v_other = 'ISPICKVAL(Stage__c, "Draft")'
    conds = [_cond("Obj.Stage__c", predicate="in_set",
                   value=["Approved", "Contract Review"])]
    assert gc._best_aligned_vr((v, v_other), conds) == v


def test_entailment_in_set_partial_fire_refuses_possibly_not_necessarily():
    # in_set {Approved, Draft}: the rule fires at Approved but NOT Draft → it only
    # POSSIBLY fires → UNKNOWN → refuse (the necessarily-not-possibly floor, D-350).
    v_appr = 'ISPICKVAL(Stage__c, "Approved")'
    v_review = 'ISPICKVAL(Stage__c, "Contract Review")'
    conds = [_cond("Obj.Stage__c", predicate="in_set", value=["Approved", "Draft"])]
    assert gc._best_aligned_vr((v_appr, v_review), conds) is None


def test_entailment_org_state_vr_never_fires():
    # An org-state-only tied VR (ISCHANGED) is never provably fired, so it can never
    # win the tie-break; the concrete rule the state necessarily fires does.
    v_changed = "ISCHANGED(A__c)"                 # org-state → unknown, never fires
    v_blank = "NOT(ISBLANK(A__c))"
    conds = [_cond("Obj.A__c")]                   # equals "x" → NOT(ISBLANK) fires
    assert gc._best_aligned_vr((v_changed, v_blank), conds) == v_blank


def test_best_aligned_refuses_business_vs_generic_tie():
    # D-295.1 regression guard (the adversarial wrong-green vector): a claim
    # under-specified to {discount__c} ties the intended approval-gate rule (2
    # fields) with a generic numeric rule (1 field). The OLD tightest-VR tie-break
    # picked the GENERIC rule — the exact class D-295 exists to avoid. Now it
    # REFUSES rather than green a test asserting the wrong rule.
    business = "AND(Discount__c > 0, ISBLANK(Approver__c))"
    generic = "Discount__c > 50"
    conds = [_cond("Opportunity.Discount__c")]
    assert gc._best_aligned_vr((business, generic), conds) is None
    assert gc._best_aligned_vr((generic, business), conds) is None


def test_best_aligned_strict_higher_overlap_still_wins():
    # A UNIQUE top score is still selected (only ties refuse) — cardinality picks
    # the strictly-higher-overlap VR. This keeps AC1/AC4/AC9 working.
    broad = "AND(ISBLANK(A__c), ISBLANK(B__c), ISBLANK(C__c))"   # {a,b,c}
    narrow = "ISBLANK(A__c)"                                      # {a}
    conds = [_cond("Obj.A__c"), _cond("Obj.B__c")]               # broad=2, narrow=1
    assert gc._best_aligned_vr((narrow, broad), conds) == broad


def test_best_aligned_refuse_no_shared_field():
    conds = [_cond("Opportunity.Description")]  # no VR references Description
    assert gc._best_aligned_vr(ALL_VRS, conds) is None


def test_best_aligned_empty_conditions_is_none():
    assert gc._best_aligned_vr(ALL_VRS, []) is None


# --- _align_vr_to_conditions: ARMED (S2 — narrow to the aligned VR, or refuse) --

def test_align_narrows_ac1_to_home_loan():
    # 5 candidate VRs → the ONE matching AC1's conditions. This is the defect fix:
    # AC1 no longer grounds on the generic Amount rule.
    conds = [_cond("Opportunity.Loan_Type__c"), _cond("Opportunity.Loan_Amount__c")]
    assert gc._align_vr_to_conditions(ALL_VRS, conds) == (VR_HOME_LOAN,)


def test_align_narrows_ac4_to_credit_assessment():
    conds = [_cond("Opportunity.StageName"), _cond("Opportunity.KYC_Complete__c")]
    assert gc._align_vr_to_conditions(ALL_VRS, conds) == (VR_CREDIT_ASSESSMENT,)


def test_align_narrows_ac9_to_block_approved():
    conds = [_cond("Opportunity.Loan_Amount__c"),
             _cond("Opportunity.Approval_Status__c"), _cond("Opportunity.StageName")]
    assert gc._align_vr_to_conditions(ALL_VRS, conds) == (VR_BLOCK_APPROVED,)


def test_align_refuses_empty_when_no_vr_aligns():
    # >=2 candidates + conditions, no shared field → () → the D-293 gate refuses
    # (the D-295 mismatch reason, not a derivability gap).
    conds = [_cond("Opportunity.Description")]
    assert gc._align_vr_to_conditions(ALL_VRS, conds) == ()


def test_align_ac2_selects_loan_exceeds_via_cross_field_tiebreak():
    # D-296 (lever 4): {loan_amount, property_value} ties Home_Loan(2) and
    # Loan_Exceeds(2) on field-overlap, but only Loan_Exceeds carries a cross-field
    # pair EXACTLY equal to the claim's fields → the tie-break selects it. This was a
    # refusal under D-295.1; the flip IS the point of lever 4. (AC2 still refuses
    # END-TO-END until S3 derivation — but the SELECTION is now correct.)
    conds = [_cond("Opportunity.Loan_Amount__c"),
             _cond("Opportunity.Property_Value__c")]
    assert gc._align_vr_to_conditions(ALL_VRS, conds) == (VR_LOAN_EXCEEDS,)


def test_align_degenerate_empty_conditions_passthrough_identity():
    # A condition-free prohibition (pre-D-293) is untouched — SAME object, all VRs,
    # first-derivable preserved. The byte-identity backstop.
    assert gc._align_vr_to_conditions(ALL_VRS, []) is ALL_VRS
    assert gc._align_vr_to_conditions(ALL_VRS, None) is ALL_VRS


def test_align_degenerate_single_vr_passthrough_identity():
    # A single-VR object is never refused by alignment (nothing to disambiguate);
    # the lone VR grounds, with the D-293 derivability gate as its only backstop —
    # even when the claim's fields don't overlap it.
    one = (VR_HOME_LOAN,)
    conds = [_cond("Opportunity.Description")]      # score 0, but len<=1 guards
    assert gc._align_vr_to_conditions(one, conds) is one


def test_align_zero_vr_passthrough():
    conds = [_cond("Opportunity.Loan_Amount__c")]
    assert gc._align_vr_to_conditions((), conds) == ()


# --- _prohibition_refusal_detail: D-295 mismatch vs D-293 derivability gap ---

def test_refusal_detail_d295_when_no_vr_aligns():
    # Alignment emptied a >=2-VR candidate set → the D-295 mismatch reason, and it
    # surfaces the unmatched condition fields so a BA can fix the condition.
    conds = [_cond("Opportunity.Description")]
    detail = gc._prohibition_refusal_detail("Opportunity", (), ALL_VRS, conds)
    assert "D-295" in detail
    assert "do not uniquely select" in detail
    assert "description" in detail


def test_refusal_detail_d293_when_aligned_vr_not_derivable():
    # A non-empty aligned VR that isn't derivable → the D-293 derivability reason
    # (this is exactly AC2: Loan_Exceeds is selected but its compound shape can't
    # yet be derived under D-294).
    detail = gc._prohibition_refusal_detail(
        "Opportunity", (VR_LOAN_EXCEEDS,), ALL_VRS,
        [_cond("Opportunity.Loan_Amount__c"), _cond("Opportunity.Property_Value__c")])
    assert "D-293" in detail
    assert "no derivable behavioural reject recipe" in detail


def test_refusal_detail_d293_when_no_candidate_vrs():
    # No VRs on the object at all (vr_all empty) → D-293, never the D-295 mismatch.
    detail = gc._prohibition_refusal_detail("Opportunity", (), (), [])
    assert "D-293" in detail
    assert "D-295" not in detail


# --- D-296 lever 4: _vr_cross_field_pairs (dormant — the structural discriminator) --

def test_cross_field_pairs_on_real_rules():
    # Only Loan_Exceeds has a field-to-field Comparison; the rest are field-vs-literal
    # or blank/picklist checks -> no cross-field signature.
    assert gc._vr_cross_field_pairs(VR_LOAN_EXCEEDS) == frozenset(
        {frozenset({"loan_amount__c", "property_value__c"})})
    assert gc._vr_cross_field_pairs(VR_HOME_LOAN) == frozenset()
    assert gc._vr_cross_field_pairs(VR_CREDIT_ASSESSMENT) == frozenset()
    # Block_Approved's Loan_Amount__c > 5000000 is field-vs-LITERAL -> no pair.
    assert gc._vr_cross_field_pairs(VR_BLOCK_APPROVED) == frozenset()
    # The generic Amount rule (the D-295.1 wrong-green class) has no cross-field sig.
    assert gc._vr_cross_field_pairs(VR_AMOUNT) == frozenset()


def test_cross_field_pairs_unparseable_is_empty():
    assert gc._vr_cross_field_pairs("REGEX(Name, '[0-9]+') && CASE(x,1,2,3)") == frozenset()
    assert gc._vr_cross_field_pairs("") == frozenset()


def test_cross_field_pairs_order_free():
    # The pair is order-free (a frozenset), so operand order doesn't matter.
    assert gc._vr_cross_field_pairs("A__c > B__c") == gc._vr_cross_field_pairs("B__c > A__c")
    assert gc._vr_cross_field_pairs("A__c > B__c") == frozenset({frozenset({"a__c", "b__c"})})


def test_cross_field_pairs_dotted_takes_bare_tail():
    # A self-qualified formula ref reduces to bare tails, matching _vr_formula_fields
    # + the claim-condition side.
    assert gc._vr_cross_field_pairs(
        "Opportunity.Loan_Amount__c > Opportunity.Property_Value__c") == frozenset(
        {frozenset({"loan_amount__c", "property_value__c"})})


def test_cross_field_pairs_field_vs_literal_has_none():
    # The membership discriminator is field-TO-field only; a literal comparison
    # (either side a Literal) is never a cross-field pair.
    assert gc._vr_cross_field_pairs("Amount__c > 10000") == frozenset()
    assert gc._vr_cross_field_pairs("ISPICKVAL(StageName, \"Closed\")") == frozenset()


def test_cross_field_pairs_multiple_distinct_pairs():
    assert gc._vr_cross_field_pairs("AND(A__c > B__c, C__c < D__c)") == frozenset(
        {frozenset({"a__c", "b__c"}), frozenset({"c__c", "d__c"})})


# --- D-296 lever 4 S2: the cross-field tie-break (armed selection) ----------

def test_best_aligned_ac2_breaks_tie_to_loan_exceeds():
    # AC2 ties Home_Loan(2)/Loan_Exceeds(2) on field-overlap; the cross-field
    # discriminator picks Loan_Exceeds (its pair == claim fields exactly).
    conds = [_cond("Opportunity.Loan_Amount__c"), _cond("Opportunity.Property_Value__c")]
    assert gc._best_aligned_vr(ALL_VRS, conds) == VR_LOAN_EXCEEDS


def test_best_aligned_ac1_ac4_ac9_are_strict_winners_unaffected():
    # The strict-unique winners never reach the tie branch — byte-identical selection.
    assert gc._best_aligned_vr(ALL_VRS, [_cond("Opportunity.Loan_Type__c"),
                                         _cond("Opportunity.Loan_Amount__c")]) == VR_HOME_LOAN
    assert gc._best_aligned_vr(ALL_VRS, [_cond("Opportunity.StageName"),
                                         _cond("Opportunity.KYC_Complete__c")]) == VR_CREDIT_ASSESSMENT
    assert gc._best_aligned_vr(ALL_VRS, [_cond("Opportunity.Loan_Amount__c"),
                                         _cond("Opportunity.Approval_Status__c"),
                                         _cond("Opportunity.StageName")]) == VR_BLOCK_APPROVED


def test_break_tie_exactly_one_qualifier():
    conds = frozenset({"loan_amount__c", "property_value__c"})
    # Home_Loan has no cross-field pair; Loan_Exceeds's pair == conds → unique winner.
    assert gc._break_tie_by_cross_field(
        [VR_HOME_LOAN, VR_LOAN_EXCEEDS], conds) == VR_LOAN_EXCEEDS


def test_break_tie_two_cross_field_returns_none():
    # ATTACK2: two VRs each carry a cross-field pair == claim fields → ambiguous → None.
    a = "A__c > B__c"
    b = "B__c < A__c"      # same order-free pair {a__c, b__c}
    conds = frozenset({"a__c", "b__c"})
    assert gc._break_tie_by_cross_field([a, b], conds) is None


def test_break_tie_superset_attack_rejected_by_exact_equality():
    # An over-grounded claim {a,b,c}: an INCIDENTAL cross-field VR carries pair {a,b}
    # (a strict SUBSET of the claim). Exact-equality REJECTS it → None (no mis-select).
    incidental = "AND(ISBLANK(C__c), A__c > B__c)"     # cross-field pair {a__c, b__c}
    all_blank = "AND(ISBLANK(A__c), ISBLANK(B__c), ISBLANK(C__c))"
    conds = frozenset({"a__c", "b__c", "c__c"})
    assert gc._break_tie_by_cross_field([all_blank, incidental], conds) is None


def test_break_tie_zero_qualifiers_returns_none():
    conds = frozenset({"x__c", "y__c"})   # no VR references these
    assert gc._break_tie_by_cross_field([VR_HOME_LOAN, VR_CREDIT_ASSESSMENT], conds) is None


# --- D-330: cross-field clause + predicate-aware hard filter -----------------

def _cross_cond(left: str, right: str) -> _GroundedCondition:
    return _GroundedCondition(
        field=_Endpoint(entity_id=1, entity_type="Field", external_id=left),
        predicate="exceeds",
        compared_to=_Endpoint(entity_id=2, entity_type="Field", external_id=right))


def test_cross_field_clause_contributes_both_fields():
    conds = (_cross_cond("Opportunity.Loan_Amount__c",
                         "Opportunity.Property_Value__c"),)
    assert gc._claim_condition_fields(conds) == frozenset(
        {"loan_amount__c", "property_value__c"})
    assert gc._claim_cross_field_pairs(conds) == frozenset(
        {frozenset({"loan_amount__c", "property_value__c"})})


def test_cross_field_hard_filter_beats_wider_field_overlap():
    # The req-302 AC2 mis-attribution: with conditions {Loan Type, Loan Amount
    # exceeds Property Value}, plain field-overlap scores VR_HOME_LOAN 3
    # (loan_type + loan_amount + property_value) vs VR_LOAN_EXCEEDS 2 — the
    # WRONG unique top-scorer. The D-330 hard filter admits only VRs carrying
    # the claim's cross-field pair, so the exceeds rule wins.
    conds = (
        _cond("Opportunity.Loan_Type__c"),
        _cross_cond("Opportunity.Loan_Amount__c",
                    "Opportunity.Property_Value__c"),
    )
    assert gc._align_vr_to_conditions(ALL_VRS, conds) == (VR_LOAN_EXCEEDS,)


def test_cross_field_clause_alone_selects_the_exceeds_rule():
    conds = (_cross_cond("Opportunity.Loan_Amount__c",
                         "Opportunity.Property_Value__c"),)
    assert gc._align_vr_to_conditions(ALL_VRS, conds) == (VR_LOAN_EXCEEDS,)


def test_cross_field_filter_refuses_when_no_vr_carries_the_pair():
    # A cross-field claim whose pair no VR contains refuses (never falls back
    # to a same-fields-different-predicate rule).
    conds = (_cross_cond("Opportunity.Loan_Amount__c",
                         "Opportunity.Annual_Income__c"),)
    assert gc._align_vr_to_conditions(ALL_VRS, conds) == ()


def test_v1_only_conditions_unchanged_by_the_filter():
    # No cross-field clause -> the D-330 filter is inert; D-295/D-296
    # behavior byte-identical (the AC1 mandatory-fields selection).
    conds = (_cond("Opportunity.Loan_Type__c"),
             _cond("Opportunity.Annual_Income__c"))
    assert gc._align_vr_to_conditions(ALL_VRS, conds) == (VR_HOME_LOAN,)
