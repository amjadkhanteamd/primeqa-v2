"""S6 full-surface verdicts (D-136) — the positive value-claim + property value
verdicts + regression. Pure: constructs `RunEvidence` and asserts the deterministic
verdict; no DB, no org.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from primeqa.execution_engine.evidence import (
    AssertEvidence,
    CleanupRecord,
    CreateAttemptEvidence,
    DataReadEvidence,
    ErrorSurface,
    ReadEvidence,
    RunEvidence,
)
from primeqa.interpretation.interpreter import interpret_run

_T = datetime(2026, 6, 3, tzinfo=timezone.utc)


def _run(outcome, steps, *, error=None):
    return RunEvidence(
        run_id=uuid4(), recipe_id=uuid4(), recipe_version_seq=1,
        claim_test_id=uuid4(), claim_version_seq=None, environment_id=7,
        api_choice="rest", outcome=outcome, started_at=_T, finished_at=_T,
        steps=tuple(steps), error=error)


def _create(*, success, matched=None, http_status=201):
    return CreateAttemptEvidence(
        step_id="create-record", ordinal=0, sobject="Account",
        field_values={"Status": "Active"}, http_status=http_status, success=success,
        error_code=None, message=None, rejection_body=(), matched=matched,
        cleanup=CleanupRecord(attempted=False), started_at=_T, finished_at=_T, duration_ms=1)


def _assert(*, predicate, held, ordinal=2):
    return AssertEvidence(
        step_id="assert-value", ordinal=ordinal, predicate=predicate,
        subject_ref="read.x", evaluated_row_count=1, held=held,
        started_at=_T, finished_at=_T, duration_ms=1)


def _data_read(*, row_count=1):
    return DataReadEvidence(
        step_id="read-created", ordinal=1,
        soql="SELECT Status FROM Account WHERE Id='x'", sobject="Account",
        fields_captured=("Status",), row_count=row_count,
        rows=({"Status": "Active"},) if row_count else (),
        started_at=_T, finished_at=_T, duration_ms=1)


def _meta_read(*, row_count, external_id="Account.Industry"):
    return ReadEvidence(
        step_id="read-subject", ordinal=0, query="SELECT ... FROM FieldDefinition ...",
        sobject="FieldDefinition", edge="sf_api_name", subject_entity_type="Field",
        subject_external_id=external_id, row_count=row_count,
        rows=tuple({"x": 1} for _ in range(row_count)),
        started_at=_T, finished_at=_T, duration_ms=1)


# ---------------------------------------------------------------------------
# Positive value-claim (the D-136 fix — previously mis-verdicted prohibition_*)
# ---------------------------------------------------------------------------

def test_positive_value_claim_passed_is_value_persisted():
    ev = _run("passed", [_create(success=True), _data_read(),
                         _assert(predicate="equals", held=True)])
    out = interpret_run(ev)
    assert out.verdict == "value_persisted"
    assert out.outcome == "passed"                 # carried verbatim
    assert "value persists" in out.attribution.lower()


def test_positive_value_claim_failed_is_value_not_persisted():
    ev = _run("failed", [_create(success=True), _data_read(),
                         _assert(predicate="equals", held=False)])
    out = interpret_run(ev)
    assert out.verdict == "value_not_persisted"
    assert "did not persist" in out.attribution.lower()


def test_positive_value_claim_errored_is_not_evaluated():
    # an errored positive (create succeeded, read-back couldn't be evaluated) has no
    # assert → the errored outcome grounds not_evaluated regardless of branch.
    ev = _run("errored", [_create(success=True), _data_read(row_count=0)],
              error=ErrorSurface(phase="read", error_type="SFRequestError", message="boom"))
    assert interpret_run(ev).verdict == "not_evaluated"


# ---------------------------------------------------------------------------
# Property inspection — value verdicts (D-136), distinct from existence presence
# ---------------------------------------------------------------------------

def test_property_equals_passed_is_value_matches():
    ev = _run("passed", [_meta_read(row_count=1), _assert(predicate="equals", held=True)])
    out = interpret_run(ev)
    assert out.verdict == "asserted_value_matches"
    assert "value" in out.attribution.lower()


def test_property_equals_failed_is_value_differs():
    # the field IS present (row_count=1); only its VALUE differs → not "metadata absent".
    ev = _run("failed", [_meta_read(row_count=1), _assert(predicate="equals", held=False)])
    out = interpret_run(ev)
    assert out.verdict == "asserted_value_differs"


def test_property_is_null_passed_is_value_matches():
    ev = _run("passed", [_meta_read(row_count=1), _assert(predicate="is_null", held=True)])
    assert interpret_run(ev).verdict == "asserted_value_matches"


# ---------------------------------------------------------------------------
# Existence / metadata-relationship — presence verdicts UNCHANGED (regression)
# ---------------------------------------------------------------------------

def test_existence_passed_is_metadata_present():
    ev = _run("passed", [_meta_read(row_count=1), _assert(predicate="exists", held=True)])
    assert interpret_run(ev).verdict == "asserted_metadata_present"


def test_existence_failed_is_metadata_absent():
    ev = _run("failed", [_meta_read(row_count=0), _assert(predicate="exists", held=False)])
    assert interpret_run(ev).verdict == "asserted_metadata_absent"


# ---------------------------------------------------------------------------
# Negative prohibition — UNCHANGED (regression: a single create, no assert)
# ---------------------------------------------------------------------------

def test_negative_prohibition_passed_unchanged():
    ev = _run("passed", [_create(success=False, matched=True, http_status=400)])
    assert interpret_run(ev).verdict == "prohibition_enforced"


def test_negative_prohibition_failed_unchanged():
    ev = _run("failed", [_create(success=True, matched=None, http_status=201)])
    assert interpret_run(ev).verdict == "prohibition_not_enforced"


# ---------------------------------------------------------------------------
# D-210 — claim_kind selects the positive vocabulary (same evidence shape)
# ---------------------------------------------------------------------------

def test_state_transition_passed_vocabulary():
    ev = _run("passed", [_create(success=True), _data_read(),
                         _assert(predicate="equals", held=True)])
    out = interpret_run(ev, claim_kind="state-transition-claim")
    assert out.verdict == "state_transitioned"
    assert "transition" in out.attribution.lower()


def test_state_transition_failed_vocabulary():
    ev = _run("failed", [_create(success=True), _data_read(),
                         _assert(predicate="equals", held=False)])
    out = interpret_run(ev, claim_kind="state-transition-claim")
    assert out.verdict == "state_not_transitioned"


def test_automation_effect_passed_vocabulary():
    ev = _run("passed", [_create(success=True), _data_read(),
                         _assert(predicate="exists", held=True)])
    out = interpret_run(ev, claim_kind="automation-effect-claim")
    assert out.verdict == "automation_triggered"
    assert "automation" in out.attribution.lower()


def test_automation_effect_failed_vocabulary():
    ev = _run("failed", [_create(success=True), _data_read(row_count=0),
                         _assert(predicate="exists", held=False)])
    out = interpret_run(ev, claim_kind="automation-effect-claim")
    assert out.verdict == "automation_not_triggered"


def test_unknown_or_none_claim_kind_keeps_value_vocabulary():
    ev = _run("passed", [_create(success=True), _data_read(),
                         _assert(predicate="equals", held=True)])
    assert interpret_run(ev).verdict == "value_persisted"
    assert interpret_run(ev, claim_kind="something-new").verdict == "value_persisted"


def test_claim_kind_does_not_touch_negatives():
    ev = _run("passed", [_create(success=False, matched=True)])
    out = interpret_run(ev, claim_kind="automation-effect-claim")
    assert out.verdict == "prohibition_enforced"
