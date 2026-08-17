"""D-454 pins: the partial-coverage flag — keyed to the D-453 measured record.

Acceptance, on the five named claims' real shapes: 4da40a8b flags PARTIAL
with StageName + Approval_Status__c named; 4f52b937 lands CANNOT_ASSESS
citing TA7's parse failure; 37d9dac4 lands CANNOT_ASSESS citing empty
persisted staging; e7d4c607 is COVERED via the RecordTypeId equivalence and
does NOT flag; 183846ea (TA2's prohibition) is COVERED and does not flag.
OR-awareness is pinned SYNTHETICALLY (no live OR specimen exists). The flag
never refuses and never touches bundle bodies.
"""
from types import SimpleNamespace

import pytest

from primeqa.generation.coverage_flag import (
    CANNOT_ASSESS, COVERED, PARTIAL, assess_rule_coverage)
from primeqa.generation.governance_core import (
    _coverage_flags_for, _coverage_pinned_fields)

pytestmark = pytest.mark.unit

_BLOCK_APPROVED = ('AND( ISPICKVAL(StageName, "Approved"), '
                   'Loan_Amount__c > 5000000, '
                   'NOT(ISPICKVAL(Approval_Status__c, "Approved")) )')
_TA7 = 'TEXT(PLS_TA_Parent__r.Industry) = "Banking" && PLS_TA_Amount__c > 50000'
_LOAN_EXCEEDS = 'Loan_Amount__c > Property_Value__c'
_VR08 = 'RecordType.DeveloperName = "PLS_BM_Enterprise" && PLS_BM_Discount__c > 0.25'
_TA2 = 'PLS_TA_Archived__c = TRUE && ISBLANK(PLS_TA_Archive_Reason__c)'


# ---------------------------------------------------------------------------
# The five named claims (their real persisted shapes)
# ---------------------------------------------------------------------------

def test_4da40a8b_flags_partial_naming_the_missing_conjuncts():
    f = assess_rule_coverage(
        vr_name="Block_Approved_Without_Approval",
        vr_formula=_BLOCK_APPROVED, vr_active=True,
        pinned_fields={"loan_type__c", "loan_amount__c",
                       "property_value__c", "annual_income__c"},
        mechanism_kind="boundary-literal-proxy")
    assert f.verdict == PARTIAL
    assert set(f.missing_fields) == {"stagename", "approval_status__c"}
    assert "loan_amount__c" in f.covered_fields


def test_4f52b937_cannot_assess_citing_ta7_parse_failure():
    f = assess_rule_coverage(
        vr_name="PLS_TA_VR07_Banking_Parent_Cap", vr_formula=_TA7,
        vr_active=True, pinned_fields={"pls_ta_amount__c"},
        mechanism_kind="boundary-literal-proxy")
    assert f.verdict == CANNOT_ASSESS
    assert "does not parse" in f.reason


def test_37d9dac4_cannot_assess_citing_empty_staging():
    f = assess_rule_coverage(
        vr_name="Loan_Exceeds_Property_Value", vr_formula=_LOAN_EXCEEDS,
        vr_active=True, pinned_fields=(), mechanism_kind="grounding")
    assert f.verdict == CANNOT_ASSESS
    assert "empty persisted staging" in f.reason
    assert "R1 padding" in f.reason


def test_e7d4c607_covered_by_the_recordtypeid_equivalence():
    f = assess_rule_coverage(
        vr_name="PLS_BM_VR08_Enterprise_Discount", vr_formula=_VR08,
        vr_active=True,
        pinned_fields={"recordtypeid", "pls_bm_discount__c"},
        mechanism_kind="grounding")
    assert f.verdict == COVERED
    # ... and without the equivalence pin the same shape is PARTIAL:
    f2 = assess_rule_coverage(
        vr_name="x", vr_formula=_VR08, vr_active=True,
        pinned_fields={"pls_bm_discount__c"}, mechanism_kind="grounding")
    assert f2.verdict == PARTIAL


def test_183846ea_ta2_prohibition_is_covered():
    f = assess_rule_coverage(
        vr_name="PLS_TA_VR02_Archive_Reason", vr_formula=_TA2,
        vr_active=True,
        pinned_fields={"pls_ta_archived__c", "pls_ta_archive_reason__c"},
        mechanism_kind="grounding")
    assert f.verdict == COVERED


# ---------------------------------------------------------------------------
# Disjunct-awareness — SYNTHETIC (no live OR specimen exists)
# ---------------------------------------------------------------------------

_OR_RULE = 'A__c > 5 || (B__c = TRUE && C__c > 1)'


def test_synthetic_or_one_staged_disjunct_is_full_coverage():
    f = assess_rule_coverage(
        vr_name="synthetic", vr_formula=_OR_RULE, vr_active=True,
        pinned_fields={"a__c"}, mechanism_kind="grounding")
    assert f.verdict == COVERED       # conjunct-flattening would false-flag


def test_synthetic_or_partial_within_a_disjunct_still_flags():
    f = assess_rule_coverage(
        vr_name="synthetic", vr_formula=_OR_RULE, vr_active=True,
        pinned_fields={"b__c"}, mechanism_kind="grounding")
    assert f.verdict == PARTIAL
    assert "c__c" in f.missing_fields


def test_not_wrapped_conjunct_fields_still_require_pins():
    rule = 'NOT(ISPICKVAL(S__c, "X")) && A__c > 1'
    assert assess_rule_coverage(
        vr_name="n", vr_formula=rule, vr_active=True,
        pinned_fields={"s__c", "a__c"},
        mechanism_kind="grounding").verdict == COVERED
    f = assess_rule_coverage(
        vr_name="n", vr_formula=rule, vr_active=True,
        pinned_fields={"a__c"}, mechanism_kind="grounding")
    assert f.verdict == PARTIAL and "s__c" in f.missing_fields


def test_fieldless_leaves_are_trivially_covered():
    f = assess_rule_coverage(
        vr_name="n", vr_formula="ISNEW() && U__c > 100", vr_active=True,
        pinned_fields={"u__c"}, mechanism_kind="grounding")
    assert f.verdict == COVERED


def test_inactive_mechanism_is_its_own_named_state():
    f = assess_rule_coverage(
        vr_name="n", vr_formula=_TA2, vr_active=False,
        pinned_fields={"pls_ta_archived__c", "pls_ta_archive_reason__c"},
        mechanism_kind="grounding")
    assert f.verdict == COVERED and f.mechanism_inactive is True
    assert f.to_payload().get("mechanism_inactive") is True


# ---------------------------------------------------------------------------
# The wiring keeps clean bundles byte-identical (flag-never-refuse)
# ---------------------------------------------------------------------------

def _bundle(conds=(), staged=()):
    cond_objs = [SimpleNamespace(
        subject=SimpleNamespace(external_id=f"Obj.{f}"),
        predicate=p, value=v) for f, p, v in conds]
    steps = [SimpleNamespace(field_values={f"Obj.{f}": v for f, v in staged},
                             field_changes=None)]
    return SimpleNamespace(
        archetype="data_behavior", claim_kind="acceptance-claim",
        semantic_conditions=SimpleNamespace(conditions=cond_objs),
        observation_realization=SimpleNamespace(steps=steps),
        coverage_flag=None)


def test_covered_active_rules_persist_but_stay_off_the_outcome_surface():
    """D-455 amended D-454's filter: a mechanism-resolved COVERED persists
    (provenance — so "assessed, clean" is distinguishable from "predates
    the flag") but stays OFF the outcome's signal-only surface."""
    from primeqa.generation.governance_core import _coverage_signal_only
    g = SimpleNamespace(vr_formulas=(_TA2,), claim_kind="prohibition-claim")
    b = _bundle(conds=(("PLS_TA_Archived__c", "equals", True),
                       ("PLS_TA_Archive_Reason__c", "is_null", None)),
                staged=(("PLS_TA_Archived__c", True),))
    rules = [{"name": "TA2", "formula": _TA2, "active": True,
              "nums": set(), "bare": {"pls_ta_archived__c",
                                      "pls_ta_archive_reason__c"}}]
    flags = _coverage_flags_for(g, b, rules)
    assert len(flags) == 1 and flags[0].verdict == COVERED
    payloads = [f.to_payload() for f in flags]
    assert _coverage_signal_only(payloads) == []          # signal-only filter
    inactive = [dict(p, mechanism_inactive=True) for p in payloads]
    assert _coverage_signal_only(inactive) == inactive    # sub-flag passes


def test_no_mechanism_produces_no_flag():
    g = SimpleNamespace(vr_formulas=(), claim_kind="existence-claim")
    b = _bundle(staged=(("X__c", 1),))
    assert _coverage_flags_for(g, b, []) == ()


def test_boundary_literal_proxy_flags_the_vacuous_acceptance():
    """The 4da40a8b shape end-to-end through the mechanism resolver."""
    g = SimpleNamespace(vr_formulas=(), claim_kind="acceptance-claim")
    b = _bundle(conds=(("Loan_Amount__c", "equals", 5000000),),
                staged=(("Loan_Amount__c", 5000000),
                        ("Loan_Type__c", "Home")))
    rules = [{"name": "Block_Approved_Without_Approval",
              "formula": _BLOCK_APPROVED, "active": True,
              "nums": {5000000.0},
              "bare": {"stagename", "loan_amount__c",
                       "approval_status__c"}}]
    flags = _coverage_flags_for(g, b, rules)
    assert len(flags) == 1 and flags[0].verdict == PARTIAL
    assert set(flags[0].missing_fields) == {"stagename",
                                            "approval_status__c"}


def test_pinned_fields_reads_conditions_and_staged_steps():
    b = _bundle(conds=(("A__c", "equals", 1), ("B__c", "is_null", None)),
                staged=(("C__c", 2),))
    assert _coverage_pinned_fields(b) == frozenset(
        {"a__c", "b__c", "c__c"})
