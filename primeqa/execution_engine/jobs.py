"""Substrate-4 execution-job queue (D-130, Phase 3 slice B1).

The async-work record wrapping :func:`run_recipe_execution_async`. The per-tenant
durable queue B2's consumer drains: "execute recipe ``test_id`` on
``environment_id``", with the claim -> attempt -> heartbeat -> reap lifecycle.

A near-mechanical mirror of S3's :class:`GenerationJobStore` (D-106.4): per-tenant
(``s4_execution_jobs`` lives in the ``tenant_<id>`` schema, isolation by schema —
no ``tenant_id`` column), constructed with the tenant id, opens a connection per
call (``get_tenant_connection`` — each its own committed transaction).

Idempotency (D-130.A) DIFFERS from S3. S3 keys ``UNIQUE (requirement_key,
s1_version_seq)`` ("one job ever"). Execution is legitimately **repeatable**, so
S4 uses a **partial-unique on the active set**: :meth:`create_or_get_job` returns
the existing *active* job for ``(test_id, environment_id)`` or creates a fresh
``queued`` one — a new job is allowed once the prior is terminal (re-verify).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from sqlalchemy import text

from primeqa.semantic.connection import get_tenant_connection

# Read-back column list for s4_execution_jobs (one source of truth).
_JOB_COLS = (
    "id, test_id, environment_id, status, current_request_id, attempt_count, "
    "claimed_at, started_at, completed_at, heartbeat_at, error_code, "
    "error_message, created_by, created_at, updated_at"
)

# The active (non-terminal) states — the partial-unique + claim scope.
_ACTIVE = "status IN ('queued', 'claimed', 'running')"
_TERMINAL = "('completed', 'failed', 'cancelled')"


@dataclass(frozen=True)
class ExecutionJob:
    """One ``s4_execution_jobs`` row."""

    id: int
    test_id: str
    environment_id: int
    status: str
    current_request_id: Optional[str]
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


@dataclass(frozen=True)
class JobAttempt:
    """The identity :meth:`ExecutionJobStore.start_attempt` mints — the fresh
    per-attempt ``request_id`` the consumer hands to the async run."""

    attempt_no: int
    request_id: str


def _job(row) -> Optional[ExecutionJob]:
    if row is None:
        return None
    rid = row["current_request_id"]
    return ExecutionJob(
        id=row["id"], test_id=str(row["test_id"]),
        environment_id=row["environment_id"], status=row["status"],
        current_request_id=str(rid) if rid else None,
        attempt_count=row["attempt_count"], claimed_at=row["claimed_at"],
        started_at=row["started_at"], completed_at=row["completed_at"],
        heartbeat_at=row["heartbeat_at"], error_code=row["error_code"],
        error_message=row["error_message"], created_by=row["created_by"],
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


class ExecutionJobStore:
    """Per-tenant execution-job-queue repository (the ``LedgerPersister`` idiom)."""

    def __init__(self, tenant_id: int):
        self._tenant_id = tenant_id

    # -- Idempotency (D-130.A): get-or-create the ACTIVE job --------------
    def create_or_get_job(
        self, *, test_id, environment_id: int, created_by: Optional[int] = None,
    ) -> ExecutionJob:
        """Return the existing **active** job for ``(test_id, environment_id)`` or
        create a fresh ``queued`` one. Re-runnable: once the prior job is terminal
        there is no active row, so a new one is created (D-130.A). Race-safe via
        the partial-unique ``ON CONFLICT … DO NOTHING`` then SELECT the active row."""
        with get_tenant_connection(self._tenant_id) as conn:
            conn.execute(text(
                "INSERT INTO s4_execution_jobs (test_id, environment_id, created_by) "
                "VALUES (CAST(:tid AS uuid), :eid, :cb) "
                f"ON CONFLICT (test_id, environment_id) WHERE {_ACTIVE} DO NOTHING"
            ), {"tid": str(test_id), "eid": environment_id, "cb": created_by})
            row = conn.execute(text(
                f"SELECT {_JOB_COLS} FROM s4_execution_jobs "
                f"WHERE test_id = CAST(:tid AS uuid) AND environment_id = :eid "
                f"AND {_ACTIVE} ORDER BY created_at DESC LIMIT 1"
            ), {"tid": str(test_id), "eid": environment_id}).mappings().first()
            return _job(row)

    # -- Consumer claim: SELECT FOR UPDATE SKIP LOCKED --------------------
    def claim_next_queued_job(self) -> Optional[ExecutionJob]:
        """Atomically claim the oldest ``queued`` job (``queued`` -> ``claimed``)
        via ``SELECT … FOR UPDATE SKIP LOCKED`` — concurrent workers never grab
        the same row. Returns the claimed job, or ``None`` when empty. One per call
        (the consumer processes one per tenant per tick)."""
        with get_tenant_connection(self._tenant_id) as conn:
            row = conn.execute(text(
                "SELECT id FROM s4_execution_jobs WHERE status = 'queued' "
                "ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
            )).mappings().first()
            if row is None:
                return None
            conn.execute(text(
                "UPDATE s4_execution_jobs SET status = 'claimed', "
                "claimed_at = NOW(), updated_at = NOW() WHERE id = :id"
            ), {"id": row["id"]})
            full = conn.execute(text(
                f"SELECT {_JOB_COLS} FROM s4_execution_jobs WHERE id = :id"
            ), {"id": row["id"]}).mappings().first()
            return _job(full)

    # -- Fresh request_id per attempt -------------------------------------
    def start_attempt(self, job_id: int) -> JobAttempt:
        """Mint a FRESH ``request_id`` for a new attempt: bump ``attempt_count``,
        set ``current_request_id``, move the job to ``running``, and write the
        ``s4_execution_job_attempts`` row (observability-grade; the executor mints
        its own run_id per run)."""
        new_rid = str(uuid4())
        with get_tenant_connection(self._tenant_id) as conn:
            row = conn.execute(text(
                "SELECT attempt_count FROM s4_execution_jobs WHERE id = :jid FOR UPDATE"
            ), {"jid": job_id}).mappings().first()
            if row is None:
                raise ValueError(f"no s4_execution_job with id={job_id}")
            attempt_no = row["attempt_count"] + 1
            conn.execute(text(
                "UPDATE s4_execution_jobs SET attempt_count = :n, "
                "current_request_id = CAST(:rid AS uuid), status = 'running', "
                "started_at = COALESCE(started_at, NOW()), updated_at = NOW() "
                "WHERE id = :jid"
            ), {"n": attempt_no, "rid": new_rid, "jid": job_id})
            conn.execute(text(
                "INSERT INTO s4_execution_job_attempts "
                "(job_id, attempt_no, request_id, status, started_at) "
                "VALUES (:jid, :n, CAST(:rid AS uuid), 'running', NOW())"
            ), {"jid": job_id, "n": attempt_no, "rid": new_rid})
            return JobAttempt(attempt_no=attempt_no, request_id=new_rid)

    # -- Status transitions + heartbeat -----------------------------------
    def claim(self, job_id: int) -> None:
        """``queued`` -> ``claimed``. No-op if not queued."""
        with get_tenant_connection(self._tenant_id) as conn:
            conn.execute(text(
                "UPDATE s4_execution_jobs SET status = 'claimed', "
                "claimed_at = NOW(), updated_at = NOW() "
                "WHERE id = :jid AND status = 'queued'"
            ), {"jid": job_id})

    def complete(self, job_id: int) -> None:
        """Terminal success: job ``completed`` + finalize the open attempt."""
        self._finish(job_id, job_status="completed", attempt_status="completed")

    def fail(self, job_id: int, *, error_code: Optional[str] = None,
             error_message: Optional[str] = None) -> None:
        """Terminal failure: job ``failed`` with a thin classified ``error_code``
        + finalize the open attempt. The job-UPDATE is guarded ``status NOT IN
        (terminal)`` so a job that reached terminal concurrently (e.g. completed
        between the reaper's select and this call) is never clobbered — race-safe
        for the reaper."""
        with get_tenant_connection(self._tenant_id) as conn:
            conn.execute(text(
                "UPDATE s4_execution_jobs SET status = 'failed', "
                "completed_at = NOW(), error_code = :ec, error_message = :em, "
                f"updated_at = NOW() WHERE id = :jid AND status NOT IN {_TERMINAL}"
            ), {"jid": job_id, "ec": error_code, "em": error_message})
            conn.execute(text(
                "UPDATE s4_execution_job_attempts SET status = 'failed', "
                "error_code = :ec, finished_at = NOW() "
                "WHERE job_id = :jid AND finished_at IS NULL"
            ), {"jid": job_id, "ec": error_code})

    def cancel(self, job_id: int) -> None:
        """Terminal cancel: job ``cancelled`` + finalize the open attempt."""
        self._finish(job_id, job_status="cancelled", attempt_status="cancelled")

    def heartbeat(self, job_id: int) -> None:
        """Liveness ping (the reaper reads ``heartbeat_at``)."""
        with get_tenant_connection(self._tenant_id) as conn:
            conn.execute(text(
                "UPDATE s4_execution_jobs SET heartbeat_at = NOW(), "
                "updated_at = NOW() WHERE id = :jid"
            ), {"jid": job_id})

    def _finish(self, job_id: int, *, job_status: str, attempt_status: str) -> None:
        with get_tenant_connection(self._tenant_id) as conn:
            # CORR-2: guard the job-status UPDATE with ``status NOT IN (terminal)``
            # — identical to fail() — so complete()/cancel() can never resurrect a
            # job the reaper already terminalized. Without it, a long run the
            # reaper failed (heartbeat timeout while the worker was still alive)
            # was clobbered back to 'completed'/'cancelled' when the worker
            # returned. The attempt UPDATE below is already race-safe via
            # ``finished_at IS NULL`` (the reaper's fail() finalized the attempt).
            conn.execute(text(
                "UPDATE s4_execution_jobs SET status = :st, "
                "completed_at = NOW(), updated_at = NOW() "
                f"WHERE id = :jid AND status NOT IN {_TERMINAL}"
            ), {"jid": job_id, "st": job_status})
            conn.execute(text(
                "UPDATE s4_execution_job_attempts SET status = :st, "
                "finished_at = NOW() WHERE job_id = :jid AND finished_at IS NULL"
            ), {"jid": job_id, "st": attempt_status})

    # -- Reaper: fail jobs stuck past the heartbeat timeout ---------------
    def reap_stale_jobs(self, *, stale_minutes: int = 10) -> int:
        """Fail jobs stuck in ``claimed``/``running`` whose last activity
        (``COALESCE(heartbeat_at, claimed_at)``) is older than ``stale_minutes`` —
        the worker likely died mid-run. Reuses :meth:`fail` so each open attempt is
        finalized (``error_code='stale_timeout'``). Returns the number reaped.

        The default is generous: the consumer heartbeats only at claim/start (the
        async run is one blocking sequence, no mid-run hook), so the timeout must
        exceed the longest legitimate run."""
        threshold = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)
        with get_tenant_connection(self._tenant_id) as conn:
            rows = conn.execute(text(
                "SELECT id FROM s4_execution_jobs "
                "WHERE status IN ('claimed', 'running') "
                "AND COALESCE(heartbeat_at, claimed_at) < :threshold"
            ), {"threshold": threshold}).mappings().all()
        stale_ids = [r["id"] for r in rows]
        for jid in stale_ids:
            self.fail(jid, error_code="stale_timeout",
                      error_message="Execution timed out — the worker may have "
                                    "crashed mid-run. Re-enqueue to retry.")
        return len(stale_ids)

    # -- Reads ------------------------------------------------------------
    def get_job(self, job_id: int) -> Optional[ExecutionJob]:
        with get_tenant_connection(self._tenant_id) as conn:
            row = conn.execute(text(
                f"SELECT {_JOB_COLS} FROM s4_execution_jobs WHERE id = :jid"
            ), {"jid": job_id}).mappings().first()
            return _job(row)

    def get_attempts(self, job_id: int) -> list[dict]:
        """Per-attempt history rows (oldest first) — for the consumer + tests."""
        with get_tenant_connection(self._tenant_id) as conn:
            rows = conn.execute(text(
                "SELECT attempt_no, request_id, status, error_code, "
                "started_at, finished_at FROM s4_execution_job_attempts "
                "WHERE job_id = :jid ORDER BY attempt_no"
            ), {"jid": job_id}).mappings().all()
            return [{**r, "request_id": str(r["request_id"])} for r in rows]
