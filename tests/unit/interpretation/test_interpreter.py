"""Unit tests for the S6 deterministic interpreter (D-111 slice 1) — pure,
offline, constructed RunEvidence per outcome.

`interpret_run` maps S4's real `RunEvidence` (both verticals) to a structured
`Interpretation` — verdict + evidence-derived attribution + evidence refs, with
the outcome carried verbatim. Deterministic: same evidence → same interpretation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from primeqa.execution_engine.evidence import (
    AssertEvidence,
    CleanupRecord,
    CreateAttemptEvidence,
    ErrorSurface,
    ReadEvidence,
    RunEvidence,
)
from primeqa.interpretation import Interpretation, interpret_run

_T = datetime(2026, 5, 27, tzinfo=timezone.utc)
_VR = "FIELD_CUSTOM_VALIDATION_EXCEPTION"


# ---------------------------------------------------------------------------
# RunEvidence builders (the real S4 shape)
# ---------------------------------------------------------------------------

def _run(*, outcome, steps, error=None):
    return RunEvidence(
        run_id=uuid4(), recipe_id=uuid4(), recipe_version_seq=1,
        claim_test_id=uuid4(), claim_version_seq=None, environment_id=7,
        api_choice="rest", outcome=outcome, started_at=_T, finished_at=_T,
        steps=tuple(steps), error=error)


def _create(*, success, matched, http_status, body, record_id=None, error=None):
    return CreateAttemptEvidence(
        step_id="create-violating", ordinal=0, sobject="Lead",
        field_values={"Reason__c": None}, http_status=http_status, success=success,
        error_code=(body[0]["errorCode"] if body else None),
        message=(body[0].get("message") if body else None),
        rejection_body=tuple(body), matched=matched,
        cleanup=CleanupRecord(attempted=bool(record_id),
                              succeeded=True if record_id else None,
                              record_id=record_id),
        started_at=_T, finished_at=_T, duration_ms=1, error=error)


def _read(row_count):
    return ReadEvidence(
        step_id="read-subject", ordinal=0, query="SELECT ...",
        sobject="ValidationRule", edge="APPLIES_TO", subject_entity_type="Object",
        subject_external_id="Lead", row_count=row_count,
        rows=tuple({"Id": str(i)} for i in range(row_count)),
        started_at=_T, finished_at=_T, duration_ms=1)


def _assert(held):
    return AssertEvidence(
        step_id="assert-edge", ordinal=1, predicate="exists",
        subject_ref="read-subject", evaluated_row_count=1, held=held,
        started_at=_T, finished_at=_T, duration_ms=0)


# ---------------------------------------------------------------------------
# Behavioral negative — the four verdicts
# ---------------------------------------------------------------------------

def test_behavioral_passed_is_prohibition_enforced():
    ev = _run(outcome="passed", steps=[_create(
        success=False, matched=True, http_status=400,
        body=[{"errorCode": _VR, "message": "reason required"}])])
    interp = interpret_run(ev)

    assert isinstance(interp, Interpretation)
    assert interp.outcome == "passed"            # carried verbatim
    assert interp.verdict == "prohibition_enforced"
    assert "rejected as asserted" in interp.attribution
    assert interp.evidence_refs[0].step_id == "create-violating"


def test_behavioral_failed_success_is_not_enforced():
    ev = _run(outcome="failed", steps=[_create(
        success=True, matched=False, http_status=201, body=[], record_id="001Z")])
    interp = interpret_run(ev)
    assert interp.verdict == "prohibition_not_enforced"
    assert "SUCCEEDED" in interp.attribution
    assert "001Z" in interp.evidence_refs[0].detail


def test_behavioral_failed_wrong_code_is_rejected_unasserted():
    ev = _run(outcome="failed", steps=[_create(
        success=False, matched=False, http_status=400,
        body=[{"errorCode": "DUPLICATE_VALUE", "message": "dup"}])])
    interp = interpret_run(ev)
    assert interp.verdict == "rejected_unasserted_reason"
    assert "DUPLICATE_VALUE" in interp.attribution


def test_behavioral_errored_is_not_evaluated():
    err = ErrorSurface(phase="create", error_type="SFRequestError", message="boom")
    ev = _run(outcome="errored", steps=[_create(
        success=False, matched=None, http_status=None, body=[], error=err)],
        error=err)
    interp = interpret_run(ev)
    assert interp.verdict == "not_evaluated"
    assert "SFRequestError" in interp.attribution


# ---------------------------------------------------------------------------
# Inspection — the structural verdicts
# ---------------------------------------------------------------------------

def test_inspection_passed_is_metadata_present():
    ev = _run(outcome="passed", steps=[_read(2), _assert(True)])
    interp = interpret_run(ev)
    assert interp.verdict == "asserted_metadata_present"
    assert "Object Lead" in interp.attribution
    # refs cite both the read and the assert.
    assert {r.step_id for r in interp.evidence_refs} == {"read-subject", "assert-edge"}


def test_inspection_failed_is_metadata_absent():
    ev = _run(outcome="failed", steps=[_read(0), _assert(False)])
    interp = interpret_run(ev)
    assert interp.verdict == "asserted_metadata_absent"
    assert "absent" in interp.attribution


def test_inspection_errored_is_not_evaluated():
    err = ErrorSurface(phase="read", error_type="SFAuthError", message="401")
    ev = _run(outcome="errored", steps=[_read(0)], error=err)
    interp = interpret_run(ev)
    assert interp.verdict == "not_evaluated"
    assert "SFAuthError" in interp.attribution


# ---------------------------------------------------------------------------
# D-272 Slice 1: indeterminate-vs-permanent split on the not_evaluated path
# ---------------------------------------------------------------------------

def test_errored_indeterminate_is_marked_re_runnable():
    # an auth/transport error is could-not-evaluate → re-runnable; the verdict is
    # unchanged (not_evaluated) but the attribution says re-run, not "needs attention".
    err = ErrorSurface(phase="read", error_type="SFAuthError", message="401")
    ev = _run(outcome="errored", steps=[_read(0)], error=err)
    interp = interpret_run(ev)
    assert interp.verdict == "not_evaluated"          # verdict unchanged
    attr = interp.attribution.lower()
    assert "evidence is incomplete" in attr and attr.rstrip().endswith("re-run.")
    assert "needs attention" not in attr


def test_errored_permanent_is_not_re_runnable():
    # a normalization defect (our-side malformed/un-buildable test, e.g.
    # StepRefResolutionError) is permanent: re-running as-is won't change it.
    err = ErrorSurface(phase="construct", error_type="StepRefResolutionError",
                       message="ref unresolved")
    ev = _run(outcome="errored", steps=[_read(0)], error=err)
    interp = interpret_run(ev)
    assert interp.verdict == "not_evaluated"          # still not_evaluated, NOT failed
    attr = interp.attribution.lower()
    assert "needs attention" in attr and "will not change this" in attr
    assert "evidence is incomplete" not in attr


# ---------------------------------------------------------------------------
# Discipline: outcome carried (never recomputed) + deterministic
# ---------------------------------------------------------------------------

def test_outcome_is_carried_not_recomputed():
    # A constructed-inconsistent run (outcome=failed but a matched create) — the
    # interpreter restates outcome=failed verbatim; it does NOT flip to passed.
    ev = _run(outcome="failed", steps=[_create(
        success=False, matched=True, http_status=400,
        body=[{"errorCode": _VR, "message": "x"}])])
    interp = interpret_run(ev)
    assert interp.outcome == "failed"            # S4 owns the outcome; S6 restates it


def test_deterministic():
    ev = _run(outcome="passed", steps=[_create(
        success=False, matched=True, http_status=400,
        body=[{"errorCode": _VR, "message": "x"}])])
    a, b = interpret_run(ev), interpret_run(ev)
    assert a == b


# ---------------------------------------------------------------------------
# D-203 — the 2-step negative is graded against the MUTATION, never the setup
# ---------------------------------------------------------------------------

from primeqa.execution_engine.evidence import (  # noqa: E402
    DeleteAttemptEvidence,
    UpdateAttemptEvidence,
)


def _setup_create(record_id="006A"):
    """The 2-step negative's setup create — succeeded, subject torn down."""
    return CreateAttemptEvidence(
        step_id="create-setup", ordinal=0, sobject="Opportunity",
        field_values={"Amount": 500, "Name": "PQA"}, http_status=201,
        success=True, error_code=None, message=None, rejection_body=(),
        matched=None,
        cleanup=CleanupRecord(attempted=True, succeeded=True, record_id=record_id),
        started_at=_T, finished_at=_T, duration_ms=1)


def _mutation(kind, *, success, matched, http_status, body, error=None):
    common = dict(
        step_id=f"{kind}-violating", ordinal=1, sobject="Opportunity",
        record_id="006A", http_status=http_status, success=success,
        error_code=(body[0]["errorCode"] if body else None),
        message=(body[0].get("message") if body else None),
        rejection_body=tuple(body), matched=matched,
        started_at=_T, finished_at=_T, duration_ms=1, error=error)
    if kind == "update":
        return UpdateAttemptEvidence(field_changes={"Amount": 2000000}, **common)
    return DeleteAttemptEvidence(**common)


def test_two_step_passed_is_prohibition_enforced_on_the_update():
    ev = _run(outcome="passed", steps=[
        _setup_create(),
        _mutation("update", success=False, matched=True, http_status=400,
                  body=[{"errorCode": _VR, "message": "no"}])])
    interp = interpret_run(ev)
    assert interp.verdict == "prohibition_enforced"
    assert "violating update" in interp.attribution
    # The ref cites the MUTATION step, not the setup create.
    assert interp.evidence_refs[0].step_id == "update-violating"


def test_two_step_failed_success_is_not_enforced_never_misread_as_setup():
    # The regression D-203 exists to prevent: a successful setup create must
    # NOT be graded — the verdict reads the mutation's success.
    ev = _run(outcome="failed", steps=[
        _setup_create(),
        _mutation("update", success=True, matched=False, http_status=204, body=[])])
    interp = interpret_run(ev)
    assert interp.verdict == "prohibition_not_enforced"
    assert "violating update" in interp.attribution
    # The subject's id rides the evidence ref (the create vertical's convention).
    assert "006A" in interp.evidence_refs[0].detail


def test_two_step_wrong_code_is_rejected_unasserted():
    ev = _run(outcome="failed", steps=[
        _setup_create(),
        _mutation("delete", success=False, matched=False, http_status=400,
                  body=[{"errorCode": "DELETE_FAILED", "message": "ref"}])])
    interp = interpret_run(ev)
    assert interp.verdict == "rejected_unasserted_reason"
    assert "DELETE_FAILED" in interp.attribution


def test_two_step_errored_is_not_evaluated():
    err = ErrorSurface(phase="update", error_type="SFRequestError", message="down")
    ev = _run(outcome="errored", steps=[
        _setup_create(),
        _mutation("update", success=False, matched=None, http_status=None,
                  body=[], error=err)], error=err)
    interp = interpret_run(ev)
    assert interp.verdict == "not_evaluated"


def test_two_step_delete_passed_words_the_delete():
    ev = _run(outcome="passed", steps=[
        _setup_create(),
        _mutation("delete", success=False, matched=True, http_status=400,
                  body=[{"errorCode": _VR, "message": "no"}])])
    interp = interpret_run(ev)
    assert interp.verdict == "prohibition_enforced"
    assert "violating delete" in interp.attribution


def test_one_step_negative_dispatch_unchanged():
    # Regression: the 1-step create-rejected still grades the create.
    ev = _run(outcome="passed", steps=[_create(
        success=False, matched=True, http_status=400,
        body=[{"errorCode": _VR, "message": "x"}])])
    interp = interpret_run(ev)
    assert interp.verdict == "prohibition_enforced"
    assert "violating create" in interp.attribution
