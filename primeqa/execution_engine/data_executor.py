"""The data-recipe executor — behavioral negative (D-110.2) + positive create-and-verify (D-115).

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

The **positive** vertical (D-115) dispatches on a create with *no*
``expect_rejection``: construct the operational world (pad the required fields S4
can fill — k16) → create-expect-success → observe the record back (a distinct,
async-ready phase) → ground ``field == V`` → teardown (k14, always). The outcome
grammar — incl. the 400-rejection disambiguation by offending field (semantic →
``failed`` / padding → ``errored``) — is DECISIONS_LOG D-115.2. Still produce-only;
it reads S1 requiredness through an injected ``SemanticOrgModel`` port (``s1=``),
never its own SQL.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from primeqa.execution_engine.errors import (
    AssertionResolutionError,
    PlanTranslationError,
    UnsupportedPredicateError,
)
from primeqa.execution_engine.evidence import (
    AssertEvidence,
    CleanupRecord,
    CreateAttemptEvidence,
    DataReadEvidence,
    ErrorSurface,
    RunEvidence,
)
from primeqa.execution_engine.plan import DataRecipePlan
from primeqa.execution_engine.provisioning import CreatedRecordTracker
from primeqa.execution_engine.refs import resolve_step_refs
from primeqa.execution_engine.world import construct_world
from primeqa.integrations.exceptions import SFClientError

# A Salesforce **business** rejection (validation rule, required-field,
# duplicate, …) surfaces as HTTP 400 with a structured error body. Any other
# non-2xx (401 / 403 / 429 / 5xx) is an infra / auth failure — the org did not
# perform a business evaluation, so the create "couldn't be attempted" → errored.
_BUSINESS_REJECTION_STATUS = 400


def execute_data_recipe(
    plan: DataRecipePlan, *, client, environment_id: int, s1=None,
) -> RunEvidence:
    """Execute a data-recipe plan against ``client``; dispatch on the first
    step's ``expect_rejection``.

    ``client`` is anything with ``create`` / ``delete`` (+ ``query`` for the
    positive read-back) returning the normalized envelope (injected; unit tests
    drive a stub). ``s1`` is a ``SemanticOrgModel``-shaped requiredness reader,
    **required for the positive vertical** (the run path injects it; the negative
    ignores it). Returns a :class:`RunEvidence` carrying the grounded outcome +
    per-step evidence."""
    create = plan.steps[0]
    if getattr(create, "expect_rejection", None) is not None:
        return _execute_negative(
            plan, client=client, environment_id=environment_id)
    if s1 is None:
        raise PlanTranslationError(
            "a positive data-recipe needs an S1 requiredness reader (s1=); none "
            "was injected", recipe_id=plan.recipe_id)
    return _run_positive(
        plan, client=client, environment_id=environment_id, s1=s1)


def _execute_negative(
    plan: DataRecipePlan, *, client, environment_id: int,
) -> RunEvidence:
    """The behavioral-negative path (D-110.2): a single create the org should
    reject; the 4-way create-reject eval grounds the outcome."""
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


# Predicates the positive ground step can faithfully evaluate. Side A emits
# `equals`; others are deferred (fail-loud until built).
_SUPPORTED_DATA_PREDICATES = frozenset({"equals"})


def _sf_field(name: str, sobject: str) -> str:
    """An S1 *qualified* field name (``{Object}.{field}``) → its bare Salesforce
    API name (``{field}``). S1 names fields object-qualified for graph uniqueness
    (``sync.phases`` field phase); the live REST / SOQL API speaks **bare** names.
    A name without the ``{sobject}.`` self-prefix — already bare, or a relationship
    path like ``Owner.Name`` — passes through unchanged."""
    return name.removeprefix(f"{sobject}.")


def _sf_fields(field_values: dict, sobject: str) -> dict:
    """Bare-ify the keys of a create payload (recipe field(s) + operational
    padding) for the live create."""
    return {_sf_field(k, sobject): v for k, v in field_values.items()}


def _sf_soql(soql: str, sobject: str) -> str:
    """Bare-ify self-qualified field references in a SOQL string
    (``{sobject}.X`` → ``X``). ``FROM {sobject}`` (no trailing dot) and
    relationship paths (``Owner.Name``) are untouched."""
    return soql.replace(f"{sobject}.", "")


def _run_positive(plan: DataRecipePlan, *, client, environment_id: int, s1) -> RunEvidence:
    """The positive create-and-verify path (D-115): construct-world →
    create-expect-success → observe → ground ``field == V`` → teardown (k14).

    The plan is the bridge-guaranteed triple ``(PlannedCreate, PlannedDataRead,
    PlannedAssertion)``. The full outcome grammar is DECISIONS_LOG D-115.2."""
    run_id = uuid4()
    started = _now()
    create, read, assertion = plan.steps
    sobject = create.target_object.external_id
    semantic_fields = set(create.field_values)      # the recipe-set keys (k16)

    # 1. Construct the operational world — pad required scalars + recursively
    #    build required lookup/master-detail PARENTS (F6.2). Every created record
    #    (parents, in creation order) lands on the tracker; the target joins last.
    at_seq = s1.current_version_seq()
    tracker = CreatedRecordTracker()
    try:
        scalar_filler, parent_filler, unfillable = construct_world(
            sobject, semantic_fields, s1=s1, client=client, tracker=tracker,
            at_seq=at_seq)
    except Exception as e:
        # ANY failure mid-construct — a transport raise (SFClientError) OR an S1
        # read raise (VersionNotFoundError / ValueError) — must tear down any
        # parents already built before surfacing the errored run. Catching only
        # SFClientError here would leak a built parent on an S1 read error.
        tracker.teardown(client, _best_effort_delete)
        err = ErrorSurface("construct", type(e).__name__, str(e))
        return _result(plan, run_id, started, environment_id, (), "errored", err,
                       created_records=tracker.records)
    if unfillable:
        # A required field/parent could not be constructed — tear down any parents
        # already built (a later required-ref failed after an earlier one landed).
        tracker.teardown(client, _best_effort_delete)
        err = ErrorSurface(
            phase="construct", error_type="UnfillableWorld",
            message=("required field(s)/parent(s) S4 could not construct: "
                     + ", ".join(unfillable)))
        return _result(plan, run_id, started, environment_id, (), "errored", err,
                       created_records=tracker.records)

    # Bare-ify field names for the live API: the recipe + padding speak S1's
    # object-qualified names ({Object}.field); Salesforce creates want bare names.
    field_values = _sf_fields(
        {**create.field_values, **scalar_filler, **parent_filler}, sobject)

    # 2. Create the target — expect success.
    c_start = _now()
    try:
        env = client.create(sobject, field_values)
    except SFClientError as e:
        tracker.teardown(client, _best_effort_delete)   # tear down built parents
        err = ErrorSurface("create", type(e).__name__, str(e))
        ev = _evidence(
            create, sobject, c_start, _now(), http_status=None, success=False,
            rejection_body=(), matched=None, cleanup=CleanupRecord(attempted=False),
            error=err, field_values=field_values)
        return _result(plan, run_id, started, environment_id, (ev,), "errored", err,
                       created_records=tracker.records)

    http_status = env["http_status"]
    success = env["success"]
    body = env["api_response"]["body"]
    record_id = env["record_id"]
    rejection_body = _as_error_tuple(body)

    if not success:
        # The target create did not land — no target record made; tear down the
        # parents (if any) that were built for it.
        tracker.teardown(client, _best_effort_delete)
        outcome, top_error = _grade_rejected_create(
            http_status, rejection_body, semantic_fields)
        ev = _evidence(
            create, sobject, c_start, _now(), http_status=http_status,
            success=False, rejection_body=rejection_body, matched=None,
            cleanup=CleanupRecord(attempted=False),
            error=(top_error if outcome == "errored" else None),
            field_values=field_values)
        return _result(
            plan, run_id, started, environment_id, (ev,), outcome, top_error,
            created_records=tracker.records)

    # 3. Create succeeded → record the target (LAST, so reverse teardown deletes
    #    it before its parents) → observe the record back (a distinct, async-ready
    #    phase: no immediate-consistency assumption is baked in here).
    c_end = _now()
    tracker.record(sobject, record_id)          # F6.2: target after its parents
    state = {create.step_id: {"id": record_id}}
    read_ev, read_err = _run_read_back(read, sobject, state, client, ordinal=1)

    # 4. Teardown (k14) — every created record (the target, + from F6.2 any
    #    provisioned parents), **reverse-order**, *before* grading, so a later
    #    fail-loud ground never leaks them. The create_ev carries the target's
    #    cleanup (reverse-order teardown → index 0 is the last-created = target).
    cleanup = tracker.teardown(client, _best_effort_delete)[0]
    create_ev = _evidence(
        create, sobject, c_start, c_end, http_status=http_status, success=True,
        rejection_body=(), matched=None, cleanup=cleanup, field_values=field_values)

    # 5. Ground field == V (or errored when the record could not be observed).
    if read_err is not None:
        return _result(
            plan, run_id, started, environment_id,
            (create_ev, read_ev), "errored", read_err,
            created_records=tracker.records)
    if read_ev.row_count == 0:
        err = ErrorSurface(
            phase="read", error_type="RecordNotObserved",
            message=("read-back returned 0 rows; cannot evaluate field == V "
                     "(no immediate-consistency assumption)"))
        return _result(
            plan, run_id, started, environment_id,
            (create_ev, read_ev), "errored", err,
            created_records=tracker.records)

    assert_ev = _run_ground(assertion, read_ev, ordinal=2)
    outcome = "passed" if assert_ev.held else "failed"
    return _result(
        plan, run_id, started, environment_id,
        (create_ev, read_ev, assert_ev), outcome, None,
        created_records=tracker.records)


def _grade_rejected_create(http_status, rejection_body, semantic_fields):
    """Grade a create the org refused. A 400 business rejection is disambiguated
    by offending field: the **semantic** field named → ``failed`` (the value is
    not achievable — the finding); only **padding** named → ``errored`` (S4's own
    operational gap); none named → ``errored`` (ambiguous). Any other non-2xx →
    ``errored`` (the org did not business-evaluate). Returns (outcome, top_error|
    None — None only when the outcome is a clean ``failed``)."""
    if http_status != _BUSINESS_REJECTION_STATUS:
        return "errored", ErrorSurface(
            "create", "UnexpectedResponse",
            f"create returned HTTP {http_status} (not a business rejection)")
    offending = _offending_fields(rejection_body)
    if offending & semantic_fields:
        return "failed", None       # the requirement's value is not achievable
    if offending:
        return "errored", ErrorSurface(
            "create", "PaddingRejection",
            f"create rejected on operational field(s) {sorted(offending)}; none "
            f"is the semantic field under test")
    return "errored", ErrorSurface(
        "create", "AmbiguousRejection",
        "create rejected with no field attribution; cannot ascribe it to the "
        "value under test")


def _offending_fields(rejection_body) -> set:
    """The union of all ``fields`` arrays across a (possibly multi-error) DML
    rejection body."""
    out: set = set()
    for e in rejection_body:
        if isinstance(e, dict):
            for f in (e.get("fields") or []):
                out.add(f)
    return out


def _run_read_back(read, sobject, state, client, *, ordinal):
    """Resolve the read's ``$<step>.id`` reference(s), issue the SOQL, capture
    the row(s). Returns (:class:`DataReadEvidence`, error|None). A transport
    failure → error → the caller renders ``errored``; an unresolved reference
    fail-loud (:class:`StepRefResolutionError`) propagates (a recipe defect)."""
    # Bare-ify the SOQL + captured field names for the live API (SF returns rows
    # keyed by bare names); resolve the $<step>.id ref first so the WHERE id is intact.
    soql = _sf_soql(resolve_step_refs(read.soql or "", state), sobject)
    captured = tuple(_sf_field(f, sobject) for f in read.fields_to_capture)
    start = _now()
    try:
        rows = client.query(soql)
    except SFClientError as e:
        end = _now()
        err = ErrorSurface("read", type(e).__name__, str(e))
        ev = DataReadEvidence(
            step_id=read.step_id, ordinal=ordinal, soql=soql, sobject=sobject,
            fields_captured=captured, row_count=0, rows=(),
            started_at=start, finished_at=end, duration_ms=_ms(start, end),
            error=err)
        return ev, err
    end = _now()
    ev = DataReadEvidence(
        step_id=read.step_id, ordinal=ordinal, soql=soql, sobject=sobject,
        fields_captured=captured, row_count=len(rows),
        rows=tuple(rows), started_at=start, finished_at=end,
        duration_ms=_ms(start, end))
    return ev, None


def _run_ground(assertion, read_ev, *, ordinal) -> AssertEvidence:
    """Ground ``field == V``: resolve the predicate's ``subject_ref``
    (``<read_step>.<field>``) to the observed value in the read's single row and
    compare it to the carried ``value`` verbatim. Fail-loud (raise) on a
    non-equals predicate or a ``subject_ref`` that does not reference this read —
    a recipe defect (teardown has already run, so no record leaks)."""
    pred = assertion.predicate
    if pred.predicate not in _SUPPORTED_DATA_PREDICATES:
        raise UnsupportedPredicateError(
            f"assertion {assertion.step_id!r} uses predicate {pred.predicate!r}; "
            f"the positive vertical evaluates only "
            f"{sorted(_SUPPORTED_DATA_PREDICATES)}")
    step_ref, _, field = pred.subject_ref.partition(".")
    if not field or step_ref != read_ev.step_id:
        raise AssertionResolutionError(
            f"assertion {assertion.step_id!r} subject_ref {pred.subject_ref!r} "
            f"must be '{read_ev.step_id}.<field>' (the read-back's captured field)")
    # The captured field is keyed bare in the SF response (see _run_read_back).
    field = _sf_field(field, read_ev.sobject)
    start = _now()
    observed = read_ev.rows[0].get(field)
    held = observed == pred.value
    end = _now()
    return AssertEvidence(
        step_id=assertion.step_id, ordinal=ordinal, predicate=pred.predicate,
        subject_ref=pred.subject_ref, evaluated_row_count=read_ev.row_count,
        held=held, started_at=start, finished_at=end, duration_ms=_ms(start, end))


def _result(plan, run_id, started, environment_id, steps, outcome, error,
            created_records=()) -> RunEvidence:
    """Assemble the positive vertical's :class:`RunEvidence` envelope."""
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
        finished_at=_now(),
        steps=steps,
        error=error,
        created_records=created_records,
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
              rejection_body, matched, cleanup, error=None,
              field_values=None) -> CreateAttemptEvidence:
    first = rejection_body[0] if rejection_body else {}
    # The actual posted payload — for the positive vertical that is the semantic
    # field + S4's operational padding; for the negative it is the create's own
    # field_values (the default).
    posted = field_values if field_values is not None else create.field_values
    return CreateAttemptEvidence(
        step_id=create.step_id, ordinal=0, sobject=sobject,
        field_values=dict(posted), http_status=http_status,
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
    except Exception:
        # Best-effort: a failed delete (transport OR anything else) is recorded,
        # never raised — teardown must not be able to flip the outcome or escape.
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
