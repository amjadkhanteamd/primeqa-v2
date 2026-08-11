"""D-443 pins: the emission gate's context arm.

Direction pins: context_enabled=False is byte-old behaviour; emission-time
ISCHANGED is provably False OUTSIDE the staged update's key set; the
create phase keeps D-439's unverified-ISCHANGED refusal; percent staging
no longer proves fires the org would not make (and still proves the ones
it would); RecordType resolves through the injected resolver.
"""
import pytest

from primeqa.generation.vr_conflict import find_staged_vr_conflict

pytestmark = pytest.mark.unit


VR02 = [("VR02", 'PLS_BM_Discount__c > 0.20 && ISBLANK(PLS_BM_Approval_Reason__c)')]
VR10ISH = [("VR10ish", 'ISPICKVAL(Stage__c, "Approved") && ISCHANGED(Stage__c)')]
VR05ISH = [("VR05ish", 'ISPICKVAL(PRIORVALUE(Stage__c), "Approved") && ISCHANGED(Val__c)')]
ISNEWR = [("NewR", 'ISNEW() && ISBLANK(Reason__c)')]
RTR = [("RTR", 'RecordType.DeveloperName = "Enterprise" && Disc__c > 100')]


# ---------------------------------------------------------------------------
# The byte-old switch
# ---------------------------------------------------------------------------

def test_context_disabled_is_byte_old_admit_on_org_state():
    """Old behaviour: org-state -> unknown -> admit, even on a staged
    transition that provably fires."""
    out = find_staged_vr_conflict(
        VR10ISH, {"Stage__c": "Draft"}, {"Stage__c": "Approved"},
        context_enabled=False)
    assert out is None


# ---------------------------------------------------------------------------
# Org-state at emission
# ---------------------------------------------------------------------------

def test_staged_transition_now_provably_fires_ischanged_rule():
    out = find_staged_vr_conflict(
        VR10ISH, {"Stage__c": "Draft"}, {"Stage__c": "Approved"})
    assert out is not None and "VR10ish" in out and "update" in out


def test_ischanged_provably_false_outside_update_keys():
    """The staged update is the COMPLETE mutation: a rule needing
    ISCHANGED(Stage) cannot fire on an update that does not touch Stage."""
    out = find_staged_vr_conflict(
        VR10ISH, {"Stage__c": "Approved"}, {"Other__c": 1})
    assert out is None      # provably not fired — admit


def test_ischanged_on_create_stays_unknown():
    """D-439's unverified-create refusal carries over: a create staging
    Approved does not prove the ISCHANGED rule fires."""
    out = find_staged_vr_conflict(VR10ISH, {"Stage__c": "Approved"})
    assert out is None


def test_priorvalue_composition_fires_on_update_phase():
    out = find_staged_vr_conflict(
        VR05ISH, {"Stage__c": "Approved", "Val__c": 1}, {"Val__c": 2})
    assert out is not None and "VR05ish" in out


def test_isnew_rule_provable_on_create_phase():
    out = find_staged_vr_conflict(ISNEWR, {"Reason__c": None})
    assert out is not None and "NewR" in out and "create" in out


# ---------------------------------------------------------------------------
# Percent — the false-refusal close and the true catch
# ---------------------------------------------------------------------------

_PCT = {"PLS_BM_Discount__c": "percent"}


def test_percent_20_no_longer_proves_a_fire_the_org_would_not_make():
    staged = {"PLS_BM_Discount__c": 20, "PLS_BM_Approval_Reason__c": None}
    assert find_staged_vr_conflict(VR02, staged, field_types=_PCT) is None


def test_percent_2001_still_proves_the_real_fire():
    staged = {"PLS_BM_Discount__c": 20.01, "PLS_BM_Approval_Reason__c": None}
    out = find_staged_vr_conflict(VR02, staged, field_types=_PCT)
    assert out is not None and "VR02" in out


def test_percent_without_types_keeps_old_raw_behaviour():
    staged = {"PLS_BM_Discount__c": 20, "PLS_BM_Approval_Reason__c": None}
    out = find_staged_vr_conflict(VR02, staged)
    assert out is not None      # raw 20 > 0.20 — the pre-D-443 (unsafe) proof


# ---------------------------------------------------------------------------
# RecordType
# ---------------------------------------------------------------------------

def test_record_type_resolves_and_proves():
    staged = {"RecordTypeId": "012OK0000000001AAA", "Disc__c": 101}
    out = find_staged_vr_conflict(
        RTR, staged,
        record_type_developer_name=lambda rid: "Enterprise")
    assert out is not None and "RTR" in out


def test_record_type_unresolvable_stays_unknown():
    staged = {"RecordTypeId": "012OK0000000001AAA", "Disc__c": 101}
    assert find_staged_vr_conflict(
        RTR, staged, record_type_developer_name=lambda rid: None) is None
    assert find_staged_vr_conflict(RTR, staged) is None   # no resolver
