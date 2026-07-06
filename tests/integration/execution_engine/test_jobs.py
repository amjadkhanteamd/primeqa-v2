"""Integration: S4 execution-job queue + lifecycle (D-130, Phase 3 slice B1).

Per-tenant (local-PG governance schema, via conftest's session ``db_setup``).
Exercises the Postgres semantics the store relies on — the partial-unique active
idempotency, ``FOR UPDATE SKIP LOCKED`` claim, fresh-request_id attempts, the
race-safe terminal guard, and the reaper. DB-gated: the module skips when local
PostgreSQL is unreachable.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from primeqa.execution_engine.jobs import ExecutionJobStore
from primeqa.semantic.connection import get_tenant_connection

from .conftest import TEST_TENANT_ID


def _store() -> ExecutionJobStore:
    return ExecutionJobStore(TEST_TENANT_ID)


def _tid():
    return uuid4()


# ---------------------------------------------------------------------------
# Idempotency (D-130.A) — active dedup, but re-runnable after terminal
# ---------------------------------------------------------------------------

def test_create_or_get_active_job_is_idempotent():
    s, tid = _store(), _tid()
    j1 = s.create_or_get_job(test_id=tid, environment_id=7, created_by=1)
    j2 = s.create_or_get_job(test_id=tid, environment_id=7, created_by=999)
    assert j1.id == j2.id                          # same active job, no dup
    assert j1.status == "queued" and j1.attempt_count == 0
    assert j1.current_request_id is None
    assert j2.created_by == 1                       # ON CONFLICT DO NOTHING: first wins


def test_distinct_env_is_distinct_job():
    s, tid = _store(), _tid()
    a = s.create_or_get_job(test_id=tid, environment_id=7)
    b = s.create_or_get_job(test_id=tid, environment_id=8)
    assert a.id != b.id


def test_rerun_allowed_after_terminal():
    # The D-130.A difference from S3: a fresh job IS allowed once the prior is
    # terminal (execution is repeatable — re-verify the same recipe later).
    s, tid = _store(), _tid()
    j1 = s.create_or_get_job(test_id=tid, environment_id=7)
    s.complete(j1.id)
    j2 = s.create_or_get_job(test_id=tid, environment_id=7)
    assert j2.id != j1.id and j2.status == "queued"


def test_partial_unique_blocks_a_second_active_insert():
    # The active-set partial unique is a real DB constraint, not app-only.
    s, tid = _store(), _tid()
    s.create_or_get_job(test_id=tid, environment_id=7)
    with pytest.raises(IntegrityError):
        with get_tenant_connection(TEST_TENANT_ID) as conn:
            conn.execute(text(
                "INSERT INTO s4_execution_jobs (test_id, environment_id) "
                "VALUES (CAST(:t AS uuid), :e)"), {"t": str(tid), "e": 7})


# ---------------------------------------------------------------------------
# Claim — FOR UPDATE SKIP LOCKED
# ---------------------------------------------------------------------------

def test_claim_next_queued_transitions_and_returns():
    s, tid = _store(), _tid()
    job = s.create_or_get_job(test_id=tid, environment_id=7)
    claimed = s.claim_next_queued_job()
    assert claimed is not None
    assert claimed.id == job.id and claimed.status == "claimed"
    assert claimed.claimed_at is not None


def test_claim_empty_queue_returns_none():
    assert _store().claim_next_queued_job() is None


# ---------------------------------------------------------------------------
# Attempts — fresh request_id per attempt (observability-grade)
# ---------------------------------------------------------------------------

def test_start_attempt_mints_fresh_request_id():
    s = _store()
    job = s.create_or_get_job(test_id=_tid(), environment_id=7)
    a1 = s.start_attempt(job.id)
    a2 = s.start_attempt(job.id)
    assert (a1.attempt_no, a2.attempt_no) == (1, 2)
    assert a1.request_id != a2.request_id
    j = s.get_job(job.id)
    assert j.attempt_count == 2
    assert j.current_request_id == a2.request_id
    assert j.status == "running"
    assert [r["attempt_no"] for r in s.get_attempts(job.id)] == [1, 2]


def test_start_attempt_unknown_job_raises():
    with pytest.raises(ValueError):
        _store().start_attempt(999_999)


# ---------------------------------------------------------------------------
# Lifecycle + the race-safe terminal guard
# ---------------------------------------------------------------------------

def test_complete_finalizes_attempt():
    s = _store()
    job = s.create_or_get_job(test_id=_tid(), environment_id=7)
    s.start_attempt(job.id)
    s.complete(job.id)
    j = s.get_job(job.id)
    assert j.status == "completed" and j.completed_at is not None
    assert s.get_attempts(job.id)[-1]["status"] == "completed"


def test_fail_records_classified_error():
    s = _store()
    job = s.create_or_get_job(test_id=_tid(), environment_id=7)
    s.start_attempt(job.id)
    s.fail(job.id, error_code="sf_auth", error_message="401 invalid grant")
    j = s.get_job(job.id)
    assert j.status == "failed" and j.error_code == "sf_auth"
    att = s.get_attempts(job.id)[-1]
    assert att["status"] == "failed" and att["error_code"] == "sf_auth"
    assert att["finished_at"] is not None


def test_fail_is_race_safe_against_a_terminal_job():
    # fail() must NOT clobber a job that already reached terminal (the reaper race:
    # a job that completed between the reaper's select and its fail() call).
    s = _store()
    job = s.create_or_get_job(test_id=_tid(), environment_id=7)
    s.start_attempt(job.id)
    s.complete(job.id)
    s.fail(job.id, error_code="stale_timeout")
    assert s.get_job(job.id).status == "completed"   # unchanged


def test_complete_is_race_safe_against_a_reaper_failed_job():
    # CORR-2 (repro): the REVERSE race. A long-running job the reaper terminalizes
    # as 'failed' (it exceeded the heartbeat timeout while the worker was still
    # alive) must NOT be resurrected to 'completed' when the slow worker finally
    # returns and calls complete(). _finish had no terminal guard (fail() does),
    # so 'failed' was clobbered to 'completed'. Fails against the old code.
    s = _store()
    job = s.create_or_get_job(test_id=_tid(), environment_id=7)
    s.start_attempt(job.id)
    s.fail(job.id, error_code="stale_timeout")   # reaper terminalizes the stale job
    s.complete(job.id)                           # slow worker returns afterwards
    assert s.get_job(job.id).status == "failed"  # must stay failed, not resurrected


def test_cancel_is_race_safe_against_a_reaper_failed_job():
    # CORR-2: cancel() also routes through _finish, so it must equally not clobber
    # a job the reaper already terminalized.
    s = _store()
    job = s.create_or_get_job(test_id=_tid(), environment_id=7)
    s.start_attempt(job.id)
    s.fail(job.id, error_code="stale_timeout")
    s.cancel(job.id)
    assert s.get_job(job.id).status == "failed"  # terminal 'failed' is not clobbered


def test_heartbeat_sets_timestamp():
    s = _store()
    job = s.create_or_get_job(test_id=_tid(), environment_id=7)
    s.start_attempt(job.id)
    assert s.get_job(job.id).heartbeat_at is None
    s.heartbeat(job.id)
    assert s.get_job(job.id).heartbeat_at is not None


# ---------------------------------------------------------------------------
# Reaper
# ---------------------------------------------------------------------------

def test_reap_stale_running_job_fails_it():
    s = _store()
    job = s.create_or_get_job(test_id=_tid(), environment_id=7)
    s.claim(job.id)
    s.start_attempt(job.id)
    # backdate last activity past the threshold (the worker "died").
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        conn.execute(text(
            "UPDATE s4_execution_jobs SET claimed_at = NOW() - INTERVAL '1 hour', "
            "heartbeat_at = NOW() - INTERVAL '1 hour' WHERE id = :id"), {"id": job.id})
    assert s.reap_stale_jobs(stale_minutes=10) == 1
    j = s.get_job(job.id)
    assert j.status == "failed" and j.error_code == "stale_timeout"


def test_reap_ignores_a_fresh_job():
    s = _store()
    job = s.create_or_get_job(test_id=_tid(), environment_id=7)
    s.claim(job.id)
    s.start_attempt(job.id)
    s.heartbeat(job.id)
    assert s.reap_stale_jobs(stale_minutes=10) == 0
    assert s.get_job(job.id).status == "running"
