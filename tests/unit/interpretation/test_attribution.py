"""Unit tests for S6 deeper attribution (D-111.1 slice 2a) — offline, stub
S1VrReader.

`attribute_run` enriches the two failed behavioral verdicts with a structured
`Cause` derived from S1's VR metadata (one per `cause_kind`), pass-through for
the rest, and never mutates the carried outcome.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from primeqa.execution_engine.evidence import (
    AssertEvidence,
    CleanupRecord,
    CreateAttemptEvidence,
    ReadEvidence,
    RunEvidence,
)
from primeqa.interpretation import (
    VrMeta,
    attribute_run,
    interpret_run,
)

_T = datetime(2026, 5, 27, tzinfo=timezone.utc)
_VR = "FIELD_CUSTOM_VALIDATION_EXCEPTION"


# ---------------------------------------------------------------------------
# Stub S1 reader + evidence builders
# ---------------------------------------------------------------------------

class _StubS1:
    def __init__(self, vrs):
        self._vrs = tuple(vrs)
        self.calls = []

    def vrs_for_object(self, subject_external_id):
        self.calls.append(subject_external_id)
        return self._vrs


def _run(*, outcome, create):
    return RunEvidence(
        run_id=uuid4(), recipe_id=uuid4(), recipe_version_seq=1,
        claim_test_id=uuid4(), claim_version_seq=None, environment_id=7,
        api_choice="rest", outcome=outcome, started_at=_T, finished_at=_T,
        steps=(create,))


def _create(*, success, matched, http_status, body, field_values=None, record_id=None):
    return CreateAttemptEvidence(
        step_id="create-violating", ordinal=0, sobject="Lead",
        field_values=field_values if field_values is not None else {"Reason__c": None},
        http_status=http_status, success=success,
        error_code=(body[0]["errorCode"] if body else None),
        message=(body[0].get("message") if body else None),
        rejection_body=tuple(body), matched=matched,
        cleanup=CleanupRecord(attempted=bool(record_id),
                              succeeded=True if record_id else None, record_id=record_id),
        started_at=_T, finished_at=_T, duration_ms=1)


def _interp(evidence, s1):
    """interpret (slice 1) then attribute (slice 2)."""
    return attribute_run(interpret_run(evidence), evidence, s1=s1)


# ---------------------------------------------------------------------------
# prohibition_not_enforced — (a) inactive / (b) drift / (c) enforcement gap
# ---------------------------------------------------------------------------

def test_vr_inactive():
    # the create's payload violates the VR's formula, but the VR is INACTIVE.
    ev = _run(outcome="failed", create=_create(
        success=True, matched=False, http_status=201, body=[], record_id="001Z"))
    s1 = _StubS1([VrMeta(name="RequireReason", is_active=False,
                         formula_text="ISBLANK(Reason__c)", error_message="reason required")])
    interp = _interp(ev, s1)
    assert interp.verdict == "prohibition_not_enforced"
    assert interp.cause.cause_kind == "vr_inactive"
    assert interp.cause.vr_name == "RequireReason"
    assert "inactive" in interp.attribution


def test_enforcement_gap():
    # the VR is ACTIVE and violated, yet the create succeeded — the real defect.
    ev = _run(outcome="failed", create=_create(
        success=True, matched=False, http_status=201, body=[], record_id="001Z"))
    s1 = _StubS1([VrMeta(name="RequireReason", is_active=True,
                         formula_text="ISBLANK(Reason__c)", error_message="reason required")])
    interp = _interp(ev, s1)
    assert interp.cause.cause_kind == "enforcement_gap"
    assert interp.cause.vr_name == "RequireReason"


def test_vr_formula_drift():
    # the active VR's current formula is EVALUABLE on the payload but NOT violated
    # → confirmed drift (the rule was edited). Adjusted (D-114): the old absent-field
    # formula `Amount__c = 99` is `NonEvaluable` under `evaluate` (not confirmed-False)
    # — that case is now `vr_formula_indeterminate` (see below). A genuinely-evaluable
    # not-violated formula tests *confirmed* drift.
    ev = _run(outcome="failed", create=_create(
        success=True, matched=False, http_status=201, body=[], record_id="001Z",
        field_values={"Status__c": "Final"}))
    s1 = _StubS1([VrMeta(name="RequireDraft", is_active=True,
                         formula_text='ISPICKVAL(Status__c, "Draft")', error_message="x")])
    interp = _interp(ev, s1)
    assert interp.cause.cause_kind == "vr_formula_drift"
    assert interp.cause.vr_name is None
    assert "edited since generation" in interp.attribution


def test_enforcement_gap_loosened_still_violating():
    # THE false-drift fix (D-114): the stored payload (Amount=99) STILL violates a
    # *loosened* current formula (Amount < 200), and the active VR didn't fire →
    # enforcement_gap, not drift. The old `derive` + subset-match proxy re-derived
    # `199`, missed the match, and mis-reported this as `vr_formula_drift`.
    ev = _run(outcome="failed", create=_create(
        success=True, matched=False, http_status=201, body=[], record_id="001Z",
        field_values={"Amount": 99}))
    s1 = _StubS1([VrMeta(name="AmountCap", is_active=True,
                         formula_text="Amount < 200", error_message="x")])
    interp = _interp(ev, s1)
    assert interp.cause.cause_kind == "enforcement_gap"
    assert interp.cause.vr_name == "AmountCap"


def test_vr_formula_indeterminate():
    # the active VR's current formula left the single-object evaluable subset
    # (org-state ISCHANGED) → violation can't be computed → indeterminate, NOT a
    # guessed drift (the old NotDerivable → drift collapse — D-114 fix). Carries the VR.
    ev = _run(outcome="failed", create=_create(
        success=True, matched=False, http_status=201, body=[], record_id="001Z",
        field_values={"Reason__c": None}))
    s1 = _StubS1([VrMeta(name="ChangedGuard", is_active=True,
                         formula_text="ISCHANGED(Status__c)", error_message="x")])
    interp = _interp(ev, s1)
    assert interp.cause.cause_kind == "vr_formula_indeterminate"
    assert interp.cause.vr_name == "ChangedGuard"
    assert "indeterminate" in interp.attribution


def test_no_active_vr():
    # no active VR enforces the prohibition (none on the object) → the residual the
    # old code mis-labeled as drift is now closed → no_active_vr (matches S8's leg).
    ev = _run(outcome="failed", create=_create(
        success=True, matched=False, http_status=201, body=[], record_id="001Z",
        field_values={"Reason__c": None}))
    interp = _interp(ev, _StubS1([]))                          # no VRs at all
    assert interp.cause.cause_kind == "no_active_vr"
    assert "deactivated" in interp.attribution


# ---------------------------------------------------------------------------
# rejected_unasserted_reason — other VR / platform constraint
# ---------------------------------------------------------------------------

def test_other_vr_fired():
    # rejected by a *different* VR (same code, message matches another VR).
    ev = _run(outcome="failed", create=_create(
        success=False, matched=False, http_status=400,
        body=[{"errorCode": _VR, "message": "You must select an Order Type."}]))
    s1 = _StubS1([
        VrMeta(name="RequireReason", is_active=True, formula_text="ISBLANK(Reason__c)",
               error_message="reason required"),
        VrMeta(name="OrderTypeRequired", is_active=True, formula_text="ISBLANK(Type__c)",
               error_message="You must select an Order Type."),
    ])
    interp = _interp(ev, s1)
    assert interp.verdict == "rejected_unasserted_reason"
    assert interp.cause.cause_kind == "other_vr_fired"
    assert interp.cause.vr_name == "OrderTypeRequired"


def test_platform_constraint():
    # rejected by a non-VR platform code (REQUIRED_FIELD_MISSING).
    ev = _run(outcome="failed", create=_create(
        success=False, matched=False, http_status=400,
        body=[{"errorCode": "REQUIRED_FIELD_MISSING", "message": "Required fields missing"}]))
    interp = _interp(ev, _StubS1([]))
    assert interp.cause.cause_kind == "platform_constraint"
    assert "REQUIRED_FIELD_MISSING" in interp.cause.detail


# ---------------------------------------------------------------------------
# Pass-through + discipline
# ---------------------------------------------------------------------------

def test_passed_is_passthrough_unchanged():
    ev = _run(outcome="passed", create=_create(
        success=False, matched=True, http_status=400,
        body=[{"errorCode": _VR, "message": "reason required"}]))
    base = interpret_run(ev)
    enriched = attribute_run(base, ev, s1=_StubS1([VrMeta("x", True)]))
    assert enriched == base                  # unchanged
    assert enriched.cause is None


def test_inspection_is_passthrough():
    read = ReadEvidence(
        step_id="r", ordinal=0, query="q", sobject="ValidationRule", edge="APPLIES_TO",
        subject_entity_type="Object", subject_external_id="Lead", row_count=0,
        rows=(), started_at=_T, finished_at=_T, duration_ms=1)
    assertion = AssertEvidence(
        step_id="a", ordinal=1, predicate="exists", subject_ref="r",
        evaluated_row_count=0, held=False, started_at=_T, finished_at=_T, duration_ms=0)
    ev = RunEvidence(
        run_id=uuid4(), recipe_id=uuid4(), recipe_version_seq=1, claim_test_id=uuid4(),
        claim_version_seq=None, environment_id=7, api_choice="metadata_api",
        outcome="failed", started_at=_T, finished_at=_T, steps=(read, assertion))
    base = interpret_run(ev)
    enriched = attribute_run(base, ev, s1=_StubS1([]))
    assert enriched == base and enriched.cause is None


def test_outcome_never_mutated_and_deterministic():
    ev = _run(outcome="failed", create=_create(
        success=True, matched=False, http_status=201, body=[], record_id="001Z"))
    s1 = _StubS1([VrMeta("RequireReason", True, "ISBLANK(Reason__c)", "x")])
    a, b = _interp(ev, s1), _interp(ev, s1)
    assert a.outcome == "failed"             # carried, never recomputed
    assert a == b                            # deterministic
