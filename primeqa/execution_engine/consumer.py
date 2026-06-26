"""Substrate-4 execution consumer + reaper ticks (D-131, Phase 3 slice B2).

The worker-layer loop that drives a run off the ``s4_execution_jobs`` queue.
Per-tenant; mirrors S3's ``consumer.py`` lifecycle (claim -> run -> complete/fail)
with per-tenant isolation.

The default ``run_fn`` is the **async** :func:`run_recipe_execution_async` — it runs
**all** recipe kinds (metadata + data) under the brief-transaction bracket, so NO DB
connection is held across Salesforce I/O (D-129 metadata; D-230.2 lifted the data-path
deferral via WorldPlan pre-resolution). The async path self-resolves the kind-aware
client + (for data) the operational-world snapshot in its select bracket, so the
consumer passes ``client=None`` and holds no connection per in-flight job. The
synchronous :func:`run_recipe_execution_for_tenant` remains the live-proven fallback
(the UI "Run" path), injectable as ``run_fn`` for callers that want boundary A.

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
from primeqa.execution_engine.run import async_run_claim_execution_for_tenant

log = logging.getLogger(__name__)


def _async_log_outcome(result) -> str:
    """A log-friendly outcome string for EITHER async run-result shape (D-284,
    Slice 4e). A single :class:`RunPathResult` carries ``.evidence.outcome``; a
    :class:`RunAllResult` (run-all, NO ``.evidence``) carries ``.probes`` /
    ``.batch_id`` — report the batch, never assume ``.evidence`` (which would
    ``AttributeError``). Does NOT compute Verified (the decision engine reads the
    batch, 4d). The single shape returns the EXACT prior expression — byte-identical."""
    if hasattr(result, "batch_id"):              # RunAllResult (run-all batch shape)
        probes = getattr(result, "probes", ()) or ()
        return f"batch {result.batch_id}: {len(probes)} probe(s)"
    return (result.evidence.outcome if (result.ran and result.evidence)
            else (result.reason or "ran"))

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
    run_fn=async_run_claim_execution_for_tenant,
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
    result = None
    try:
        # The default run_fn (async run_recipe_execution_async) self-resolves the
        # per-kind client (Tooling for metadata, Data for data) in its SELECT bracket
        # AFTER it reads the recipe kind — the consumer can't know the kind up front,
        # so it passes client=None (D-230.2). A client_resolver is injected only by
        # tests + the sync fallback run_fn.
        client = (client_resolver(tenant_id, job.environment_id)
                  if client_resolver else None)
        result = run_fn(
            tenant_id, UUID(job.test_id),
            environment_id=job.environment_id, client=client)
        # D-284 (Slice 4e): route handled which run path ran; the result is a
        # single RunPathResult or a run-all RunAllResult. _async_log_outcome handles
        # BOTH (a RunAllResult has no .evidence — the old direct read crashed).
        outcome = _async_log_outcome(result)
        log.info("s4 execution job %s done (ran=%s, outcome=%s)",
                 job.id, result.ran, outcome)
        store.complete(job.id)
    except Exception as exc:
        log.warning("s4 execution job %s failed: %s", job.id, exc)
        store.fail(job.id, error_code=_classify_error(exc),
                   error_message=str(exc)[:500])
    # D-234: best-effort run-failure notification for this UNATTENDED run (the queue
    # is the scheduled / auto-enqueued / CI path; live UI runs are watched). OUTSIDE
    # the try/except above so a notify problem can NEVER route to store.fail and
    # wrongly fail an already-completed job; the notifier itself never raises +
    # skips quarantined claims (D-232). Fires only on a real did-not-pass outcome.
    try:
        ev = getattr(result, "evidence", None)
        if result is not None and result.ran and ev is not None \
                and ev.outcome in ("failed", "errored"):
            from primeqa.shared.notifications import notify_substrate_run_failed
            notify_substrate_run_failed(
                tenant_id, run_id=str(ev.run_id), test_id=str(ev.claim_test_id),
                environment_id=ev.environment_id, outcome=ev.outcome,
                error_message=(ev.error.message if ev.error else None))
    except Exception:                                # pragma: no cover
        log.exception("s4 run-failure notify raised; job %s already finalized",
                      job.id)
    return job.id


def run_s4_execution_tick(
    tenant_ids, *, client_resolver: Optional[ClientResolver] = None,
    run_fn=async_run_claim_execution_for_tenant,
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


def run_s4_cleanup_reaper_tick(
    tenant_ids, *, stale_minutes: int = 15,
) -> dict[int, int]:
    """The crash-recovery cleanup reaper (D-230): delete Salesforce records a
    crashed run left stranded (an ``s4_created_records`` row still ``cleaned=false``
    past the grace window). Per-tenant isolation — one tenant's failure never
    starves the others. Returns ``{tenant_id: reaped_count}``. Distinct from
    :func:`run_s4_reaper_tick` (which reaps stuck JOBS, not orphaned org records)."""
    from primeqa.execution_engine.stranded_cleanup import reap_stranded_records

    reaped: dict[int, int] = {}
    for tid in tenant_ids:
        try:
            reaped[tid] = reap_stranded_records(tid, stale_minutes=stale_minutes)
        except Exception as exc:
            log.warning("s4_cleanup_reaper_tick: tenant %s failed: %s", tid, exc)
            reaped[tid] = 0
    return reaped
