"""Substrate-4 execution consumer + reaper ticks (D-131, Phase 3 slice B2).

The worker-layer loop that drives a run off the ``s4_execution_jobs`` queue.
Per-tenant; mirrors S3's ``consumer.py`` lifecycle (claim -> run -> complete/fail)
with per-tenant isolation.

The default ``run_fn`` is the **synchronous** :func:`run_recipe_execution_for_tenant`
— it runs **all** recipe kinds (metadata + data), holding a connection across the
run (boundary A). This is deliberate: the enqueue source must run data recipes,
and the async wrapper (:func:`run_recipe_execution_async`) **refuses** them (it
reads S1 mid-execute; metadata-path-only, D-129). Holding one connection per
in-flight job is the accepted low-volume interim; the data-path async bracketing
is the deferred-proper path (then ``run_fn`` becomes a metadata->async /
data->sync dispatcher).

Two injected seams (D-131.A):
  - ``client_resolver``: ``(tenant_id, environment_id) -> client`` — **optional**.
    The default sync ``run_fn`` self-resolves the per-kind client (Tooling for
    metadata, Data for data) *after* it selects the recipe — the consumer cannot
    know the kind up front, so it passes ``client=None``. A resolver is injected
    only by tests + a future async ``run_fn``.
  - ``run_fn``: defaults to :func:`run_recipe_execution_for_tenant`; injectable so
    the consumer's orchestration is tested without seeding a full approved recipe.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional
from uuid import UUID

from primeqa.execution_engine.jobs import ExecutionJobStore
from primeqa.execution_engine.run import run_recipe_execution_for_tenant

log = logging.getLogger(__name__)

# (tenant_id, environment_id) -> an authenticated SF client (Tooling/data).
ClientResolver = Callable[[int, Optional[int]], object]


def _classify_error(exc: Exception) -> str:
    """Thin error_code for the failed job (abort-on-error)."""
    from primeqa.execution_engine.errors import (
        CredentialResolutionError,
        ExecutionEngineError,
    )
    if isinstance(exc, CredentialResolutionError):
        return "credential_error"
    try:
        from primeqa.integrations.exceptions import SFClientError
        if isinstance(exc, SFClientError):
            return "sf_error"
    except Exception:  # pragma: no cover - import guard
        pass
    if isinstance(exc, ExecutionEngineError):
        return "execution_error"
    return "execution_error"


def process_execution_job_for_tenant(
    tenant_id: int, *, client_resolver: Optional[ClientResolver] = None,
    run_fn=run_recipe_execution_for_tenant,
) -> Optional[int]:
    """Claim and run one queued execution job for ``tenant_id``. Returns the
    processed job id, or ``None`` when the tenant's queue is empty.

    Flow: claim (SKIP LOCKED) -> ``heartbeat`` -> ``start_attempt`` -> resolve the
    client (worker-side) -> ``run_recipe_execution_async`` -> ``complete``. Any
    raise aborts this job to ``failed`` with a classified ``error_code`` and
    finalizes the attempt. A run that simply *returns* (even ``ran=False`` /
    ``errored``) completes the job — the worker did its job; the run outcome lives
    on the persisted ``s4_execution_runs`` row (S6's to interpret). The **claim is
    not wrapped** — an infrastructure failure propagates to the per-tenant boundary
    in :func:`run_s4_execution_tick`."""
    store = ExecutionJobStore(tenant_id)
    job = store.claim_next_queued_job()
    if job is None:
        return None
    store.heartbeat(job.id)
    store.start_attempt(job.id)
    try:
        # The default run_fn (sync run_recipe_execution_for_tenant) self-resolves
        # the per-kind client (Tooling for metadata, Data for data) AFTER it selects
        # the recipe — the consumer can't know the kind up front, so it passes
        # client=None. A client_resolver is injected only by tests + a future
        # async run_fn.
        client = (client_resolver(tenant_id, job.environment_id)
                  if client_resolver else None)
        result = run_fn(
            tenant_id, UUID(job.test_id),
            environment_id=job.environment_id, client=client)
        outcome = (result.evidence.outcome if (result.ran and result.evidence)
                   else (result.reason or "ran"))
        log.info("s4 execution job %s done (ran=%s, outcome=%s)",
                 job.id, result.ran, outcome)
        store.complete(job.id)
    except Exception as exc:
        log.warning("s4 execution job %s failed: %s", job.id, exc)
        store.fail(job.id, error_code=_classify_error(exc),
                   error_message=str(exc)[:500])
    return job.id


def run_s4_execution_tick(
    tenant_ids, *, client_resolver: Optional[ClientResolver] = None,
    run_fn=run_recipe_execution_for_tenant,
) -> dict[int, str]:
    """One job per tenant, with per-tenant isolation: a tenant whose
    claim/processing raises is logged and skipped — it never starves the others.
    Returns ``{tenant_id: outcome}`` (``processed:<id>`` / ``empty`` /
    ``error:<Type>``)."""
    outcomes: dict[int, str] = {}
    for tid in tenant_ids:
        try:
            jid = process_execution_job_for_tenant(
                tid, client_resolver=client_resolver, run_fn=run_fn)
            outcomes[tid] = f"processed:{jid}" if jid is not None else "empty"
        except Exception as exc:
            log.warning("s4_execution_tick: tenant %s failed: %s", tid, exc)
            outcomes[tid] = f"error:{type(exc).__name__}"
    return outcomes


def run_s4_reaper_tick(tenant_ids, *, stale_minutes: int = 10) -> dict[int, int]:
    """The scheduler's counterpart to the consumer tick: fail jobs stuck past the
    heartbeat timeout, per tenant, with per-tenant isolation (one tenant's failure
    never starves the others). Returns ``{tenant_id: reaped_count}`` (a tenant
    whose reap raises records 0)."""
    reaped: dict[int, int] = {}
    for tid in tenant_ids:
        try:
            reaped[tid] = ExecutionJobStore(tid).reap_stale_jobs(stale_minutes=stale_minutes)
        except Exception as exc:
            log.warning("s4_reaper_tick: tenant %s failed: %s", tid, exc)
            reaped[tid] = 0
    return reaped
