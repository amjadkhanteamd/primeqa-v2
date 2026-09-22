"""Substrate-4 execution enqueue boundary — the mirror of S3's generation intake.

The thin seam that puts a recipe-execution job on the ``s4_execution_jobs`` queue;
the worker's ``s4_execution_tick`` then claims + runs it. Unlike S3's intake there
is **no S1-version pin and no requirement read** — the job row carries only
``(test_id, environment_id, created_by)`` and the recipe is selected at run time by
``test_id`` (S2). Caller-fed: the v1 route validates the environment (+ the prod
gate) and closes its db *before* calling this; this opens its own tenant connection
(the S3 seam discipline) and never touches the v1 / public schema.
"""
from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from primeqa.execution_engine.errors import UnexecutableClaimError
from primeqa.execution_engine.jobs import ExecutionJob, ExecutionJobStore

log = logging.getLogger(__name__)


def enqueue_s4_execution(
    *, tenant_id: int, test_id, environment_id: int,
    created_by: Optional[int] = None,
    plan_id: Optional[str] = None,
) -> ExecutionJob:
    """Get-or-create the **active** execution job for ``(test_id, environment_id)``
    — idempotent while active, re-runnable once terminal (D-130.A). ``test_id`` is
    the S2 claim/test id; ``environment_id`` is a route-validated v1 environment.

    Raises :class:`~primeqa.execution_engine.errors.UnexecutableClaimError`
    when every status-eligible recipe fails S4's shape dry-run (the D-223
    pre-enqueue gate) — the job would only die at run time, so the refusal
    happens here where the caller can surface it.

    Raises :class:`~primeqa.execution_engine.errors.PlanRequiredError` FIRST,
    before any other gate, when ``plan_id`` is absent (AUD-013): the plan is
    checked before the recipe so that a plan-less request is refused for the
    reason that is actually true of it.

    Returns the queued :class:`ExecutionJob` (the worker runs it on the next tick)."""
    from sqlalchemy.orm import Session

    from primeqa.execution_engine.errors import PlanRequiredError
    from primeqa.execution_engine.executability import gate_enqueue
    from primeqa.semantic.connection import get_tenant_connection

    if not plan_id:
        raise PlanRequiredError(test_id=test_id, where="intake")
    tid = UUID(str(test_id))
    with get_tenant_connection(tenant_id) as conn:
        session = Session(bind=conn)
        try:
            gate_enqueue(session, tid)
        finally:
            session.close()
    return ExecutionJobStore(tenant_id).create_or_get_job(
        test_id=tid,
        environment_id=int(environment_id),
        created_by=created_by,
        plan_id=plan_id,
    )

def plan_of_run(tenant_id: int, run_id) -> Optional[str]:
    """The recorded plan behind an existing run, or ``None`` for a legacy run.

    A repair re-run is a re-execution of THAT run, so it runs under the same
    plan (AUD-013). A legacy run carries no plan and stays that way — nothing
    is backfilled — so its re-run is refused by the intake with the plan named.
    """
    from sqlalchemy import text

    from primeqa.semantic.connection import get_tenant_connection
    if run_id is None:
        return None
    with get_tenant_connection(tenant_id) as conn:
        row = conn.execute(text(
            "SELECT CAST(plan_id AS text) FROM s4_execution_runs "
            "WHERE run_id = CAST(:r AS uuid) AND finished_at IS NOT NULL"), {"r": str(run_id)}).first()
    return row[0] if row and row[0] else None


def enqueue_claims_for_requirements(
    *, tenant_id: int, external_keys, environment_id: int,
    created_by: Optional[int] = None, plan_id: Optional[str] = None,
) -> dict:
    """D-219 slice 2: enqueue every APPROVED claim of the given requirement
    keys. The substrate analog of v1's "run the release's test plan" — one
    s4 job per distinct approved claim (idempotent while active, D-130.A).
    Returns ``{enqueued, claims, requirements}`` counts. Opens its own tenant
    session (the intake seam discipline)."""
    from sqlalchemy.orm import Session

    from primeqa.semantic.connection import get_tenant_connection
    from primeqa.test_representation.coordinator import (
        COVERAGE_LINK_KINDS,
        SemanticTransactionCoordinator,
    )

    keys = [k for k in (external_keys or []) if k]
    if keys and not plan_id:
        from primeqa.execution_engine.errors import PlanRequiredError
        raise PlanRequiredError(where="release fan-out")     # AUD-013: loud, before any read
    coord = SemanticTransactionCoordinator()
    test_ids, seen = [], set()
    with get_tenant_connection(tenant_id) as conn:
        session = Session(bind=conn)
        try:
            for key in keys:
                for m in coord.list_tests_by_requirement(
                        session, external_system="jira", external_key=key,
                        link_kind=COVERAGE_LINK_KINDS):
                    sid = str(m.test_id)
                    if sid in seen:
                        continue
                    seen.add(sid)
                    if coord.get_current_approved_claim(
                            session, m.test_id) is not None:
                        test_ids.append(m.test_id)
        finally:
            session.close()

    enqueued, skipped_unexecutable = 0, 0
    for tid in test_ids:
        try:
            enqueue_s4_execution(tenant_id=tenant_id, test_id=tid,
                                 environment_id=environment_id,
                                 created_by=created_by, plan_id=plan_id)
            enqueued += 1
        except UnexecutableClaimError as exc:
            # D-223: the gate refused the shape — skip, never abort the batch.
            log.info("enqueue skipped (unexecutable) tenant %s test %s: %s",
                     tenant_id, tid, exc)
            skipped_unexecutable += 1
    return {"enqueued": enqueued, "claims": len(test_ids),
            "requirements": len(keys),
            "skipped_unexecutable": skipped_unexecutable}
