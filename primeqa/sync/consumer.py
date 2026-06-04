"""Substrate-1 sync consumer (D-152, cutover Step 0 / S0.3).

The worker-layer loop driving :meth:`SyncEngine.run_sync` off the ``s1_sync_jobs``
queue. Per-tenant; mirrors ``generation/consumer.py`` (claim → run → complete/fail)
with two engine-driven divergences (see DECISIONS_LOG D-152):

1. **complete/fail reads ``sync_runs.status``.** The engine captures phase failure
   in the ``sync_run`` row and **returns the id regardless** (only fatal infra —
   ``SyncEngineError`` — raises). So the terminal decision is: ``run_sync`` raises
   → ``fail`` (classified); else read ``sync_runs.status`` — ``'failure'`` →
   ``fail('sync_failed')``; ``'running'/'success'/'partial_success'`` →
   ``set_sync_run`` + ``complete`` (``'running'`` is a structural-complete success —
   the engine hands off to the separate enrichment worker; the job's unit is the
   *structural* sync).
2. **Resume-on-reap.** The job carries ``last_sync_run_id`` (seeded by
   ``create_or_get_job`` from the org's incomplete ``sync_run``, D-152); the
   consumer passes it as ``run_sync(resume_sync_run_id=…)`` so a reaped sync
   continues from ``last_completed_phase``.

``sf_client_resolver`` + ``engine_factory`` are **injected** (the generation
``api_key_resolver`` decoupling pattern) so the substrate consumer stays decoupled
from the v1 connection store + the heavy ``SyncEngine``, and is governance-testable
with a stub SF client + a fake engine. Heartbeat is a single beat at
``mark_running`` (the engine exposes no progress hook); the resume path makes a
rare over-window reap cheap, so the daemon-thread periodic beat is deferred.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from sqlalchemy import text

from primeqa.semantic.connection import (
    _resolve_schema_name,
    get_engine,
    get_tenant_connection,
)
from primeqa.sync.jobs import SyncJobStore

log = logging.getLogger(__name__)

# (tenant_id, environment_id) -> an authenticated SalesforceClient (or compatible).
SfClientResolver = Callable[[int, Optional[int]], Any]
# (engine_db, sf_client, tenant_schema) -> a SyncEngine-like with .run_sync(...).
EngineFactory = Callable[[Any, Any, str], Any]

# The only sync_run status that means the structural sync did NOT succeed.
_SYNC_FAILED = "failure"


def _default_engine_factory(engine_db, sf_client, tenant_schema):
    """Build a real ``SyncEngine``. Imported lazily so importing the consumer
    doesn't drag the engine + its phase graph (and the FK assertion that runs at
    construction) at module-load time — and so a test that injects a fake engine
    never constructs the real one."""
    from primeqa.sync.engine import SyncEngine
    return SyncEngine(engine_db=engine_db, sf_client=sf_client,
                      tenant_schema=tenant_schema)


def _classify_error(exc: Exception) -> str:
    """Thin ``error_code`` for a fatally-failed job (``run_sync`` raised, or
    credential resolution failed before the run)."""
    from primeqa.sync.credentials import CredentialResolutionError
    from primeqa.sync.exceptions import SyncEngineError
    if isinstance(exc, CredentialResolutionError):
        return "credential_error"
    if isinstance(exc, SyncEngineError):
        return "sync_engine_error"
    return "sync_error"


def _read_sync_run_outcome(tenant_id: int, sync_run_id):
    """Read ``(status, error_message)`` for a ``sync_run`` — the engine records
    phase failure here rather than raising. Returns the mapping row or ``None``."""
    with get_tenant_connection(tenant_id) as conn:
        return conn.execute(text(
            "SELECT status, error_message FROM sync_runs "
            "WHERE id = CAST(:id AS uuid)"
        ), {"id": str(sync_run_id)}).mappings().first()


def process_sync_job_for_tenant(
    tenant_id: int, *, sf_client_resolver: SfClientResolver,
    engine_factory: EngineFactory = _default_engine_factory,
) -> Optional[int]:
    """Claim and run one queued sync job for ``tenant_id``. Returns the processed
    job id, or ``None`` when the tenant's queue is empty.

    Flow: claim (``SKIP LOCKED``) → ``mark_running`` + one heartbeat → resolve the
    SF client (injected) → build the engine (injected factory) → ``run_sync``
    (passing the job's ``last_sync_run_id`` as ``resume_sync_run_id``, D-152) →
    terminal:
      - ``run_sync`` **raises** (fatal infra / credential) → ``fail`` with a
        classified ``error_code``.
      - else read ``sync_runs.status``: ``'failure'`` → ``fail('sync_failed')`` +
        the row's message; otherwise → ``set_sync_run`` (provenance) + ``complete``.

    The claim is **not** wrapped — an infrastructure failure (e.g. a missing
    tenant schema) propagates to the per-tenant boundary in
    :func:`run_s1_sync_consumer_tick`."""
    store = SyncJobStore(tenant_id)
    job = store.claim_next_queued_job()
    if job is None:
        return None
    store.mark_running(job.id)
    store.heartbeat(job.id)
    try:
        sf_client = sf_client_resolver(tenant_id, job.environment_id)
        engine = engine_factory(
            get_engine(), sf_client, _resolve_schema_name(tenant_id))
        sync_run_id = engine.run_sync(
            job.connected_org_id, resume_sync_run_id=job.last_sync_run_id)
    except Exception as exc:
        log.warning("s1 sync job %s failed (run): %s", job.id, exc)
        store.fail(job.id, error_code=_classify_error(exc),
                   error_message=str(exc)[:500])
        return job.id

    outcome = _read_sync_run_outcome(tenant_id, sync_run_id)
    status = outcome["status"] if outcome else None
    if status == _SYNC_FAILED:
        msg = (outcome["error_message"] if outcome else None) or "sync_run failed"
        log.warning("s1 sync job %s: sync_run %s status=failure",
                    job.id, sync_run_id)
        store.fail(job.id, error_code="sync_failed",
                   error_message=str(msg)[:500])
    else:
        store.set_sync_run(job.id, sync_run_id)
        store.complete(job.id)
        log.info("s1 sync job %s completed (sync_run=%s status=%s)",
                 job.id, sync_run_id, status)
    return job.id


def run_s1_sync_consumer_tick(
    tenant_ids, *, sf_client_resolver: SfClientResolver,
    engine_factory: EngineFactory = _default_engine_factory,
) -> dict[int, str]:
    """One sync job per tenant, with per-tenant isolation (the resilient-tick
    pattern): a tenant whose claim/processing raises is logged + skipped — it never
    starves the others. Returns ``{tenant_id: outcome}`` for observability
    (``processed:<id>`` / ``empty`` / ``error:<Type>``)."""
    outcomes: dict[int, str] = {}
    for tid in tenant_ids:
        try:
            jid = process_sync_job_for_tenant(
                tid, sf_client_resolver=sf_client_resolver,
                engine_factory=engine_factory)
            outcomes[tid] = f"processed:{jid}" if jid is not None else "empty"
        except Exception as exc:
            log.warning("s1_sync_consumer_tick: tenant %s failed: %s", tid, exc)
            outcomes[tid] = f"error:{type(exc).__name__}"
    return outcomes


# ---------------------------------------------------------------------------
# The enqueuer (the cadence) + the reaper tick — the scheduler's counterparts
# (D-153). The enqueuer fills the queue the consumer above drains.
# ---------------------------------------------------------------------------

# The needs-(re)sync set: orgs with a credential source (environment_id, D-150)
# and **no active job**, that EITHER carry an incomplete sync_run (reaped/failed →
# resume) OR have not synced within the cadence window (never / stale). The
# active-job exclusion + create_or_get_job's dedup keep a steady state quiet.
_NEEDS_SYNC_SQL = (
    "SELECT co.id, co.environment_id "
    "FROM connected_orgs co "
    "WHERE co.environment_id IS NOT NULL "
    "  AND NOT EXISTS ("
    "      SELECT 1 FROM s1_sync_jobs j "
    "      WHERE j.connected_org_id = co.id "
    "        AND j.status IN ('queued', 'claimed', 'running')) "
    "  AND ("
    "      EXISTS (SELECT 1 FROM sync_runs sr "
    "              WHERE sr.source_org_id = co.id "
    "                AND sr.status NOT IN ('success', 'partial_success') "
    "                AND sr.last_completed_phase IS DISTINCT FROM 'Flow') "
    "      OR NOT EXISTS (SELECT 1 FROM sync_runs sr "
    "                     WHERE sr.source_org_id = co.id "
    "                       AND sr.started_at > NOW() - make_interval(hours => :hrs))) "
    "ORDER BY co.id"
)


def _enqueue_for_tenant(tenant_id: int, *, resync_interval_hours: int) -> int:
    """Enqueue every ``connected_org`` in ``tenant_id`` that needs a (re)sync (see
    ``_NEEDS_SYNC_SQL``); returns the count found. ``create_or_get_job`` is
    idempotent, so an org that gained an active job between the scan and the create
    is a harmless no-op (the scan already excludes active-job orgs, so a steady
    state finds nothing)."""
    store = SyncJobStore(tenant_id)
    with get_tenant_connection(tenant_id) as conn:
        rows = conn.execute(text(_NEEDS_SYNC_SQL),
                            {"hrs": resync_interval_hours}).mappings().all()
    for r in rows:
        store.create_or_get_job(connected_org_id=r["id"],
                                environment_id=r["environment_id"])
    return len(rows)


def run_s1_sync_enqueuer_tick(
    tenant_ids, *, resync_interval_hours: int = 24,
) -> dict[int, int]:
    """The scheduler's cadence (D-153): per tenant, enqueue the orgs needing a
    (re)sync — incomplete-sync resume OR never/stale beyond ``resync_interval_hours``
    — with per-tenant isolation (a tenant whose scan raises records 0). Idempotent
    across ticks: an org with an active job is excluded, so a steady state enqueues
    nothing. Returns ``{tenant_id: enqueued_count}``."""
    enqueued: dict[int, int] = {}
    for tid in tenant_ids:
        try:
            enqueued[tid] = _enqueue_for_tenant(
                tid, resync_interval_hours=resync_interval_hours)
        except Exception as exc:
            log.warning("s1_sync_enqueuer_tick: tenant %s failed: %s", tid, exc)
            enqueued[tid] = 0
    return enqueued


def run_s1_sync_reaper_tick(
    tenant_ids, *, stale_minutes: int = 45,
) -> dict[int, int]:
    """The scheduler's reaper counterpart (D-153, mirrors ``run_s3_reaper_tick``):
    fail jobs stuck past the heartbeat timeout, per tenant, with per-tenant
    isolation. ``stale_minutes=45`` (D-151) exceeds the longest legitimate sync; a
    reaped job leaves its ``sync_run`` resumable, so a re-enqueue continues from
    ``last_completed_phase`` (D-152 carry-forward). Returns
    ``{tenant_id: reaped_count}``."""
    reaped: dict[int, int] = {}
    for tid in tenant_ids:
        try:
            reaped[tid] = SyncJobStore(tid).reap_stale_jobs(
                stale_minutes=stale_minutes)
        except Exception as exc:
            log.warning("s1_sync_reaper_tick: tenant %s failed: %s", tid, exc)
            reaped[tid] = 0
    return reaped
