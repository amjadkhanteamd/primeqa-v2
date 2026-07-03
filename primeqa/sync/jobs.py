"""Substrate-1 sync-job queue (D-151, cutover Step 0 / S0.2).

The async-work record wrapping :meth:`SyncEngine.run_sync`. The per-tenant durable
queue the S0.3 consumer drains: "sync ``connected_org_id`` from Salesforce", with
the claim → heartbeat → reap lifecycle.

A near-mechanical mirror of S4's :class:`~primeqa.execution_engine.jobs.ExecutionJobStore`
(D-130): per-tenant (``s1_sync_jobs`` lives in the ``tenant_<id>`` schema, isolation
by schema — no ``tenant_id`` column), constructed with the tenant id, opens a
connection per call (``get_tenant_connection`` — each its own committed
transaction). **No attempts child table** — the engine writes its own ``sync_runs``;
``last_sync_run_id`` links the current/last run (the resume anchor).

Idempotency (the S4 repeatable shape): :meth:`create_or_get_job` returns the
existing *active* job for ``connected_org_id`` or creates a fresh ``queued`` one — a
new job once the prior is terminal (re-sync as the org evolves).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text

from primeqa.semantic.connection import get_tenant_connection
from primeqa.sync.fk_assertion import FINAL_PHASE

# Read-back column list for s1_sync_jobs (one source of truth).
_JOB_COLS = (
    "id, connected_org_id, environment_id, status, last_sync_run_id, "
    "attempt_count, claimed_at, started_at, completed_at, heartbeat_at, "
    "error_code, error_message, created_by, created_at, updated_at"
)

# The active (non-terminal) states — the partial-unique + claim scope.
_ACTIVE = "status IN ('queued', 'claimed', 'running')"
_TERMINAL = "('completed', 'failed', 'cancelled')"


@dataclass(frozen=True)
class SyncJob:
    """One ``s1_sync_jobs`` row."""

    id: int
    connected_org_id: str
    environment_id: int
    status: str
    last_sync_run_id: Optional[str]
    attempt_count: int
    claimed_at: Optional[datetime]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    heartbeat_at: Optional[datetime]
    error_code: Optional[str]
    error_message: Optional[str]
    created_by: Optional[int]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]


def _job(row) -> Optional[SyncJob]:
    if row is None:
        return None
    sr = row["last_sync_run_id"]
    return SyncJob(
        id=row["id"], connected_org_id=str(row["connected_org_id"]),
        environment_id=row["environment_id"], status=row["status"],
        last_sync_run_id=str(sr) if sr else None,
        attempt_count=row["attempt_count"], claimed_at=row["claimed_at"],
        started_at=row["started_at"], completed_at=row["completed_at"],
        heartbeat_at=row["heartbeat_at"], error_code=row["error_code"],
        error_message=row["error_message"], created_by=row["created_by"],
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


class SyncJobStore:
    """Per-tenant sync-job-queue repository (mirrors ``ExecutionJobStore``)."""

    def __init__(self, tenant_id: int):
        self._tenant_id = tenant_id

    # -- Idempotency: get-or-create the ACTIVE job ------------------------
    def create_or_get_job(
        self, *, connected_org_id, environment_id: int,
        created_by: Optional[int] = None,
    ) -> SyncJob:
        """Return the existing **active** job for ``connected_org_id`` or create a
        fresh ``queued`` one. Re-runnable: once the prior job is terminal there is
        no active row, so a new one is created. Race-safe via the partial-unique
        ``ON CONFLICT … DO NOTHING`` then SELECT the active row.

        **Resume carry-forward (D-152).** A newly-created job's
        ``last_sync_run_id`` is seeded from the org's most-recent *incomplete*
        ``sync_run`` — ``status NOT IN ('success','partial_success') AND
        last_completed_phase IS DISTINCT FROM FINAL_PHASE`` — so the consumer resumes a
        reaped/failed sync from ``last_completed_phase`` (the engine's
        ``run_sync(resume_sync_run_id=…)``) instead of re-syncing from phase 1.
        There is at most one such row per org (resume continues the same
        ``sync_run`` rather than forking a new one), so newest-first is
        unambiguous; NULL when none (a fresh sync). The seed is computed in the
        INSERT's source SELECT, so a conflict (an active job already exists)
        discards it via ``DO NOTHING`` — carry-forward only materializes when a new
        job is actually created."""
        with get_tenant_connection(self._tenant_id) as conn:
            conn.execute(text(
                "INSERT INTO s1_sync_jobs "
                "(connected_org_id, environment_id, created_by, last_sync_run_id) "
                "SELECT CAST(:oid AS uuid), :eid, :cb, ("
                "  SELECT sr.id FROM sync_runs sr "
                "  WHERE sr.source_org_id = CAST(:oid AS uuid) "
                "    AND sr.status NOT IN ('success', 'partial_success') "
                "    AND sr.last_completed_phase IS DISTINCT FROM :final_phase "
                "  ORDER BY sr.started_at DESC LIMIT 1) "
                f"ON CONFLICT (connected_org_id) WHERE {_ACTIVE} DO NOTHING"
            ), {"oid": str(connected_org_id), "eid": environment_id,
                "cb": created_by, "final_phase": FINAL_PHASE})
            row = conn.execute(text(
                f"SELECT {_JOB_COLS} FROM s1_sync_jobs "
                f"WHERE connected_org_id = CAST(:oid AS uuid) AND {_ACTIVE} "
                f"ORDER BY created_at DESC LIMIT 1"
            ), {"oid": str(connected_org_id)}).mappings().first()
            return _job(row)

    # -- Consumer claim: SELECT FOR UPDATE SKIP LOCKED --------------------
    def claim_next_queued_job(self) -> Optional[SyncJob]:
        """Atomically claim the oldest ``queued`` job (``queued`` → ``claimed``)
        via ``SELECT … FOR UPDATE SKIP LOCKED``. Returns the claimed job, or
        ``None`` when empty. One per call."""
        with get_tenant_connection(self._tenant_id) as conn:
            row = conn.execute(text(
                "SELECT id FROM s1_sync_jobs WHERE status = 'queued' "
                "ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
            )).mappings().first()
            if row is None:
                return None
            conn.execute(text(
                "UPDATE s1_sync_jobs SET status = 'claimed', "
                "claimed_at = NOW(), updated_at = NOW() WHERE id = :id"
            ), {"id": row["id"]})
            full = conn.execute(text(
                f"SELECT {_JOB_COLS} FROM s1_sync_jobs WHERE id = :id"
            ), {"id": row["id"]}).mappings().first()
            return _job(full)

    # -- Running: bump attempt_count, move to running ---------------------
    def mark_running(self, job_id: int) -> int:
        """Move the job to ``running`` + bump ``attempt_count`` (the consumer calls
        this before ``run_sync``). Returns the new attempt number."""
        with get_tenant_connection(self._tenant_id) as conn:
            row = conn.execute(text(
                "SELECT attempt_count FROM s1_sync_jobs WHERE id = :jid FOR UPDATE"
            ), {"jid": job_id}).mappings().first()
            if row is None:
                raise ValueError(f"no s1_sync_job with id={job_id}")
            attempt_no = row["attempt_count"] + 1
            conn.execute(text(
                "UPDATE s1_sync_jobs SET attempt_count = :n, status = 'running', "
                "started_at = COALESCE(started_at, NOW()), updated_at = NOW() "
                "WHERE id = :jid"
            ), {"n": attempt_no, "jid": job_id})
            return attempt_no

    def set_sync_run(self, job_id: int, sync_run_id) -> None:
        """Link the engine's ``sync_run`` to the job — the resume anchor (the
        consumer records it once ``run_sync`` reports its run id)."""
        with get_tenant_connection(self._tenant_id) as conn:
            conn.execute(text(
                "UPDATE s1_sync_jobs SET last_sync_run_id = CAST(:sr AS uuid), "
                "updated_at = NOW() WHERE id = :jid"
            ), {"sr": str(sync_run_id), "jid": job_id})

    # -- Heartbeat + terminal transitions --------------------------------
    def heartbeat(self, job_id: int) -> None:
        """Liveness ping (the reaper reads ``heartbeat_at``)."""
        with get_tenant_connection(self._tenant_id) as conn:
            conn.execute(text(
                "UPDATE s1_sync_jobs SET heartbeat_at = NOW(), "
                "updated_at = NOW() WHERE id = :jid"
            ), {"jid": job_id})

    def complete(self, job_id: int) -> None:
        """Terminal success: job ``completed``."""
        with get_tenant_connection(self._tenant_id) as conn:
            conn.execute(text(
                "UPDATE s1_sync_jobs SET status = 'completed', "
                "completed_at = NOW(), updated_at = NOW() WHERE id = :jid"
            ), {"jid": job_id})

    def fail(self, job_id: int, *, error_code: Optional[str] = None,
             error_message: Optional[str] = None) -> None:
        """Terminal failure with a thin classified ``error_code``. Guarded
        ``status NOT IN (terminal)`` so a job that reached terminal concurrently
        (e.g. completed between the reaper's select and this call) is never
        clobbered — race-safe for the reaper. ``last_sync_run_id`` is preserved
        (a later job can resume that run)."""
        with get_tenant_connection(self._tenant_id) as conn:
            conn.execute(text(
                "UPDATE s1_sync_jobs SET status = 'failed', "
                "completed_at = NOW(), error_code = :ec, error_message = :em, "
                f"updated_at = NOW() WHERE id = :jid AND status NOT IN {_TERMINAL}"
            ), {"jid": job_id, "ec": error_code, "em": error_message})

    # -- Reaper: fail jobs stuck past the heartbeat timeout ---------------
    def reap_stale_jobs(self, *, stale_minutes: int = 45) -> int:
        """Fail jobs stuck in ``claimed``/``running`` whose last activity
        (``COALESCE(heartbeat_at, claimed_at)``) is older than ``stale_minutes`` —
        the worker likely died mid-sync. Returns the number reaped. The default is
        generous (45 min): a full org sync is long, so the timeout must exceed the
        longest legitimate run. ``last_sync_run_id`` survives the fail, so a fresh
        job can resume that sync_run."""
        threshold = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)
        with get_tenant_connection(self._tenant_id) as conn:
            rows = conn.execute(text(
                "SELECT id FROM s1_sync_jobs "
                "WHERE status IN ('claimed', 'running') "
                "AND COALESCE(heartbeat_at, claimed_at) < :threshold"
            ), {"threshold": threshold}).mappings().all()
        stale_ids = [r["id"] for r in rows]
        for jid in stale_ids:
            self.fail(jid, error_code="stale_timeout",
                      error_message="Sync timed out — the worker may have crashed "
                                    "mid-sync. Re-enqueue to resume.")
        return len(stale_ids)

    # -- Reads ------------------------------------------------------------
    def get_job(self, job_id: int) -> Optional[SyncJob]:
        with get_tenant_connection(self._tenant_id) as conn:
            row = conn.execute(text(
                f"SELECT {_JOB_COLS} FROM s1_sync_jobs WHERE id = :jid"
            ), {"jid": job_id}).mappings().first()
            return _job(row)
