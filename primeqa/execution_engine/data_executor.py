"""The data-recipe behavioral-negative executor (SPEC §7, D-110.2 slice 2).

Attempts the planned create against a live org and renders the **grounded run
outcome** by matching the org's response to the recipe's ``RejectionExpectation``
— strictly stronger than v1's `expect_fail` sin (a bare flag that flipped *any*
failure to passed, never checking *why*). The 4-way eval:

  - create **rejected** (HTTP 400, business rejection) AND ``error_code``
    matches → **`passed`** (the prohibition enforced as asserted);
  - create **succeeds** (2xx) → **`failed`** (the prohibition did NOT enforce —
    the grounded analog of v1's `expected_fail_unverified`) + a **targeted
    best-effort delete** of the record it created (N-5 minimal-cleanup);
  - create **rejected but `error_code` doesn't match** → **`failed`** (rejected
    for the *wrong* reason — the exact case v1 wrongly flips to passed);
  - create **couldn't be attempted** (transport raise, or a non-400 error
    response — 401 / 403 / 429 / 5xx) → **`errored`**.

The **match-the-code** step is what grounds it. Produce-only: no DB import (the
inspection-executor discipline); mints its own ``run_id``; returns a
:class:`RunEvidence` with a :class:`CreateAttemptEvidence` step that the existing
``persist_run_evidence`` serializes unchanged.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from primeqa.execution_engine.evidence import (
    CleanupRecord,
    CreateAttemptEvidence,
    ErrorSurface,
    RunEvidence,
)
from primeqa.execution_engine.plan import DataRecipePlan
from primeqa.integrations.exceptions import SFClientError

# A Salesforce **business** rejection (validation rule, required-field,
# duplicate, …) surfaces as HTTP 400 with a structured error body. Any other
# non-2xx (401 / 403 / 429 / 5xx) is an infra / auth failure — the org did not
# perform a business evaluation, so the create "couldn't be attempted" → errored.
_BUSINESS_REJECTION_STATUS = 400


def execute_data_recipe(
    plan: DataRecipePlan, *, client, environment_id: int,
) -> RunEvidence:
    """Execute a behavioral-negative data-recipe plan against ``client``.

    ``client`` is anything with ``create(sobject, field_values) -> envelope``
    and ``delete(sobject, record_id) -> envelope`` (injected; unit tests drive a
    stub). Returns a :class:`RunEvidence` carrying the grounded outcome + the
    create-attempt evidence. Slice 1 guarantees the plan has exactly one
    :class:`PlannedCreate`."""
    run_id = uuid4()
    started = _now()
    create = plan.steps[0]
    sobject = create.target_object.external_id

    step, outcome, top_error = _run_create(create, sobject, client)

    finished = _now()
    return RunEvidence(
        run_id=run_id,
        recipe_id=plan.recipe_id,
        recipe_version_seq=plan.recipe_version_seq,
        claim_test_id=plan.claim_test_id,
        claim_version_seq=plan.claim_version_seq,
        environment_id=environment_id,
        api_choice=plan.api_choice,
        outcome=outcome,
        started_at=started,
        finished_at=finished,
        steps=(step,),
        error=top_error,
    )


def _run_create(create, sobject, client):
    """Attempt the create + evaluate. Returns (evidence, outcome, top_error)."""
    start = _now()
    try:
        env = client.create(sobject, create.field_values)
    except SFClientError as e:
        # Transport / network failure — the create couldn't be attempted.
        end = _now()
        err = ErrorSurface(
            phase="create", error_type=type(e).__name__, message=str(e))
        ev = _evidence(
            create, sobject, start, end, http_status=None, success=False,
            rejection_body=(), matched=None, cleanup=CleanupRecord(attempted=False),
            error=err)
        return ev, "errored", err

    http_status = env["http_status"]
    success = env["success"]
    body = env["api_response"]["body"]
    record_id = env["record_id"]
    rejection_body = _as_error_tuple(body)

    if success:
        # The prohibition did NOT enforce — failed. Clean up the record it made.
        cleanup = _best_effort_delete(client, sobject, record_id)
        end = _now()
        ev = _evidence(
            create, sobject, start, end, http_status=http_status, success=True,
            rejection_body=(), matched=False, cleanup=cleanup)
        return ev, "failed", None

    if http_status == _BUSINESS_REJECTION_STATUS:
        # The org evaluated + rejected on business rules — the grounded eval.
        matched = _matches(create.expect_rejection, rejection_body)
        end = _now()
        ev = _evidence(
            create, sobject, start, end, http_status=http_status, success=False,
            rejection_body=rejection_body, matched=matched,
            cleanup=CleanupRecord(attempted=False))
        return ev, ("passed" if matched else "failed"), None

    # A non-2xx, non-400 response (401 / 403 / 429 / 5xx) — not a business
    # rejection; the org didn't evaluate → couldn't attempt → errored.
    end = _now()
    err = ErrorSurface(
        phase="create", error_type="UnexpectedResponse",
        message=f"create returned HTTP {http_status} (not a business rejection)")
    ev = _evidence(
        create, sobject, start, end, http_status=http_status, success=False,
        rejection_body=rejection_body, matched=None,
        cleanup=CleanupRecord(attempted=False), error=err)
    return ev, "errored", err


def _evidence(create, sobject, start, end, *, http_status, success,
              rejection_body, matched, cleanup, error=None) -> CreateAttemptEvidence:
    first = rejection_body[0] if rejection_body else {}
    return CreateAttemptEvidence(
        step_id=create.step_id, ordinal=0, sobject=sobject,
        field_values=dict(create.field_values), http_status=http_status,
        success=success,
        error_code=(first.get("errorCode") if isinstance(first, dict) else None),
        message=(first.get("message") if isinstance(first, dict) else None),
        rejection_body=rejection_body, matched=matched, cleanup=cleanup,
        started_at=start, finished_at=end, duration_ms=_ms(start, end), error=error)


def _matches(expect, rejection_body) -> bool:
    """Grounded match: the rejection matches the expectation iff (any error's
    ``errorCode`` equals ``expect.error_code``) AND (if a message pattern is
    set, any error's message matches it). Robust to a multi-error body."""
    errors = [e for e in rejection_body if isinstance(e, dict)]
    codes = [e.get("errorCode") for e in errors]
    code_ok = (expect.error_code is None) or (expect.error_code in codes)
    if expect.error_message_pattern is None:
        msg_ok = True
    else:
        pat = expect.error_message_pattern
        msg_ok = any(re.search(pat, e.get("message") or "") for e in errors)
    return code_ok and msg_ok


def _best_effort_delete(client, sobject, record_id) -> CleanupRecord:
    """Targeted delete of the one record an unexpected success created. Never
    fatal: a failed delete is recorded, not raised; the outcome stays failed."""
    if not record_id:
        return CleanupRecord(attempted=False)
    try:
        env = client.delete(sobject, record_id)
        return CleanupRecord(
            attempted=True, succeeded=bool(env.get("success")), record_id=record_id)
    except SFClientError:
        return CleanupRecord(attempted=True, succeeded=False, record_id=record_id)


def _as_error_tuple(body) -> tuple:
    """Salesforce DML errors come back as a list of ``{errorCode, message,
    fields}``; occasionally a single object. Normalize to a tuple of dicts."""
    if isinstance(body, list):
        return tuple(body)
    if isinstance(body, dict):
        return (body,)
    return ()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ms(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds() * 1000)
