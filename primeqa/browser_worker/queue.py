"""Per-tenant UI inspection job queue — lease/heartbeat/reap (ui-s2.3).

SQL mirrors the ai_enrichment_queue consumer idiom in primeqa/worker.py
(_claim_batch / _reap_stalled / _mark_failed); tenant context is set
exactly as worker.py's _set_tenant_context does. Deltas are designed in
docs/ui-testing/LLD_PHASE2_3_QUEUE.md: heartbeat-based reaping (browser
batches legitimately run long), claim-only attempts charging, a `reaps`
death counter, and a reap-time cap to failed_permanent (the poison-killer
gap the enrichment reaper has).

All helpers take an already-tenant-scoped Session and commit at the same
boundaries the worker helpers do.
"""

from __future__ import annotations

import json
import os
import socket

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

UI_INSPECTION_MAX_ATTEMPTS = 5   # mirrors ENRICHMENT_MAX_ATTEMPTS
HEARTBEAT_STALL_S = 120          # heartbeat older than this -> lease dead


def worker_identity() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def open_tenant_session(tenant_id: int, database_url: str | None = None) -> Session:
    """Engine + Session scoped to tenant_<id> — the worker.py idiom:
    SET search_path + SET app.tenant_id now, plus an after_begin listener
    re-applying both on every later transaction (commit() releases the
    connection to the pool; the next checkout may not carry the SETs)."""
    from sqlalchemy import event

    if not isinstance(tenant_id, int) or tenant_id <= 0:
        raise ValueError(f"tenant_id must be a positive int, got {tenant_id!r}")
    url = database_url or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not set and no database_url given")

    engine = create_engine(url, pool_pre_ping=True)
    session = Session(bind=engine)
    schema = f"tenant_{tenant_id}"

    session.execute(text(f'SET search_path TO "{schema}", public'))
    session.execute(text("SET app.tenant_id = :tid"), {"tid": str(tenant_id)})

    @event.listens_for(session, "after_begin")
    def _reapply(_session, _trans, conn):
        conn.execute(text(f'SET search_path TO "{schema}", public'))
        conn.execute(text(f"SET app.tenant_id = '{tenant_id}'"))

    return session


def enqueue(session: Session, payload: dict, manifest_id: str) -> str:
    """Insert a pending job; payload spike shape:
    {"surfaces": [{"key": ..., "url": ...}]}. manifest_id is required
    (NOT NULL FK since 2.4 — a job can never exist without its persisted
    manifest); manifest.enqueue_for_manifest is the normal entry point.
    Returns the job id."""
    row = session.execute(text("""
        INSERT INTO s4_ui_inspection_jobs (payload, manifest_id)
        VALUES (CAST(:payload AS JSONB), :manifest_id)
        RETURNING id
    """), {"payload": json.dumps(payload),
           "manifest_id": manifest_id}).fetchone()
    session.commit()
    return str(row[0])


def claim_one(session: Session, claimed_by: str | None = None) -> dict | None:
    """Atomically claim the oldest pending/failed_retryable job — FOR
    UPDATE SKIP LOCKED, safe across concurrent consumers. Flips it to
    in_progress, stamps the lease (claimed_by/claimed_at/heartbeat_at),
    stamps started_at, clears completed_at, bumps attempts (claim-only
    charging: attempts = number of execution starts)."""
    rows = session.execute(text("""
        UPDATE s4_ui_inspection_jobs
        SET status = 'in_progress',
            started_at = NOW(),
            completed_at = NULL,
            claimed_by = :cb,
            claimed_at = NOW(),
            heartbeat_at = NOW(),
            attempts = attempts + 1
        WHERE id IN (
            SELECT id FROM s4_ui_inspection_jobs
            WHERE status IN ('pending', 'failed_retryable')
            ORDER BY enqueued_at
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING id, payload, attempts, reaps
    """), {"cb": claimed_by or worker_identity()}).fetchall()
    session.commit()
    if not rows:
        return None
    r = rows[0]
    return {"job_id": str(r[0]), "payload": r[1], "attempts": r[2], "reaps": r[3]}


def heartbeat(session: Session, job_id: str) -> None:
    """Touch heartbeat_at — the consumer's proof of liveness, called
    before every surface."""
    session.execute(text("""
        UPDATE s4_ui_inspection_jobs
        SET heartbeat_at = NOW()
        WHERE id = :id AND status = 'in_progress'
    """), {"id": job_id})
    session.commit()


def finalize_surface(session: Session, job_id: str, surface_key: str,
                     attempt: int, observation: dict) -> None:
    """UPSERT one surface's engine observation. A retried batch rewrites
    completed surfaces idempotently; duplicates are impossible by the
    (job_id, surface_key) UNIQUE constraint."""
    session.execute(text("""
        INSERT INTO s4_ui_inspection_results
            (job_id, surface_key, attempt, observation)
        VALUES (:job_id, :surface_key, :attempt, CAST(:observation AS JSONB))
        ON CONFLICT (job_id, surface_key)
        DO UPDATE SET observation = EXCLUDED.observation,
                      attempt     = EXCLUDED.attempt,
                      created_at  = NOW()
    """), {"job_id": job_id, "surface_key": surface_key,
           "attempt": attempt, "observation": json.dumps(observation)})
    session.commit()


def reap_stalled(session: Session, stall_s: int = HEARTBEAT_STALL_S,
                 max_attempts: int = UI_INSPECTION_MAX_ATTEMPTS) -> int:
    """Return dead-lease jobs (in_progress, heartbeat older than stall_s)
    to pending — heartbeat-based, never elapsed-time-based. Increments
    reaps, never attempts. Jobs already at max_attempts park as
    failed_permanent (the reaper cap). Returns rows reaped."""
    result = session.execute(text("""
        UPDATE s4_ui_inspection_jobs
        SET reaps        = reaps + 1,
            status       = CASE WHEN attempts >= :max
                                THEN 'failed_permanent' ELSE 'pending' END,
            completed_at = CASE WHEN attempts >= :max THEN NOW() END,
            error_text   = CASE WHEN attempts >= :max
                                THEN 'reaped at max attempts (stale heartbeat)'
                           END,
            started_at   = CASE WHEN attempts >= :max THEN started_at END,
            claimed_by   = NULL, claimed_at = NULL, heartbeat_at = NULL
        WHERE status = 'in_progress'
          AND heartbeat_at < NOW() - make_interval(secs => :stall_s)
    """), {"max": max_attempts, "stall_s": stall_s})
    session.commit()
    return result.rowcount


def mark_succeeded(session: Session, job_id: str) -> None:
    session.execute(text("""
        UPDATE s4_ui_inspection_jobs
        SET status = 'succeeded', completed_at = NOW(), error_text = NULL,
            claimed_by = NULL, claimed_at = NULL, heartbeat_at = NULL
        WHERE id = :id
    """), {"id": job_id})
    session.commit()


def mark_failed(session: Session, job_id: str, err: str, attempts: int,
                retryable: bool = True) -> str:
    """The _mark_failed classification verbatim: retryable failures become
    failed_retryable until attempts hit the max, then failed_permanent."""
    if retryable and attempts < UI_INSPECTION_MAX_ATTEMPTS:
        status = "failed_retryable"
    else:
        status = "failed_permanent"
    session.execute(text("""
        UPDATE s4_ui_inspection_jobs
        SET status = :st, completed_at = NOW(), error_text = :err,
            claimed_by = NULL, claimed_at = NULL, heartbeat_at = NULL
        WHERE id = :id
    """), {"st": status, "err": (err or "")[:2000], "id": job_id})
    session.commit()
    return status
