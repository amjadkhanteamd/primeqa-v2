"""Governance tests for the S1 sync-job queue (D-151, cutover Step 0 / S0.2).

``SyncJobStore`` against the governance DB (no Salesforce). Covers the lifecycle
(create → claim → run → heartbeat → complete), the S4-shape active-set
idempotency (one active sync per connected_org; a fresh job only after terminal),
claim semantics (oldest-first, FOR UPDATE SKIP LOCKED), and the reaper (fails a
stale run, preserves ``last_sync_run_id`` for resume, skips a fresh one).
"""
from __future__ import annotations

import time
from uuid import uuid4

from sqlalchemy import text

from primeqa.semantic.connection import get_tenant_connection

TENANT = 1


def _backdate(job_id: int, minutes: int) -> None:
    """Push a job's claimed_at + heartbeat_at into the past (simulate a worker
    that died mid-sync) so the reaper sees it as stale."""
    with get_tenant_connection(TENANT) as conn:
        conn.execute(text(
            "UPDATE s1_sync_jobs SET "
            "claimed_at = NOW() - make_interval(mins => :m), "
            "heartbeat_at = NOW() - make_interval(mins => :m) WHERE id = :id"),
            {"m": minutes, "id": job_id})


# --- creation + active-set idempotency -------------------------------------

def test_create_or_get_creates_queued(store):
    job = store.create_or_get_job(connected_org_id=uuid4(), environment_id=42)
    assert job.status == "queued"
    assert job.attempt_count == 0
    assert job.environment_id == 42
    assert job.claimed_at is None
    assert job.last_sync_run_id is None


def test_active_set_idempotency_same_org(store):
    oid = uuid4()
    first = store.create_or_get_job(connected_org_id=oid, environment_id=7)
    second = store.create_or_get_job(connected_org_id=oid, environment_id=7)
    assert first.id == second.id            # one active sync per connected_org
    with get_tenant_connection(TENANT) as conn:
        n = conn.execute(text(
            "SELECT count(*) FROM s1_sync_jobs WHERE connected_org_id = CAST(:o AS uuid)"),
            {"o": str(oid)}).scalar()
    assert n == 1


def test_distinct_orgs_get_distinct_jobs(store):
    a = store.create_or_get_job(connected_org_id=uuid4(), environment_id=1)
    b = store.create_or_get_job(connected_org_id=uuid4(), environment_id=2)
    assert a.id != b.id


def test_fresh_job_after_terminal(store):
    oid = uuid4()
    first = store.create_or_get_job(connected_org_id=oid, environment_id=5)
    store.complete(first.id)
    # the prior is terminal → the partial-unique no longer blocks; a new job
    again = store.create_or_get_job(connected_org_id=oid, environment_id=5)
    assert again.id != first.id
    assert again.status == "queued"


# --- claim + lifecycle ------------------------------------------------------

def test_claim_none_when_empty(store):
    assert store.claim_next_queued_job() is None


def test_lifecycle_create_claim_run_heartbeat_complete(store):
    job = store.create_or_get_job(connected_org_id=uuid4(), environment_id=9)

    claimed = store.claim_next_queued_job()
    assert claimed.id == job.id
    assert claimed.status == "claimed"
    assert claimed.claimed_at is not None

    attempt_no = store.mark_running(job.id)
    assert attempt_no == 1
    running = store.get_job(job.id)
    assert running.status == "running"
    assert running.attempt_count == 1
    assert running.started_at is not None

    sr = uuid4()
    store.set_sync_run(job.id, sr)
    assert store.get_job(job.id).last_sync_run_id == str(sr)

    store.heartbeat(job.id)
    assert store.get_job(job.id).heartbeat_at is not None

    store.complete(job.id)
    done = store.get_job(job.id)
    assert done.status == "completed"
    assert done.completed_at is not None


def test_claim_picks_oldest_and_skips_claimed(store):
    a = store.create_or_get_job(connected_org_id=uuid4(), environment_id=1)
    time.sleep(0.01)                         # deterministic created_at ordering
    b = store.create_or_get_job(connected_org_id=uuid4(), environment_id=2)

    first = store.claim_next_queued_job()
    second = store.claim_next_queued_job()
    assert first.id == a.id                  # oldest first
    assert second.id == b.id
    assert store.claim_next_queued_job() is None   # both now claimed


# --- reaper -----------------------------------------------------------------

def test_reaper_fails_stale_and_preserves_sync_run(store):
    job = store.create_or_get_job(connected_org_id=uuid4(), environment_id=3)
    store.claim_next_queued_job()
    store.mark_running(job.id)
    sr = uuid4()
    store.set_sync_run(job.id, sr)
    _backdate(job.id, minutes=60)            # > the 45-min default → stale

    reaped = store.reap_stale_jobs(stale_minutes=45)
    assert reaped == 1
    failed = store.get_job(job.id)
    assert failed.status == "failed"
    assert failed.error_code == "stale_timeout"
    assert failed.last_sync_run_id == str(sr)   # survives → a fresh job resumes


def test_reaper_skips_fresh_job(store):
    job = store.create_or_get_job(connected_org_id=uuid4(), environment_id=4)
    store.claim_next_queued_job()
    store.heartbeat(job.id)                   # fresh activity → not stale
    assert store.reap_stale_jobs(stale_minutes=45) == 0
    assert store.get_job(job.id).status == "claimed"


def test_reaper_respects_custom_threshold(store):
    job = store.create_or_get_job(connected_org_id=uuid4(), environment_id=6)
    store.claim_next_queued_job()
    store.mark_running(job.id)
    _backdate(job.id, minutes=20)             # stale at 10-min, fresh at 45-min
    assert store.reap_stale_jobs(stale_minutes=45) == 0     # under the bound
    assert store.reap_stale_jobs(stale_minutes=10) == 1     # over a tighter bound
    assert store.get_job(job.id).status == "failed"


# --- terminal guard ---------------------------------------------------------

def test_fail_does_not_clobber_completed(store):
    job = store.create_or_get_job(connected_org_id=uuid4(), environment_id=8)
    store.complete(job.id)
    store.fail(job.id, error_code="late", error_message="should not apply")
    after = store.get_job(job.id)
    assert after.status == "completed"        # guarded: NOT IN terminal
    assert after.error_code is None


def test_complete_does_not_clobber_failed(store):
    """D-341 (the S4 CORR-2 port): a zombie worker returning after the reaper
    failed its job must not resurrect it to completed."""
    job = store.create_or_get_job(connected_org_id=uuid4(), environment_id=8)
    store.claim_next_queued_job()
    store.mark_running(job.id)
    store.fail(job.id, error_code="stale_timeout", error_message="reaped")
    store.complete(job.id)                    # the zombie's late complete()
    after = store.get_job(job.id)
    assert after.status == "failed"           # guarded: NOT IN terminal
    assert after.error_code == "stale_timeout"


# --- heartbeat fencing (D-341) ----------------------------------------------

def test_heartbeat_active_returns_true(store):
    job = store.create_or_get_job(connected_org_id=uuid4(), environment_id=3)
    store.claim_next_queued_job()
    store.mark_running(job.id)
    assert store.heartbeat(job.id) is True
    assert store.get_job(job.id).heartbeat_at is not None


def test_heartbeat_rejected_on_reaped_job(store):
    """A rejected beat is the zombie's 'I've been reaped' signal — and must
    never resurrect heartbeat_at on the terminal row."""
    job = store.create_or_get_job(connected_org_id=uuid4(), environment_id=3)
    store.claim_next_queued_job()
    store.mark_running(job.id)
    store.fail(job.id, error_code="stale_timeout", error_message="reaped")
    before = store.get_job(job.id).heartbeat_at
    assert store.heartbeat(job.id) is False
    assert store.get_job(job.id).heartbeat_at == before


# --- requeue (graceful shutdown, D-341) ---------------------------------------

def test_requeue_running_job_keeps_anchor(store):
    job = store.create_or_get_job(connected_org_id=uuid4(), environment_id=5)
    store.claim_next_queued_job()
    store.mark_running(job.id)
    sr = uuid4()
    store.set_sync_run(job.id, sr)
    store.heartbeat(job.id)

    assert store.requeue(job.id) is True
    after = store.get_job(job.id)
    assert after.status == "queued"
    assert after.claimed_at is None
    assert after.heartbeat_at is None
    assert after.last_sync_run_id == str(sr)  # the resume anchor survives
    assert after.attempt_count == 1           # attempts are history, kept


def test_requeue_does_not_resurrect_failed(store):
    job = store.create_or_get_job(connected_org_id=uuid4(), environment_id=5)
    store.claim_next_queued_job()
    store.mark_running(job.id)
    store.fail(job.id, error_code="stale_timeout", error_message="reaped")
    assert store.requeue(job.id) is False
    assert store.get_job(job.id).status == "failed"


def test_requeued_job_is_the_orgs_single_active_row(store):
    """A requeued row stays inside the active partial-unique set — the org
    still has exactly one active job, and create_or_get returns it."""
    oid = uuid4()
    job = store.create_or_get_job(connected_org_id=oid, environment_id=5)
    store.claim_next_queued_job()
    store.mark_running(job.id)
    store.requeue(job.id)
    again = store.create_or_get_job(connected_org_id=oid, environment_id=5)
    assert again.id == job.id
    reclaimed = store.claim_next_queued_job()
    assert reclaimed.id == job.id             # claimable again after requeue
