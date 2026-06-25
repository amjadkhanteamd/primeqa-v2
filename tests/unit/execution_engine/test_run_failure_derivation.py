"""Phase 2 Slice A — the S4 run-level failure derivation at persist (pure, no DB).

``_run_failure`` promotes the run's top-level error surface (``evidence.error``)
to ``(failure_category, sf_error_code)``, reusing the shared taxonomy. The SF
errorCode comes from the erroring step (D-225 captured it on the mutation-attempt
evidence). No transport error → ``(None, None)``.
"""
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

from primeqa.execution_engine.evidence import ErrorSurface, failure_signature
from primeqa.execution_engine.result_store import _run_failure
from primeqa.integrations.failure_taxonomy import FailureCategory, is_indeterminate


def _ev(error, steps):
    """A duck-typed RunEvidence stand-in (only .error + .steps are read)."""
    return SimpleNamespace(error=error, steps=list(steps))


def test_no_error_is_none():
    # passed / assertion-failed / business-rejection runs carry no top error
    assert _run_failure(_ev(None, [])) == (None, None)


def test_permission_from_erroring_mutation_step_code():
    err = ErrorSurface(phase="create", error_type="SFRequestError", message="x")
    step = SimpleNamespace(error=err, error_code="INSUFFICIENT_FIELD_ACCESS")
    assert _run_failure(_ev(err, [step])) == \
        (FailureCategory.PERMISSION, "INSUFFICIENT_FIELD_ACCESS")


def test_auth_from_error_type_without_code():
    err = ErrorSurface(phase="read", error_type="SFAuthError", message="x")
    step = SimpleNamespace(error=err)          # read evidence carries no error_code
    assert _run_failure(_ev(err, [step])) == (FailureCategory.AUTH, None)


def test_unfillable_world_is_normalization():
    err = ErrorSurface(phase="construct", error_type="UnfillableWorld", message="x")
    assert _run_failure(_ev(err, [])) == (FailureCategory.NORMALIZATION, None)


def test_read_path_request_error_unknown_without_code():
    # read-path SFRequestError has no captured errorCode → coarse 'unknown'
    err = ErrorSurface(phase="read", error_type="SFRequestError", message="x")
    step = SimpleNamespace(error=err)
    assert _run_failure(_ev(err, [step])) == (FailureCategory.UNKNOWN, None)


def test_business_rejection_step_ignored():
    # a step with an errorCode but NO transport .error (a normal business
    # rejection) must not leak its code into a run that did not error
    err = ErrorSurface(phase="create", error_type="SFRequestError", message="x")
    rejection = SimpleNamespace(error=None, error_code="FIELD_CUSTOM_VALIDATION_EXCEPTION")
    erroring = SimpleNamespace(error=err, error_code="INSUFFICIENT_FIELD_ACCESS")
    cat, code = _run_failure(_ev(err, [rejection, erroring]))
    assert (cat, code) == (FailureCategory.PERMISSION, "INSUFFICIENT_FIELD_ACCESS")


# --- D-272 Slice 1: _run_failure delegates to the shared failure_signature ----

def test_run_failure_delegates_to_failure_signature():
    # the result store and S6 read the SAME category — _run_failure is now a thin
    # wrapper over evidence.failure_signature (the single derivation).
    err = ErrorSurface(phase="read", error_type="SFAuthError", message="x")
    ev = _ev(err, [SimpleNamespace(error=err)])
    assert _run_failure(ev) == failure_signature(ev) == (FailureCategory.AUTH, None)


def test_failure_signature_feeds_the_indeterminate_split():
    # the derived category drives is_indeterminate: an our-side normalization
    # defect is permanent; an auth/transport error is re-runnable.
    norm = ErrorSurface(phase="construct", error_type="UnfillableWorld", message="x")
    cat_norm, _ = failure_signature(_ev(norm, []))
    assert cat_norm == FailureCategory.NORMALIZATION
    assert is_indeterminate(cat_norm) is False

    auth = ErrorSurface(phase="read", error_type="SFAuthError", message="x")
    cat_auth, _ = failure_signature(_ev(auth, [SimpleNamespace(error=auth)]))
    assert is_indeterminate(cat_auth) is True
