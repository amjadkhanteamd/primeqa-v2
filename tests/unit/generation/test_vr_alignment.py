"""Pure unit tests for D-295 VR-to-claim alignment (no PG, no LLM).

The scorer selects the ONE validation rule whose fields match a prohibition
claim's grounded conditions, so each prohibition grounds on its OWN rule rather
than the first-derivable generic VR on a multi-VR object (the req-302 defect:
both AC1 and AC4 grounded on the generic ``Amount > 10000``). The VR formulas
below are the REAL env-59 Opportunity rules for req-302 (fetched from prod
tenant_1), so these tests prove selection on the actual business rules.

**Slice 1 (this file lands with it): the aligner is DORMANT** — a byte-identical
pass-through. The scorer core (``_best_aligned_vr`` + the two field-extractors)
is fully exercised here; the wrapper's pass-through is asserted by identity. S2
arms ``_align_vr_to_conditions`` to narrow / refuse and flips those assertions.
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


def test_best_aligned_tie_prefers_tightest_vr():
    # Loan_Amount alone scores 1 against Home_Loan(4), Loan_Exceeds(2), Block(3);
    # the tightest (fewest fields) wins → Loan_Exceeds.
    conds = [_cond("Opportunity.Loan_Amount__c")]
    assert gc._best_aligned_vr(ALL_VRS, conds) == VR_LOAN_EXCEEDS


def test_best_aligned_tie_same_width_prefers_earliest_index():
    a1 = "ISBLANK(A__c)"
    a2 = "NOT(ISBLANK(A__c))"   # same field set {a__c}, same width, later index
    conds = [_cond("Obj.A__c")]
    assert gc._best_aligned_vr((a1, a2), conds) == a1
    assert gc._best_aligned_vr((a2, a1), conds) == a2  # order is the last-resort key


def test_best_aligned_refuse_no_shared_field():
    conds = [_cond("Opportunity.Description")]  # no VR references Description
    assert gc._best_aligned_vr(ALL_VRS, conds) is None


def test_best_aligned_empty_conditions_is_none():
    assert gc._best_aligned_vr(ALL_VRS, []) is None


# --- _align_vr_to_conditions: DORMANT pass-through (S1 byte-identity) --------

def test_align_dormant_is_passthrough_identity():
    # Returns the SAME tuple object — no narrowing, no copy — so the gate and the
    # persisted GroundedNegative see exactly today's formulas. This is the S1
    # byte-identity guarantee; S2 flips it to narrow-or-refuse.
    conds = [_cond("Opportunity.Loan_Type__c"), _cond("Opportunity.Loan_Amount__c")]
    assert gc._align_vr_to_conditions(ALL_VRS, conds) is ALL_VRS


def test_align_dormant_unchanged_on_the_case_s2_will_change():
    # AC1 on a multi-VR object is exactly the case S2 narrows (5 VRs → 1). S1 must
    # still return all five, unchanged, so generation behaviour is identical.
    conds = [_cond("Opportunity.Loan_Type__c"), _cond("Opportunity.Loan_Amount__c")]
    assert gc._align_vr_to_conditions(ALL_VRS, conds) == ALL_VRS
    assert len(gc._align_vr_to_conditions(ALL_VRS, conds)) == 5


def test_align_dormant_passthrough_even_when_no_vr_aligns():
    conds = [_cond("Opportunity.Description")]
    assert gc._align_vr_to_conditions(ALL_VRS, conds) is ALL_VRS
