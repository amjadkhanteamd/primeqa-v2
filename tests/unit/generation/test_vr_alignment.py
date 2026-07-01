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


def _cond(qualified_field: str) -> _GroundedCondition:
    """A grounded condition on ``Object.Field`` (object-qualified external_id,
    as governance builds them)."""
    return _GroundedCondition(
        field=_Endpoint(entity_id=1, entity_type="Field", external_id=qualified_field),
        predicate="equals", value="x")


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


def test_best_aligned_refuses_two_way_tie_order_independent():
    a1 = "ISBLANK(A__c)"
    a2 = "NOT(ISBLANK(A__c))"        # same field set {a__c} → both score 1 → tie
    conds = [_cond("Obj.A__c")]
    assert gc._best_aligned_vr((a1, a2), conds) is None
    assert gc._best_aligned_vr((a2, a1), conds) is None   # order never a signal


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


def test_align_refuses_on_ambiguous_tie():
    # {loan_amount, property_value} ties Home_Loan(2) and Loan_Exceeds(2) → ambiguous
    # → () → the D-293 gate refuses. This is req-302 AC2 under D-295.1: field-overlap
    # alone cannot tell the cross-field rule from the mandatory-fields rule, so it
    # honestly refuses rather than guessing.
    conds = [_cond("Opportunity.Loan_Amount__c"),
             _cond("Opportunity.Property_Value__c")]
    assert gc._align_vr_to_conditions(ALL_VRS, conds) == ()


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
