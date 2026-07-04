"""Substrate-3 production-integration job queue (D-106.4, slice 1).

The async-work record wrapping the in-process ``run_generation`` core. This slice
is **model + idempotency + attempt-lineage identity ONLY** — no worker consumer
(slice 3), no ``run_generation`` invocation, no endpoint (slice 4).

Placement (D-106.4 .A): per-tenant (``s3_generation_jobs`` lives in the
``tenant_<id>`` schema, isolation by schema — no ``tenant_id`` column). Mirrors
the v1 ``generation_jobs`` *lifecycle*, not its shared placement. Writes over a
per-tenant connection (``get_tenant_connection``); like ``LedgerPersister``, the
store is constructed with the tenant id and opens a connection per call (each
its own committed transaction).

Two-layer idempotency (D-106.4 .B):
  - Layer 1 — :meth:`create_or_get_job`: get-or-create on the unique key
    ``(requirement_key, s1_version_seq)``. One job per logical request, ever.
  - Layer 2 — :meth:`start_attempt`: mint a FRESH ``request_id`` per attempt,
    record it in ``s3_generation_job_attempts``, bump ``attempt_count`` and set
    ``current_request_id``. The fresh id closes the ``generation_requests`` PK
    collision; the ledger's ``prior_request_id`` stays NULL (attempt lineage is
    job-level, never the semantic chain).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from sqlalchemy import text

from primeqa.semantic.connection import get_tenant_connection

# Read-back column list for s3_generation_jobs (one source of truth).
_JOB_COLS = (
    "id, requirement_key, requirement_text, s1_version_seq, s1_version_name, "
    "environment_id, status, current_request_id, attempt_count, progress_pct, "
    "progress_msg, claimed_at, started_at, completed_at, heartbeat_at, "
    "error_code, error_message, created_by, created_at, updated_at"
)


@dataclass(frozen=True)
class GenerationJob:
    """One ``s3_generation_jobs`` row."""

    id: int
    requirement_key: str
    requirement_text: Optional[str]      # enqueue-pinned (option B: caller-fed)
    s1_version_seq: int
    s1_version_name: Optional[str]
    environment_id: Optional[int]        # api_key resolution handle (worker-side)
    status: str
    current_request_id: Optional[str]
    attempt_count: int
    progress_pct: Optional[int]
    progress_msg: Optional[str]
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
    """The identity a :meth:`GenerationJobStore.start_attempt` mints — the fresh
    per-attempt ``request_id`` slice 3 will hand to ``run_generation``."""

    attempt_no: int
    request_id: str


def _job(row) -> Optional[GenerationJob]:
    if row is None:
        return None
    rid = row["current_request_id"]
    return GenerationJob(
        id=row["id"], requirement_key=row["requirement_key"],
        requirement_text=row["requirement_text"],
        s1_version_seq=row["s1_version_seq"], s1_version_name=row["s1_version_name"],
        environment_id=row["environment_id"],
        status=row["status"], current_request_id=str(rid) if rid else None,
        attempt_count=row["attempt_count"], progress_pct=row["progress_pct"],
        progress_msg=row["progress_msg"], claimed_at=row["claimed_at"],
        started_at=row["started_at"], completed_at=row["completed_at"],
        heartbeat_at=row["heartbeat_at"], error_code=row["error_code"],
        error_message=row["error_message"], created_by=row["created_by"],
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


class GenerationJobStore:
    """Per-tenant job-queue repository (mirrors the ``LedgerPersister`` idiom)."""

    def __init__(self, tenant_id: int):
        self._tenant_id = tenant_id

    # -- Idempotency layer 1: get-or-create -------------------------------
    def create_or_get_job(
        self, *, requirement_key: str, s1_version_seq: int,
        s1_version_name: Optional[str] = None, created_by: Optional[int] = None,
        requirement_text: Optional[str] = None, environment_id: Optional[int] = None,
        force_rerun: bool = False,
    ) -> GenerationJob:
        """Return the existing job for ``(requirement_key, s1_version_seq)`` or
        create a fresh ``queued`` one. Idempotent: the same key always maps to
        the same job row (D-106.4 .B layer 1). ``requirement_text`` +
        ``environment_id`` are the enqueue-pinned fields (slice 4); they are
        optional so slice-1/3 callers are unaffected.

        ``force_rerun`` (D-314) completes the migration's documented "retry in
        place via start_attempt" model with the missing re-queue TRIGGER. When
        True and the existing job is TERMINAL (``completed``/``failed``/
        ``cancelled``), the job is reset to ``queued`` (run fields cleared; the
        requirement text/env/version-name refreshed to this trigger; ``created_by``
        ownership + ``attempt_count`` KEPT — the former so re-running never
        transfers cancel rights, the latter so :meth:`start_attempt` bumps the
        retry lineage) so the consumer runs it again on the SAME row. When the job is ACTIVE (``queued``/``claimed``/
        ``running``) the ``WHERE status IN (terminal)`` guard makes it a no-op —
        a double-click during an in-flight run never disturbs it, and the SELECT
        still returns the in-flight job. ``force_rerun=False`` is the unchanged
        ``DO NOTHING`` idempotent enqueue (the auto/scheduled/repair paths).
        Without a re-run trigger a terminal job at the current S1 version is a
        dead end: the UI "Generate" resolves to it and nothing new runs."""
        if force_rerun:
            conflict_action = (
                "ON CONFLICT (requirement_key, s1_version_seq) DO UPDATE SET "
                "status = 'queued', "
                "requirement_text = EXCLUDED.requirement_text, "
                "s1_version_name = EXCLUDED.s1_version_name, "
                "environment_id = EXCLUDED.environment_id, "
                # created_by is NOT refreshed: it is the job's OWNER, and the
                # cancel endpoint gates on it (creator-or-admin). Overwriting it
                # to the re-triggering user would silently transfer cancel rights
                # and strip the original creator's. Ownership stays with the first
                # creator across re-runs (admins can always cancel).
                "current_request_id = NULL, progress_pct = 0, progress_msg = NULL, "
                "claimed_at = NULL, started_at = NULL, completed_at = NULL, "
                "heartbeat_at = NULL, error_code = NULL, error_message = NULL, "
                "updated_at = NOW() "
                "WHERE s3_generation_jobs.status IN ('completed', 'failed', 'cancelled')"
            )
        else:
            conflict_action = (
                "ON CONFLICT (requirement_key, s1_version_seq) DO NOTHING"
            )
        with get_tenant_connection(self._tenant_id) as conn:
            conn.execute(text(
                "INSERT INTO s3_generation_jobs "
                "(requirement_key, requirement_text, s1_version_seq, "
                " s1_version_name, environment_id, created_by) "
                "VALUES (:rk, :rt, :sv, :svn, :eid, :cb) "
                + conflict_action
            ), {"rk": requirement_key, "rt": requirement_text, "sv": s1_version_seq,
                "svn": s1_version_name, "eid": environment_id, "cb": created_by})
            row = conn.execute(text(
                f"SELECT {_JOB_COLS} FROM s3_generation_jobs "
                "WHERE requirement_key = :rk AND s1_version_seq = :sv"
            ), {"rk": requirement_key, "sv": s1_version_seq}).mappings().first()
            return _job(row)

    # -- Consumer claim (slice 3): SELECT FOR UPDATE SKIP LOCKED ----------
    def claim_next_queued_job(self) -> Optional[GenerationJob]:
        """Atomically claim the oldest ``queued`` job (``queued`` -> ``claimed``)
        via ``SELECT … FOR UPDATE SKIP LOCKED`` — concurrent workers never grab
        the same row. Returns the claimed job, or ``None`` when the queue is
        empty. One job per call (the consumer processes one per tenant per tick)."""
        with get_tenant_connection(self._tenant_id) as conn:
            row = conn.execute(text(
                "SELECT id FROM s3_generation_jobs WHERE status = 'queued' "
                "ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
            )).mappings().first()
            if row is None:
                return None
            conn.execute(text(
                "UPDATE s3_generation_jobs SET status = 'claimed', "
                "claimed_at = NOW(), updated_at = NOW() WHERE id = :id"
            ), {"id": row["id"]})
            full = conn.execute(text(
                f"SELECT {_JOB_COLS} FROM s3_generation_jobs WHERE id = :id"
            ), {"id": row["id"]}).mappings().first()
            return _job(full)

    # -- Idempotency layer 2: fresh request_id per attempt ----------------
    def start_attempt(self, job_id: int) -> JobAttempt:
        """Mint a FRESH ``request_id`` for a new attempt: bump ``attempt_count``,
        set ``current_request_id``, move the job to ``running``, and write the
        ``s3_generation_job_attempts`` row. The fresh id is what closes the
        ``generation_requests`` PK collision on retry (D-106.4 .B layer 2); the
        ledger ``prior_request_id`` stays NULL (job-level lineage)."""
        new_rid = str(uuid4())
        with get_tenant_connection(self._tenant_id) as conn:
            row = conn.execute(text(
                "SELECT attempt_count FROM s3_generation_jobs WHERE id = :jid FOR UPDATE"
            ), {"jid": job_id}).mappings().first()
            if row is None:
                raise ValueError(f"no s3_generation_job with id={job_id}")
            attempt_no = row["attempt_count"] + 1
            conn.execute(text(
                "UPDATE s3_generation_jobs SET attempt_count = :n, "
                "current_request_id = CAST(:rid AS uuid), status = 'running', "
                "started_at = COALESCE(started_at, NOW()), updated_at = NOW() "
                "WHERE id = :jid"
            ), {"n": attempt_no, "rid": new_rid, "jid": job_id})
            conn.execute(text(
                "INSERT INTO s3_generation_job_attempts "
                "(job_id, attempt_no, request_id, status, started_at) "
                "VALUES (:jid, :n, CAST(:rid AS uuid), 'running', NOW())"
            ), {"jid": job_id, "n": attempt_no, "rid": new_rid})
            return JobAttempt(attempt_no=attempt_no, request_id=new_rid)

    # -- Status transitions + heartbeat -----------------------------------
    def claim(self, job_id: int) -> None:
        """``queued`` -> ``claimed`` (a consumer took the job). No-op if not queued."""
        with get_tenant_connection(self._tenant_id) as conn:
            conn.execute(text(
                "UPDATE s3_generation_jobs SET status = 'claimed', "
                "claimed_at = NOW(), updated_at = NOW() "
                "WHERE id = :jid AND status = 'queued'"
            ), {"jid": job_id})

    def complete(self, job_id: int) -> None:
        """Terminal success: job ``completed`` + finalize the open attempt."""
        self._finish(job_id, job_status="completed", attempt_status="completed")

    def fail(self, job_id: int, *, error_code: Optional[str] = None,
             error_message: Optional[str] = None) -> None:
        """Terminal failure (abort-on-error, D-106.3): job ``failed`` with a thin
        classified ``error_code`` + finalize the open attempt. The job-UPDATE is
        guarded ``status NOT IN (terminal)`` so a job that reached a terminal
        state concurrently (e.g. completed between the reaper's select and this
        call) is never clobbered back to failed — race-safe for the reaper."""
        with get_tenant_connection(self._tenant_id) as conn:
            conn.execute(text(
                "UPDATE s3_generation_jobs SET status = 'failed', "
                "completed_at = NOW(), error_code = :ec, error_message = :em, "
                "updated_at = NOW() WHERE id = :jid "
                "AND status NOT IN ('completed', 'failed', 'cancelled')"
            ), {"jid": job_id, "ec": error_code, "em": error_message})
            conn.execute(text(
                "UPDATE s3_generation_job_attempts SET status = 'failed', "
                "error_code = :ec, finished_at = NOW() "
                "WHERE job_id = :jid AND finished_at IS NULL"
            ), {"jid": job_id, "ec": error_code})

    def cancel(self, job_id: int) -> None:
        """Terminal cancel: job ``cancelled`` + finalize the open attempt."""
        self._finish(job_id, job_status="cancelled", attempt_status="cancelled")

    def heartbeat(self, job_id: int) -> None:
        """Liveness ping (the slice-5 reaper reads ``heartbeat_at``)."""
        with get_tenant_connection(self._tenant_id) as conn:
            conn.execute(text(
                "UPDATE s3_generation_jobs SET heartbeat_at = NOW(), "
                "updated_at = NOW() WHERE id = :jid"
            ), {"jid": job_id})

    def _finish(self, job_id: int, *, job_status: str, attempt_status: str) -> None:
        with get_tenant_connection(self._tenant_id) as conn:
            conn.execute(text(
                "UPDATE s3_generation_jobs SET status = :st, "
                "completed_at = NOW(), updated_at = NOW() WHERE id = :jid"
            ), {"jid": job_id, "st": job_status})
            conn.execute(text(
                "UPDATE s3_generation_job_attempts SET status = :st, "
                "finished_at = NOW() WHERE job_id = :jid AND finished_at IS NULL"
            ), {"jid": job_id, "st": attempt_status})

    # -- Reaper (slice 5): fail jobs stuck past the heartbeat timeout -----
    def reap_stale_jobs(self, *, stale_minutes: int = 10) -> int:
        """Fail jobs stuck in ``claimed``/``running`` whose last activity
        (``COALESCE(heartbeat_at, claimed_at)``) is older than ``stale_minutes``
        — the worker likely died mid-run. Reuses :meth:`fail` so each open
        attempt is finalized like a normal failure (``error_code='stale_timeout'``).
        Returns the number reaped.

        The timeout is GENEROUS: the consumer heartbeats only at claim/start
        (``run_generation`` is one blocking call, no mid-run hook), so it must
        exceed the longest legitimate run — unlike the v1 reaper's 2 min."""
        threshold = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)
        with get_tenant_connection(self._tenant_id) as conn:
            rows = conn.execute(text(
                "SELECT id FROM s3_generation_jobs "
                "WHERE status IN ('claimed', 'running') "
                "AND COALESCE(heartbeat_at, claimed_at) < :threshold"
            ), {"threshold": threshold}).mappings().all()
        stale_ids = [r["id"] for r in rows]
        for jid in stale_ids:
            self.fail(jid, error_code="stale_timeout",
                      error_message="Generation timed out — the worker may have "
                                    "crashed mid-run. Re-enqueue to retry.")
        return len(stale_ids)

    # -- Reads ------------------------------------------------------------
    def get_job(self, job_id: int) -> Optional[GenerationJob]:
        with get_tenant_connection(self._tenant_id) as conn:
            row = conn.execute(text(
                f"SELECT {_JOB_COLS} FROM s3_generation_jobs WHERE id = :jid"
            ), {"jid": job_id}).mappings().first()
            return _job(row)

    def get_attempts(self, job_id: int) -> list[dict]:
        """Per-attempt history rows (oldest first) — for the consumer + tests."""
        with get_tenant_connection(self._tenant_id) as conn:
            rows = conn.execute(text(
                "SELECT attempt_no, request_id, status, error_code, "
                "started_at, finished_at FROM s3_generation_job_attempts "
                "WHERE job_id = :jid ORDER BY attempt_no"
            ), {"jid": job_id}).mappings().all()
            return [{**r, "request_id": str(r["request_id"])} for r in rows]
