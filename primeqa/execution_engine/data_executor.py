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

The **2-step negative** (D-203) dispatches on a flagged update/delete: construct
the world + setup create (the positive's machinery) → attempt the prohibited
mutation → the same 4-way grading as the 1-step negative → teardown always. Any
setup failure is ``errored`` (the prohibition was never exercised), never
``failed``.
"""
from __future__ import annotations

import re
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from primeqa.execution_engine.errors import (
    AssertionResolutionError,
    PlanTranslationError,
    StepRefResolutionError,
    UnsupportedPredicateError,
)
from primeqa.execution_engine.evidence import (
    AssertEvidence,
    CleanupRecord,
    CreateAttemptEvidence,
    DataReadEvidence,
    DeleteAttemptEvidence,
    ErrorSurface,
    RunEvidence,
    UpdateAttemptEvidence,
)
from primeqa.execution_engine.plan import (
    DataRecipePlan,
    PlannedCreate,
    PlannedUpdate,
)
from primeqa.execution_engine.provisioning import CreatedRecordTracker
from primeqa.execution_engine.refs import resolve_field_value_refs, resolve_step_refs
from primeqa.execution_engine.world import (
    _sf_field,
    _sf_fields,
    build_world,
    construct_world,
    plan_world,
)
from primeqa.integrations.exceptions import SFClientError

# A Salesforce **business** rejection (validation rule, required-field,
# duplicate, …) surfaces as HTTP 400 with a structured error body. Any other
# non-2xx (401 / 403 / 429 / 5xx) is an infra / auth failure — the org did not
# perform a business evaluation, so the create "couldn't be attempted" → errored.
_BUSINESS_REJECTION_STATUS = 400


def execute_data_recipe(
    plan: DataRecipePlan, *, client, environment_id: int, s1=None,
    world_plans=None, record_sink=None,
) -> RunEvidence:
    """Execute a data-recipe plan against ``client``; dispatch on the first
    step's ``expect_rejection``.

    ``client`` is anything with ``create`` / ``update`` / ``delete`` (+
    ``query`` for the positive read-back) returning the normalized envelope
    (injected; unit tests drive a stub). Every path that constructs a world (the
    positive vertical AND the 2-step negative — both begin with an ordinary setup
    create) needs the operational-world reads resolved, supplied as EITHER a live
    ``s1`` (``SemanticOrgModel``-shaped requiredness reader; the sync path reads it
    mid-execute) OR a pre-resolved ``world_plans`` map (``{step_id: WorldPlan}``,
    D-230.2 — the async path pre-resolves it under the select bracket so execute
    holds no DB connection). The run path injects whichever applies when
    ``steps[0].expect_rejection is None``. Returns a :class:`RunEvidence` carrying the
    grounded outcome + per-step evidence."""
    flagged = next(
        (s for s in plan.steps
         if getattr(s, "expect_rejection", None) is not None), None)
    if flagged is not None and isinstance(flagged, PlannedCreate):
        # 1-step create-rejected (D-110.2) — no world to construct.
        return _execute_negative(
            plan, client=client, environment_id=environment_id)
    if s1 is None and world_plans is None:
        raise PlanTranslationError(
            "a data-recipe with an ordinary (setup) create needs its operational "
            "world resolved — a live S1 requiredness reader (s1=) or a pre-resolved "
            "world_plans map (D-230.2); neither was injected",
            recipe_id=plan.recipe_id)
    if flagged is not None:
        # 2-step setup create -> rejected update/delete (D-203).
        return _run_negative_with_setup(
            plan, client=client, environment_id=environment_id, s1=s1,
            world_plans=world_plans, record_sink=record_sink)
    return _run_positive(
        plan, client=client, environment_id=environment_id, s1=s1,
        world_plans=world_plans, record_sink=record_sink)


def _world_for(create, *, s1, client, tracker, at_seq, world_plans, recipe_id=None):
    """Resolve one create's operational world → ``(scalar_filler, parent_filler,
    unfillable)`` (D-230.2). With a pre-resolved ``world_plans`` map (async) it
    BUILDS from the detached ``WorldPlan`` (no S1 read); otherwise (sync) it reads
    S1 live via :func:`construct_world` exactly as before. A missing plan for a
    create that needs one is a planning defect — fail loud."""
    if world_plans is not None:
        wp = world_plans.get(create.step_id)
        if wp is None:
            raise PlanTranslationError(
                f"async data path: no pre-resolved WorldPlan for create step "
                f"{create.step_id!r} (plan_data_recipe_world must cover every "
                f"ordinary create)", recipe_id=recipe_id)
        return build_world(wp, client=client, tracker=tracker)
    return construct_world(
        create.target_object.external_id, set(create.field_values),
        s1=s1, client=client, tracker=tracker, at_seq=at_seq)


def plan_data_recipe_world(plan: DataRecipePlan, s1) -> dict:
    """Pre-resolve every ordinary create's operational world (D-230.2) into a
    detached ``{step_id: WorldPlan}`` map, reading S1 once (``at_seq`` pinned) under
    the caller's connection — the async SELECT bracket — so the execute bracket holds
    none. Covers the positive chain's creates AND the 2-step negative's setup create
    (a :class:`PlannedCreate` with no ``expect_rejection``); the 1-step
    create-rejected negative constructs no world (empty map)."""
    at_seq = s1.current_version_seq()
    plans: dict = {}
    for step in plan.steps:
        if (isinstance(step, PlannedCreate)
                and getattr(step, "expect_rejection", None) is None):
            plans[step.step_id] = plan_world(
                step.target_object.external_id, set(step.field_values),
                s1=s1, at_seq=at_seq)
    return plans


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


def _run_negative_with_setup(
    plan: DataRecipePlan, *, client, environment_id: int, s1=None,
    world_plans=None, record_sink=None,
) -> RunEvidence:
    """The 2-step behavioral negative (D-203): construct-world → setup
    create-expect-success → attempt the prohibited update/delete → grade the
    rejection (the same 4-way eval as the 1-step negative) → teardown (always).

    The plan is the bridge-guaranteed pair ``(PlannedCreate(no expectation),
    PlannedUpdate|PlannedDelete)``. **Any** setup failure — transport, an
    unfillable world, or the org rejecting the setup create itself — is
    ``errored``: the prohibition under test was never exercised, so there is
    nothing to grade (never ``failed``, which would falsely indict the org).
    """
    run_id = uuid4()
    started = _now()
    setup, mutation = plan.steps
    sobject = setup.target_object.external_id
    semantic_fields = set(setup.field_values)

    # 1. Construct the operational world for the setup create — the same
    #    machinery as the positive vertical (padding + parents + tracker, F6).
    #    D-230.2: live S1 read (sync) or a pre-resolved WorldPlan (async).
    at_seq = s1.current_version_seq() if s1 is not None else None
    tracker = CreatedRecordTracker(run_id=run_id, sink=record_sink)
    try:
        scalar_filler, parent_filler, unfillable = _world_for(
            setup, s1=s1, client=client, tracker=tracker, at_seq=at_seq,
            world_plans=world_plans, recipe_id=plan.recipe_id)
    except Exception as e:
        tracker.teardown(client, _best_effort_delete)
        err = ErrorSurface("construct", type(e).__name__, str(e))
        return _result(plan, run_id, started, environment_id, (), "errored", err,
                       created_records=tracker.records)
    if unfillable:
        tracker.teardown(client, _best_effort_delete)
        err = ErrorSurface(
            phase="construct", error_type="UnfillableWorld",
            message=("required field(s)/parent(s) S4 could not construct: "
                     + ", ".join(unfillable)))
        return _result(plan, run_id, started, environment_id, (), "errored", err,
                       created_records=tracker.records)

    field_values = _sf_fields(
        {**setup.field_values, **scalar_filler, **parent_filler}, sobject)

    # 2. The setup create — expect success.
    c_start = _now()
    try:
        env = client.create(sobject, field_values)
    except SFClientError as e:
        tracker.teardown(client, _best_effort_delete)
        err = ErrorSurface("create", type(e).__name__, str(e))
        ev = _evidence(
            setup, sobject, c_start, _now(), http_status=None, success=False,
            rejection_body=(), matched=None, cleanup=CleanupRecord(attempted=False),
            error=err, field_values=field_values)
        return _result(plan, run_id, started, environment_id, (ev,), "errored",
                       err, created_records=tracker.records)

    http_status = env["http_status"]
    if not env["success"]:
        tracker.teardown(client, _best_effort_delete)
        rejection_body = _as_error_tuple(env["api_response"]["body"])
        err = ErrorSurface(
            phase="create", error_type="SetupRejected",
            message=(f"setup create returned HTTP {http_status}; the negative "
                     f"could not be staged — the prohibited {mutation.kind} "
                     f"was never attempted"))
        ev = _evidence(
            setup, sobject, c_start, _now(), http_status=http_status,
            success=False, rejection_body=rejection_body, matched=None,
            cleanup=CleanupRecord(attempted=False), error=err,
            field_values=field_values)
        return _result(plan, run_id, started, environment_id, (ev,), "errored",
                       err, created_records=tracker.records)

    record_id = env["record_id"]
    c_end = _now()
    tracker.record(sobject, record_id)      # the subject joins after its parents

    # 3. The prohibited mutation — the same 4-way grading as the 1-step negative.
    mut_ev, outcome, top_error = _run_mutation_attempt(
        mutation, sobject, record_id, client)

    # 4. Teardown ALWAYS (reverse order: the subject, then any parents). The
    #    subject's CleanupRecord rides the setup create's evidence — the step
    #    that created the record (the established convention). A subject a
    #    wrongly-successful delete already removed 404s here; best-effort
    #    records that, never raises.
    cleanup = tracker.teardown(client, _best_effort_delete)[0]
    setup_ev = _evidence(
        setup, sobject, c_start, c_end, http_status=http_status, success=True,
        rejection_body=(), matched=None, cleanup=cleanup,
        field_values=field_values)

    return _result(plan, run_id, started, environment_id, (setup_ev, mut_ev),
                   outcome, top_error, created_records=tracker.records)


def _run_mutation_attempt(mutation, sobject, record_id, client):
    """Attempt the prohibited update/delete on the setup record + evaluate —
    the D-203 mirror of :func:`_run_create`'s 4-way grading. Returns
    (evidence, outcome, top_error)."""
    is_update = isinstance(mutation, PlannedUpdate)
    phase = "update" if is_update else "delete"
    changes = _sf_fields(mutation.field_changes, sobject) if is_update else None
    start = _now()
    try:
        if is_update:
            env = client.update(sobject, record_id, changes)
        else:
            env = client.delete(sobject, record_id)
    except SFClientError as e:
        end = _now()
        err = ErrorSurface(phase=phase, error_type=type(e).__name__,
                           message=str(e))
        ev = _mutation_evidence(
            mutation, sobject, record_id, changes, start, end,
            http_status=None, success=False, rejection_body=(), matched=None,
            error=err)
        return ev, "errored", err

    http_status = env["http_status"]
    rejection_body = _as_error_tuple(env["api_response"]["body"])
    end = _now()

    if env["success"]:
        # The prohibition did NOT enforce — the org accepted the mutation.
        ev = _mutation_evidence(
            mutation, sobject, record_id, changes, start, end,
            http_status=http_status, success=True, rejection_body=(),
            matched=False)
        return ev, "failed", None

    if http_status == _BUSINESS_REJECTION_STATUS:
        # The org evaluated + rejected on business rules — the grounded eval.
        matched = _matches(mutation.expect_rejection, rejection_body)
        ev = _mutation_evidence(
            mutation, sobject, record_id, changes, start, end,
            http_status=http_status, success=False,
            rejection_body=rejection_body, matched=matched)
        return ev, ("passed" if matched else "failed"), None

    err = ErrorSurface(
        phase=phase, error_type="UnexpectedResponse",
        message=f"{phase} returned HTTP {http_status} (not a business rejection)")
    ev = _mutation_evidence(
        mutation, sobject, record_id, changes, start, end,
        http_status=http_status, success=False, rejection_body=rejection_body,
        matched=None, error=err)
    return ev, "errored", err


def _mutation_evidence(mutation, sobject, record_id, changes, start, end, *,
                       http_status, success, rejection_body, matched,
                       error=None):
    """Assemble Update/DeleteAttemptEvidence. ``changes`` is the bare-ified
    payload actually PATCHed (the api_request analog); None for a delete."""
    first = rejection_body[0] if rejection_body else {}
    common = dict(
        step_id=mutation.step_id, ordinal=1, sobject=sobject,
        record_id=record_id, http_status=http_status, success=success,
        error_code=(first.get("errorCode") if isinstance(first, dict) else None),
        message=(first.get("message") if isinstance(first, dict) else None),
        rejection_body=rejection_body, matched=matched,
        error_fields=_named_fields(rejection_body),
        started_at=start, finished_at=end, duration_ms=_ms(start, end),
        error=error)
    if isinstance(mutation, PlannedUpdate):
        return UpdateAttemptEvidence(field_changes=dict(changes or {}), **common)
    return DeleteAttemptEvidence(**common)


# Predicates the positive ground step can faithfully evaluate. Side A emits
# `equals` (field == V) and `exists` (the read found a row — the cross-object
# automation-effect assert, D-210); others are deferred (fail-loud until built).
_SUPPORTED_DATA_PREDICATES = frozenset({"equals", "exists", "not_null"})

# D-210: a side-effect read may observe a record the org's automation creates
# moments AFTER the trigger create commits (async Flow paths). An empty read
# retries this many times total, with this pause between attempts, before the
# 0-row result stands. Same-record reads pass on attempt 1.
_READ_RETRY_ATTEMPTS = 3
_READ_RETRY_DELAY_S = 2.0




def _sf_soql(soql: str, sobject: str) -> str:
    """Bare-ify self-qualified field references in a SOQL string
    (``{sobject}.X`` → ``X``). ``FROM {sobject}`` (no trailing dot) and
    relationship paths (``Owner.Name``) are untouched."""
    return soql.replace(f"{sobject}.", "")


def _run_positive(plan: DataRecipePlan, *, client, environment_id: int, s1=None,
                  world_plans=None, record_sink=None) -> RunEvidence:
    """The positive create-and-verify path (D-115; N-create chains D-205):
    per create — construct-world → resolve cross-step refs → create-expect-
    success → thread state — then observe the read-back → teardown (k14,
    always, before grading) → ground ``field == V``.

    The plan is the bridge-guaranteed shape ``(PlannedCreate × N,
    PlannedDataRead, PlannedAssertion)``. The full outcome grammar is
    DECISIONS_LOG D-115.2; any pre-read failure tears down everything already
    built (creates + their provisioned parents) and surfaces honestly."""
    run_id = uuid4()
    started = _now()
    *creates, read, assertion = plan.steps

    # D-230.2: live S1 read (sync) or pre-resolved WorldPlans (async, world_plans).
    at_seq = s1.current_version_seq() if s1 is not None else None
    tracker = CreatedRecordTracker(run_id=run_id, sink=record_sink)
    state: dict[str, dict] = {}
    create_evs: list = []
    record_ids: list = []           # parallel to create_evs (None = not created)

    def _torn_down() -> tuple:
        """Teardown everything built (reverse order — children before parents,
        across all creates) and attach each create's CleanupRecord to its
        evidence BY RECORD ID (D-205 — provisioned parents interleave with the
        chain's creates, so index arithmetic would mis-attribute)."""
        cleanups = tracker.teardown(client, _best_effort_delete)
        by_id = {c.record_id: c for c in cleanups if c.record_id}
        return tuple(
            replace(ev, cleanup=by_id[rid]) if rid in by_id else ev
            for ev, rid in zip(create_evs, record_ids))

    for ordinal, create in enumerate(creates):
        sobject = create.target_object.external_id
        semantic_fields = set(create.field_values)      # the recipe-set keys (k16)

        # 1. Construct the operational world for THIS create — pad required
        #    scalars + recursively build required lookup/master-detail PARENTS
        #    (F6.2). Parents land on the shared tracker in creation order.
        try:
            scalar_filler, parent_filler, unfillable = _world_for(
                create, s1=s1, client=client, tracker=tracker, at_seq=at_seq,
                world_plans=world_plans, recipe_id=plan.recipe_id)
        except Exception as e:
            # ANY failure mid-construct (transport OR an S1 read raise) tears
            # down everything already built — including earlier chain creates.
            err = ErrorSurface("construct", type(e).__name__, str(e))
            return _result(plan, run_id, started, environment_id, _torn_down(),
                           "errored", err, created_records=tracker.records)
        if unfillable:
            err = ErrorSurface(
                phase="construct", error_type="UnfillableWorld",
                message=("required field(s)/parent(s) S4 could not construct: "
                         + ", ".join(unfillable)))
            return _result(plan, run_id, started, environment_id, _torn_down(),
                           "errored", err, created_records=tracker.records)

        # 2. Resolve cross-step references in the SEMANTIC values against the
        #    chain's accumulated state (D-205 — e.g. AccountId="$create-account
        #    .id"; padding never carries refs), then bare-ify the keys for the
        #    live API ({Object}.field → field).
        try:
            semantic_resolved = resolve_field_value_refs(create.field_values, state)
        except StepRefResolutionError as e:
            err = ErrorSurface("create", type(e).__name__, str(e))
            return _result(plan, run_id, started, environment_id, _torn_down(),
                           "errored", err, created_records=tracker.records)
        field_values = _sf_fields(
            {**semantic_resolved, **scalar_filler, **parent_filler}, sobject)

        # 3. Create — expect success.
        c_start = _now()
        try:
            env = client.create(sobject, field_values)
        except SFClientError as e:
            err = ErrorSurface("create", type(e).__name__, str(e))
            create_evs.append(_evidence(
                create, sobject, c_start, _now(), http_status=None,
                success=False, rejection_body=(), matched=None,
                cleanup=CleanupRecord(attempted=False), error=err,
                field_values=field_values, ordinal=ordinal))
            record_ids.append(None)
            return _result(plan, run_id, started, environment_id, _torn_down(),
                           "errored", err, created_records=tracker.records)

        http_status = env["http_status"]
        body = env["api_response"]["body"]
        record_id = env["record_id"]
        rejection_body = _as_error_tuple(body)

        if not env["success"]:
            # This create did not land — grade with THIS create's semantic
            # fields (D-115.2 disambiguation), tear down everything prior.
            # BARE-ified for the intersection: Salesforce's rejection `fields`
            # arrays carry bare names, while the recipe's semantic keys are
            # S1-qualified — comparing them un-bare-ified could never match
            # (a latent D-115.2 gap surfaced by the D-205 chain tests).
            outcome, top_error = _grade_rejected_create(
                http_status, rejection_body,
                {_sf_field(k, sobject) for k in semantic_fields})
            create_evs.append(_evidence(
                create, sobject, c_start, _now(), http_status=http_status,
                success=False, rejection_body=rejection_body, matched=None,
                cleanup=CleanupRecord(attempted=False),
                error=(top_error if outcome == "errored" else None),
                field_values=field_values, ordinal=ordinal))
            record_ids.append(None)
            return _result(plan, run_id, started, environment_id, _torn_down(),
                           outcome, top_error, created_records=tracker.records)

        # 4. Created → track (after its parents, so reverse teardown deletes it
        #    first) → thread the chain state for later creates' refs + the read.
        c_end = _now()
        tracker.record(sobject, record_id)
        state[create.step_id] = {"id": record_id}
        create_evs.append(_evidence(
            create, sobject, c_start, c_end, http_status=http_status,
            success=True, rejection_body=(), matched=None,
            cleanup=CleanupRecord(attempted=False),     # attached at teardown
            field_values=field_values, ordinal=ordinal))
        record_ids.append(record_id)

    # 5. Observe back (records still alive; state carries every create's id
    #    for the SOQL's $refs). D-210: an empty read retries a bounded number
    #    of times — a side-effect record may land moments after the trigger.
    read_sobject = read.target.external_id
    read_ev, read_err = _read_with_retry(
        read, read_sobject, state, client, ordinal=len(creates))

    # 5b. D-210: rows the read observed that S4 did NOT create are the org's
    #     automation-created side-effect records — register them so teardown
    #     removes them too (the test caused them; leaving them dirties the org
    #     run over run). Already-tracked ids (the same-record read) skip.
    if read_err is None and read_ev.row_count:
        known = {r.record_id for r in tracker.records}
        for row in read_ev.rows:
            rid = row.get("Id")
            if rid and rid not in known:
                tracker.record(read_sobject, rid)

    # 6. Teardown (k14) — every created record, reverse-order, BEFORE grading,
    #    so a later fail-loud ground never leaks them.
    create_steps = _torn_down()

    # 7. Ground (or errored when the record could not be observed). A 0-row
    #    read is `errored` only for a SELF-observation (the record the run
    #    created, read by Id — 0 rows is infrastructure); a SIDE-EFFECT read
    #    (queried by lookup — the AUTOMATION's product) keeps 0 rows as an
    #    evaluable finding for ANY predicate: the automation did not produce
    #    the record/value (D-227.7, surfaced by the first P1 perturbation —
    #    a Flow-off run must grade automation_not_triggered, not errored).
    if read_err is not None:
        return _result(
            plan, run_id, started, environment_id,
            create_steps + (read_ev,), "errored", read_err,
            created_records=tracker.records)
    if (read_ev.row_count == 0
            and assertion.predicate.predicate != "exists"
            and _is_self_observation(read.soql)):
        err = ErrorSurface(
            phase="read", error_type="RecordNotObserved",
            message=("read-back returned 0 rows; cannot evaluate field == V "
                     "(no immediate-consistency assumption)"))
        return _result(
            plan, run_id, started, environment_id,
            create_steps + (read_ev,), "errored", err,
            created_records=tracker.records)

    assert_ev = _run_ground(assertion, read_ev, ordinal=len(creates) + 1)
    outcome = "passed" if assert_ev.held else "failed"
    return _result(
        plan, run_id, started, environment_id,
        create_steps + (read_ev, assert_ev), outcome, None,
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


def _read_with_retry(read, sobject, state, client, *, ordinal):
    """D-210: issue the read, retrying an EMPTY result up to
    ``_READ_RETRY_ATTEMPTS`` times (``_READ_RETRY_DELAY_S`` apart) — the org's
    automation may commit its side effect moments after the trigger create.
    Transport errors and non-empty reads return immediately; the returned
    evidence records how many attempts were issued."""
    attempt = 0
    while True:
        attempt += 1
        ev, err = _run_read_back(read, sobject, state, client, ordinal=ordinal)
        if err is not None or ev.row_count > 0 or attempt >= _READ_RETRY_ATTEMPTS:
            if err is None and attempt > 1:
                ev = replace(ev, attempts=attempt)
            return ev, err
        time.sleep(_READ_RETRY_DELAY_S)


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


def _values_equal(observed, asserted) -> bool:
    """Typed-tolerant equality for read-back asserts (D-211). The asserted
    value is carried VERBATIM from the requirement (D-115 §2) — usually a
    string — while Salesforce returns typed JSON (Currency/Number → float,
    Checkbox → bool), so raw equality produces false failures
    (``"5000" != 5000.0``). Bools compare against 'true'/'false' strings and
    are excluded from numeric coercion (Python's ``True == 1`` must not
    leak); numbers compare numerically when both sides parse; strings stay
    strict — text asserts are never case-folded."""
    # Bool guard FIRST: Python's `True == 1.0` would otherwise leak through
    # the exact-equality check below.
    if isinstance(observed, bool) or isinstance(asserted, bool):
        if isinstance(observed, bool) and isinstance(asserted, bool):
            return observed is asserted
        if isinstance(observed, bool) and isinstance(asserted, str):
            return asserted.strip().lower() == str(observed).lower()
        if isinstance(asserted, bool) and isinstance(observed, str):
            return observed.strip().lower() == str(asserted).lower()
        return False
    if observed == asserted:
        return True
    try:
        return float(observed) == float(asserted)
    except (TypeError, ValueError):
        return False


def _run_ground(assertion, read_ev, *, ordinal) -> AssertEvidence:
    """Ground ``field == V``: resolve the predicate's ``subject_ref``
    (``<read_step>.<field>``) to the observed value in the read's single row and
    compare it to the carried ``value`` (typed-tolerant, D-211). Fail-loud
    (raise) on a non-equals predicate or a ``subject_ref`` that does not
    reference this read — a recipe defect (teardown has already run, so no
    record leaks)."""
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
    start = _now()
    if pred.predicate == "exists":
        # D-210: the cross-object automation-effect assert — did the read find
        # the side-effect record at all? No row indexing (0 rows is the honest
        # FAILED evaluation, not an error).
        held = read_ev.row_count > 0
    elif pred.predicate == "not_null":
        # D-227: the stamp assert — the automation wrote SOME value (e.g.
        # $Flow.CurrentDate has no stable literal to equals against). 0 rows
        # = couldn't observe → the honest FAILED evaluation.
        field = _sf_field(field, read_ev.sobject)
        held = (read_ev.row_count > 0
                and read_ev.rows[0].get(field) is not None)
    else:
        # The captured field is keyed bare in the SF response (see
        # _run_read_back). 0 rows reaches here only on a SIDE-EFFECT read
        # (D-227.7) — the automation produced nothing, so equality cannot
        # hold (the honest FAILED evaluation, mirroring exists/not_null).
        field = _sf_field(field, read_ev.sobject)
        held = (read_ev.row_count > 0
                and _values_equal(read_ev.rows[0].get(field), pred.value))
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
              field_values=None, ordinal=0) -> CreateAttemptEvidence:
    first = rejection_body[0] if rejection_body else {}
    # The actual posted payload — for the positive vertical that is the semantic
    # field + S4's operational padding; for the negative it is the create's own
    # field_values (the default). ``ordinal`` positions the step in an N-create
    # chain (D-205); the single-create verticals keep the default 0.
    posted = field_values if field_values is not None else create.field_values
    return CreateAttemptEvidence(
        step_id=create.step_id, ordinal=ordinal, sobject=sobject,
        field_values=dict(posted), http_status=http_status,
        success=success,
        error_code=(first.get("errorCode") if isinstance(first, dict) else None),
        message=(first.get("message") if isinstance(first, dict) else None),
        rejection_body=rejection_body, matched=matched, cleanup=cleanup,
        error_fields=_named_fields(rejection_body),
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


# A read that observes a record the RUN itself created: ``WHERE Id = '$…'``
# (the authored self/parent-observation shape). Side-effect reads query by a
# lookup column instead — their 0-row result is a finding, not infrastructure.
_SELF_OBSERVATION_WHERE = re.compile(r"WHERE\s+Id\s*=\s*'\$", re.IGNORECASE)


def _is_self_observation(soql: str) -> bool:
    """D-227.7: True when the read's authored SOQL targets a record this run
    created by Id — 0 rows is then RecordNotObserved (errored); False for a
    side-effect read (0 rows grades as the failed finding)."""
    return bool(_SELF_OBSERVATION_WHERE.search(soql or ""))


def _as_error_tuple(body) -> tuple:
    """Salesforce DML errors come back as a list of ``{errorCode, message,
    fields}``; occasionally a single object. Normalize to a tuple of dicts."""
    if isinstance(body, list):
        return tuple(body)
    if isinstance(body, dict):
        return (body,)
    return ()


def _named_fields(rejection_body) -> tuple:
    """D-225: every field named across the body's error entries — FLS/access
    denials name the blocked fields. Deduped, order-preserving."""
    out, seen = [], set()
    for e in rejection_body:
        if not isinstance(e, dict):
            continue
        for f in (e.get("fields") or ()):
            if f and f not in seen:
                seen.add(f)
                out.append(f)
    return tuple(out)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ms(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds() * 1000)
