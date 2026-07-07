"""Pure unit tests for the D-337 authoring-time staged-state VR-conflict
guard (no PG, no LLM).

The guard refuses a claim whose OWN staged values — automation-effect
trigger_fields / update_trigger_fields, acceptance conditions (+ D-306
update clauses), approval-arc conditions — provably fire one of the org's
ACTIVE validation rules: the org would reject the create/update for a
reason that is not the behavior under test (perma-red by construction).

The formulas below are the REAL env-59 Opportunity rules for req-302 (the
same corpus as test_vr_alignment), so the provable-fire and Kleene-unknown
verdicts are proven on the actual business rules — including
Loan_Exceeds_Property_Value, the rule behind the 2026-07-07 live catch (an
LTV automation-effect whose staged update put Loan_Amount__c >
Property_Value__c).

Kleene discipline is the load-bearing property: an unstaged field is
UNKNOWN (padding / the org may supply anything), and unknown never refuses
— only a formula whose truth is determined by the staged values alone
does. A wrong refusal of a runnable claim is the one dangerous failure
mode (the R3 posture).
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from primeqa.generation import governance_core as gc
from primeqa.generation.vr_conflict import find_staged_vr_conflict

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

ALL_RULES = (
    ("Opportunity.Amount_Floor", VR_AMOUNT),
    ("Opportunity.Block_Approved", VR_BLOCK_APPROVED),
    ("Opportunity.Credit_Assessment_Prerequisites", VR_CREDIT_ASSESSMENT),
    ("Opportunity.Home_Loan_Mandatory_Fields", VR_HOME_LOAN),
    ("Opportunity.Loan_Exceeds_Property_Value", VR_LOAN_EXCEEDS),
)


# --- the live catch: field-to-field comparison over staged values -----------

def test_live_catch_update_overlay_fires_loan_exceeds():
    # The req-302 defect verbatim: create stages a valid 80% LTV, the update
    # phase raises Loan_Amount above Property_Value (120% LTV) — the org's own
    # rule rejects that update; the recompute is never observed.
    detail = find_staged_vr_conflict(
        ALL_RULES,
        {"Opportunity.Loan_Amount__c": "400000",
         "Opportunity.Property_Value__c": "500000"},
        {"Opportunity.Loan_Amount__c": "600000"})
    assert detail is not None
    assert "Loan_Exceeds_Property_Value" in detail
    assert "update" in detail
    assert "Loan_Amount__c > Property_Value__c" in detail   # squashed formula


def test_live_catch_shape_at_create_names_create():
    detail = find_staged_vr_conflict(
        ALL_RULES,
        {"Opportunity.Loan_Amount__c": "600000",
         "Opportunity.Property_Value__c": "500000"})
    assert detail is not None
    assert "Loan_Exceeds_Property_Value" in detail
    assert "staged create state" in detail


def test_valid_ltv_staging_passes():
    assert find_staged_vr_conflict(
        ALL_RULES,
        {"Opportunity.Loan_Amount__c": "400000",
         "Opportunity.Property_Value__c": "500000"}) is None


# --- Kleene: unknown never refuses -------------------------------------------

def test_unstaged_field_is_unknown_not_a_fire():
    # Loan_Amount staged alone: the > comparison has an unstaged side, so the
    # rule is UNKNOWN — padding may supply any Property_Value. Never refuse.
    assert find_staged_vr_conflict(
        ALL_RULES, {"Opportunity.Loan_Amount__c": "600000"}) is None


def test_armed_and_with_unknown_conjunct_passes():
    # Credit Assessment armed (staged StageName) with KYC satisfied; the
    # ISBLANK(Credit_Score__c) arm is unknown (padding fills it at run time —
    # exactly R1's job). AND(True, OR(False, unknown)) -> unknown -> pass.
    assert find_staged_vr_conflict(
        [("VR", VR_CREDIT_ASSESSMENT)],
        {"Opportunity.StageName": "Credit Assessment",
         "Opportunity.KYC_Complete__c": True}) is None


def test_armed_and_with_provable_conjunct_fires():
    # Staged KYC_Complete=False under Credit Assessment: NOT(False) -> True,
    # the OR fires, the AND fires — provable from staged values alone
    # (padding cannot touch staged fields, k16).
    detail = find_staged_vr_conflict(
        [("Opportunity.Credit_Assessment_Prerequisites", VR_CREDIT_ASSESSMENT)],
        {"Opportunity.StageName": "Credit Assessment",
         "Opportunity.KYC_Complete__c": False})
    assert detail is not None
    assert "Credit_Assessment_Prerequisites" in detail


def test_or_with_one_true_arm_fires_despite_unknown_sibling():
    # Kleene OR: True OR unknown -> True (strictly more precise than the
    # strict-bool posture that would bail on the unknown sibling).
    detail = find_staged_vr_conflict(
        [("VR", VR_HOME_LOAN)],
        {"Opportunity.Loan_Type__c": "Home",
         "Opportunity.Loan_Amount__c": ""})    # staged EMPTY -> ISBLANK True
    assert detail is not None


def test_false_scope_clause_makes_rule_provably_silent():
    # Loan_Type=Personal: ISPICKVAL(.., 'Home') is provably False beyond
    # case, the AND is False — the mandatory-fields rule cannot fire.
    assert find_staged_vr_conflict(
        [("VR", VR_HOME_LOAN)],
        {"Opportunity.Loan_Type__c": "Personal"}) is None


# --- conservative leaves ------------------------------------------------------

def test_case_only_text_equality_is_unknown():
    # 'home' vs 'Home': equal only up to case — provable under NEITHER the
    # case-insensitive nor the case-sensitive reading, so unknown (no refuse
    # even though the mandatory-fields arm would fire if it matched).
    assert find_staged_vr_conflict(
        [("VR", VR_HOME_LOAN)],
        {"Opportunity.Loan_Type__c": "home",
         "Opportunity.Loan_Amount__c": ""}) is None


def test_org_state_functions_are_unknown():
    assert find_staged_vr_conflict(
        [("VR", "AND(ISCHANGED(Amount), Amount > 10)")],
        {"Opportunity.Amount": 50}) is None
    assert find_staged_vr_conflict(
        [("VR", "PRIORVALUE(Amount) > Amount")],
        {"Opportunity.Amount": 50}) is None


def test_unparseable_formula_is_unknown():
    assert find_staged_vr_conflict(
        [("VR", "REGEX(Name, '[0-9]+') && CASE(x,1,2,3)")],
        {"Opportunity.Name": "abc"}) is None


def test_cross_object_ref_is_unknown():
    assert find_staged_vr_conflict(
        [("VR", "Account.Industry = 'Banking'")],
        {"Opportunity.Industry": "Banking"}) is None


def test_non_numeric_staged_value_against_number_literal_is_unknown():
    assert find_staged_vr_conflict(
        [("VR", "Amount > 10000")], {"Opportunity.Amount": "high"}) is None


def test_null_staged_value_in_comparison_is_unknown():
    assert find_staged_vr_conflict(
        [("VR", "Amount > 10000")], {"Opportunity.Amount": None}) is None


# --- provable leaves -----------------------------------------------------------

def test_string_typed_staged_number_coerces():
    # The D-304 _identity_safe boundary makes staged numbers strings — the
    # numeric compare must still be provable.
    assert find_staged_vr_conflict(
        [("VR", VR_AMOUNT)], {"Opportunity.Amount": "10001"}) is not None
    assert find_staged_vr_conflict(
        [("VR", VR_AMOUNT)], {"Opportunity.Amount": "9999"}) is None


def test_literal_on_the_left_flips():
    assert find_staged_vr_conflict(
        [("VR", "10000 < Amount")], {"Opportunity.Amount": 10001}) is not None


def test_bare_boolean_field_and_string_bool_shapes():
    assert find_staged_vr_conflict(
        [("VR", "NOT(KYC_Complete__c)")],
        {"Opportunity.KYC_Complete__c": False}) is not None
    assert find_staged_vr_conflict(
        [("VR", "NOT(KYC_Complete__c)")],
        {"Opportunity.KYC_Complete__c": "false"}) is not None
    assert find_staged_vr_conflict(
        [("VR", "NOT(KYC_Complete__c)")],
        {"Opportunity.KYC_Complete__c": True}) is None
    # unstaged bare boolean -> unknown
    assert find_staged_vr_conflict(
        [("VR", "NOT(KYC_Complete__c)")],
        {"Opportunity.Other__c": 1}) is None


def test_text_inequality_beyond_case_is_provable():
    # <> on text: 'Personal' vs 'Home' differ beyond case — provably unequal
    # under both case-semantics readings.
    assert find_staged_vr_conflict(
        [("VR", "Loan_Type__c <> 'Home'")],
        {"Opportunity.Loan_Type__c": "Personal"}) is not None
    assert find_staged_vr_conflict(
        [("VR", "Loan_Type__c <> 'Home'")],
        {"Opportunity.Loan_Type__c": "Home"}) is None


# --- phases, determinism, edges ------------------------------------------------

def test_create_fire_reported_before_update_fire():
    # A rule firing at create is reported as the create-phase conflict even
    # when the update overlay would also fire it.
    detail = find_staged_vr_conflict(
        [("VR", VR_AMOUNT)],
        {"Opportunity.Amount": 20000}, {"Opportunity.Amount": 30000})
    assert detail is not None and "create" in detail


def test_update_overlay_overrides_create_value():
    # create violates, update fixes it back under the floor: create-phase
    # still fires (the create itself bounces) — but prove the overlay
    # direction too: create clean, update violates -> update-phase.
    detail = find_staged_vr_conflict(
        [("VR", VR_AMOUNT)],
        {"Opportunity.Amount": 5000}, {"Opportunity.Amount": 20000})
    assert detail is not None and "update" in detail


def test_deterministic_first_rule_by_sorted_name():
    rules = [("Z_Rule", "Amount > 10"), ("A_Rule", "Amount > 5")]
    detail = find_staged_vr_conflict(rules, {"Opportunity.Amount": 50})
    assert "A_Rule" in detail


def test_empty_rules_or_empty_staging_pass():
    assert find_staged_vr_conflict([], {"Opportunity.Amount": 50}) is None
    assert find_staged_vr_conflict(
        [("VR", VR_AMOUNT)], {}) is None
    assert find_staged_vr_conflict(
        [("VR", VR_AMOUNT)], {}, {}) is None


def test_update_only_staging_checks_the_overlay():
    # No create-staged fields (e.g. a padding-only create) but a staged
    # update: the post-update state is still checkable.
    detail = find_staged_vr_conflict(
        [("VR", VR_AMOUNT)], {}, {"Opportunity.Amount": 20000})
    assert detail is not None and "update" in detail


# --- _staged_vr_conflict_detail: the governance-side neighborhood read ---------

def _vr_rel(name, attrs):
    return SimpleNamespace(
        edge_type="APPLIES_TO",
        entity=SimpleNamespace(entity_type="ValidationRule", sf_api_name=name,
                               id=uuid4(), attributes=attrs))


def test_helper_reads_active_vrs_and_names_the_rule():
    nb = [
        _vr_rel("Opportunity.Loan_Exceeds", {"formula_text": VR_LOAN_EXCEEDS}),
        # wrong edge/type rows are ignored
        SimpleNamespace(edge_type="BELONGS_TO", entity=SimpleNamespace(
            entity_type="Field", sf_api_name="Opportunity.X__c", id=uuid4(),
            attributes={"formula_text": "Amount > 1"})),
    ]
    detail = gc._staged_vr_conflict_detail(
        nb, {"Opportunity.Loan_Amount__c": "600000",
             "Opportunity.Property_Value__c": "500000"})
    assert detail is not None and "Opportunity.Loan_Exceeds" in detail


def test_helper_skips_inactive_rules():
    # D-301: an inactive rule cannot fire — it must never refuse a staging.
    nb = [_vr_rel("Opportunity.Dead_Rule",
                  {"formula_text": VR_AMOUNT, "is_active": False})]
    assert gc._staged_vr_conflict_detail(
        nb, {"Opportunity.Amount": 20000}) is None


def test_helper_reads_raw_tooling_shape():
    # D-203.1 two-shape tolerance: Metadata.errorConditionFormula + Active.
    nb = [_vr_rel("Opportunity.Raw_Rule",
                  {"Active": True,
                   "Metadata": {"errorConditionFormula": "Amount > 10000"}})]
    detail = gc._staged_vr_conflict_detail(nb, {"Opportunity.Amount": 20000})
    assert detail is not None and "Raw_Rule" in detail


def test_helper_no_vrs_is_none():
    assert gc._staged_vr_conflict_detail([], {"Opportunity.Amount": 1}) is None
