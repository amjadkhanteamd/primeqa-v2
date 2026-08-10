"""D-439 pins: org-state, field-vs-field, RecordType evaluator families.

The create-semantics pins cite their verification sources (the D-439
verify-first gate). ISCHANGED-on-create is deliberately NonEvaluable — not
conclusively verified, and a guessed default flips verdicts (D-399.1).
"""
import pytest

from primeqa.semantic.formula import EvalContext, NonEvaluable, evaluate, parse

pytestmark = pytest.mark.unit


def ev(formula, payload, **ctx_kwargs):
    ctx = EvalContext(**ctx_kwargs) if ctx_kwargs else None
    return evaluate(parse(formula), payload, context=ctx)


# ---------------------------------------------------------------------------
# Create-context semantics — the verify-first gate (D-439 Phase 0)
# ---------------------------------------------------------------------------

def test_isnew_true_on_create_CONFIRMED_marksgroup():
    """CONFIRMED: 'ISNEW() will check if the formula you create is running
    when a new record is created and will return TRUE if it is' —
    marksgroup.net/blog/salesforce-com-process-builder-functions-isnew-ischanged."""
    assert ev("ISNEW()", {}, is_create=True) is True
    assert ev("ISNEW()", {}, is_create=False) is False


def test_priorvalue_on_create_returns_current_CONFIRMED_salesforcefaqs():
    """CONFIRMED: 'the PRIORVALUE function will return the value that the
    field had during the creation of the record' —
    salesforcefaqs.com/salesforce-priorvalue-function (corroborated by the
    O'Reilly Advanced Administrator guide and the NOT(ISNEW()) guard idiom).
    Composition shape: ISPICKVAL(PRIORVALUE(f), lit)."""
    f = 'ISPICKVAL(PRIORVALUE(Stage__c), "Approved")'
    assert ev(f, {"Stage__c": "Approved"}, is_create=True) is True
    assert ev(f, {"Stage__c": "Draft"}, is_create=True) is False


def test_ischanged_on_create_is_nonevaluable_NOT_VERIFIED():
    """NOT conclusively verified against a published source — so the create
    context REFUSES rather than guesses (D-399.1: a false confident verdict
    is worse than an admitted gap)."""
    r = ev("ISCHANGED(Stage__c)", {"Stage__c": "X"}, is_create=True)
    assert isinstance(r, NonEvaluable)
    assert "refusing to guess" in r.reason


# ---------------------------------------------------------------------------
# Org-state on updates (the pre/post pair)
# ---------------------------------------------------------------------------

def test_ischanged_update_changed_and_unchanged():
    prior = {"Stage__c": "Draft", "Amount__c": 5}
    assert ev("ISCHANGED(Stage__c)", {"Stage__c": "Approved"},
              prior_state=prior, is_create=False) is True
    assert ev("ISCHANGED(Amount__c)", {"Amount__c": 5},
              prior_state=prior, is_create=False) is False


def test_ischanged_absence_either_side_is_nonevaluable():
    r = ev("ISCHANGED(Missing__c)", {"Other__c": 1},
           prior_state={"Other__c": 1}, is_create=False)
    assert isinstance(r, NonEvaluable)


def test_priorvalue_update_reads_prior_state():
    f = 'ISPICKVAL(PRIORVALUE(Stage__c), "Approved")'
    assert ev(f, {"Stage__c": "Contract Review"},
              prior_state={"Stage__c": "Approved"}, is_create=False) is True
    assert ev(f, {"Stage__c": "Contract Review"},
              prior_state={"Stage__c": "Draft"}, is_create=False) is False


def test_priorvalue_missing_from_prior_state_is_nonevaluable():
    f = 'ISPICKVAL(PRIORVALUE(Stage__c), "Approved")'
    r = ev(f, {"Stage__c": "X"}, prior_state={}, is_create=False)
    assert isinstance(r, NonEvaluable)


def test_no_context_keeps_pre_d439_behaviour():
    for f in ("ISCHANGED(A__c)", "ISNEW()",
              'ISPICKVAL(PRIORVALUE(A__c), "x")'):
        assert isinstance(ev(f, {"A__c": 1}), NonEvaluable)


def test_vr05_shape_end_to_end():
    """PLS_BM_VR05_Approved_Lock: prior Approved + value changed → fires."""
    f = ('ISPICKVAL(PRIORVALUE(PLS_BM_Stage__c), "Approved") && '
         'ISCHANGED(PLS_BM_Deal_Value__c)')
    prior = {"PLS_BM_Stage__c": "Approved", "PLS_BM_Deal_Value__c": 100}
    new = {"PLS_BM_Stage__c": "Contract Review", "PLS_BM_Deal_Value__c": 101}
    assert ev(f, new, prior_state=prior, is_create=False) is True
    unchanged = dict(new, PLS_BM_Deal_Value__c=100)
    assert ev(f, unchanged, prior_state=prior, is_create=False) is False


def test_kleene_or_still_shortcircuits_past_unarmed_today():
    """VR10 shape: a True disjunct dominates the still-NonEvaluable
    TODAY() comparison — arming org-state must not disturb Kleene."""
    f = ('ISCHANGED(Stage__c) && (ISBLANK(Num__c) || Start__c < TODAY())')
    r = ev(f, {"Stage__c": "B", "Num__c": None, "Start__c": "2026-01-01"},
           prior_state={"Stage__c": "A"}, is_create=False)
    assert r is True                     # ISBLANK True short-circuits the OR


# ---------------------------------------------------------------------------
# Field-vs-field numeric
# ---------------------------------------------------------------------------

def test_field_vs_field_numeric_compare():
    f = "Loan__c > Property__c"
    assert ev(f, {"Loan__c": 2, "Property__c": 1}) is True
    assert ev(f, {"Loan__c": 1, "Property__c": 2}) is False


def test_field_vs_field_null_operand_is_nonevaluable():
    r = ev("Loan__c > Property__c", {"Loan__c": None, "Property__c": 0})
    assert isinstance(r, NonEvaluable)


def test_field_vs_field_absent_or_nonnumeric_is_nonevaluable():
    assert isinstance(ev("A__c > B__c", {"A__c": 1}), NonEvaluable)
    assert isinstance(
        ev("A__c > B__c", {"A__c": "x", "B__c": 1}), NonEvaluable)
    assert isinstance(
        ev("A__c > B__c", {"A__c": True, "B__c": 1}), NonEvaluable)


def test_blank_vs_zero_cannot_reach_the_comparison():
    """The Loan_Exceeds shape: a blank operand makes the guarded formula an
    evaluable FALSE via its own ISBLANK guards (Kleene) — the ambiguous
    blank-vs-zero comparison is never consulted."""
    f = ("AND(NOT(ISBLANK(Loan__c)), NOT(ISBLANK(Property__c)), "
         "Loan__c > Property__c)")
    assert ev(f, {"Loan__c": None, "Property__c": 0}) is False


# ---------------------------------------------------------------------------
# RecordType resolution
# ---------------------------------------------------------------------------

def _resolver(mapping):
    return lambda rid: mapping.get(rid[:15])


def test_record_type_equality_resolves_through_s1():
    f = 'RecordType.DeveloperName = "PLS_BM_Enterprise" && Disc__c > 0.25'
    ctx = _resolver({"012Ip0000005exL": "PLS_BM_Enterprise"})
    assert ev(f, {"RecordTypeId": "012Ip0000005exLIAQ", "Disc__c": 0.3},
              record_type_developer_name=ctx) is True
    assert ev(f, {"RecordTypeId": "012Ip0000005exLIAQ", "Disc__c": 0.3},
              record_type_developer_name=_resolver(
                  {"012Ip0000005exL": "Other"})) is False


def test_record_type_unresolvable_or_missing_is_nonevaluable():
    f = 'RecordType.DeveloperName = "X"'
    assert isinstance(
        ev(f, {"RecordTypeId": "012zzzzzzzzzzzzZZZ"},
           record_type_developer_name=_resolver({})), NonEvaluable)
    assert isinstance(
        ev(f, {"Disc__c": 1},
           record_type_developer_name=_resolver({"a": "b"})), NonEvaluable)
    assert isinstance(ev(f, {"RecordTypeId": "x"}), NonEvaluable)  # no ctx


def test_other_dotted_refs_stay_nonevaluable():
    r = ev('Parent.Name = "x"', {"Parent": "y"})
    assert isinstance(r, NonEvaluable)
