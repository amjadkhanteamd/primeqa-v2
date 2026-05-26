"""Integration: S3 stale-job reaper (D-106.4 slice 5) — per-tenant governance DB.

reap_stale_jobs fails jobs stuck in claimed/running past the heartbeat timeout
(reusing fail(), so the open attempt is finalized); run_s3_reaper_tick isolates
each tenant. Staleness is simulated by backdating heartbeat_at (no waiting).
"""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import text

from primeqa.generation.consumer import run_s3_reaper_tick
from primeqa.generation.jobs import GenerationJobStore
from primeqa.semantic.connection import get_tenant_connection

from .conftest import TEST_TENANT_ID


def _make_running_job(store: GenerationJobStore, s1_version_seq: int) -> int:
    """Create -> claim -> heartbeat -> start_attempt: a job in 'running' with a
    fresh heartbeat (the consumer's normal pre-run state)."""
    store.create_or_get_job(requirement_key=f"REQ-{uuid4().hex[:8]}",
                            s1_version_seq=s1_version_seq, created_by=1)
    claimed = store.claim_next_queued_job()      # clean_ledger -> the only queued job
    assert claimed is not None
    store.heartbeat(claimed.id)
    store.start_attempt(claimed.id)
    return claimed.id


def _backdate_heartbeat(job_id: int, minutes: int) -> None:
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        conn.execute(text(
            "UPDATE s3_generation_jobs SET heartbeat_at = NOW() - (:m * INTERVAL '1 minute') "
            "WHERE id = :id"), {"m": minutes, "id": job_id})


# ---------------------------------------------------------------------------
# reap_stale_jobs
# ---------------------------------------------------------------------------

def test_reaps_stale_running_job_and_finalizes_attempt(seeded):
    store = GenerationJobStore(TEST_TENANT_ID)
    jid = _make_running_job(store, seeded["v1"])
    assert store.get_job(jid).status == "running"

    _backdate_heartbeat(jid, 20)                 # 20 min stale
    n = store.reap_stale_jobs(stale_minutes=10)
    assert n == 1

    job = store.get_job(jid)
    assert job.status == "failed" and job.error_code == "stale_timeout"
    att = store.get_attempts(jid)[-1]
    assert att["status"] == "failed" and att["finished_at"] is not None


def test_fresh_heartbeat_is_not_reaped(seeded):
    # The timeout boundary: a job heartbeated just now is left running.
    store = GenerationJobStore(TEST_TENANT_ID)
    jid = _make_running_job(store, seeded["v1"])   # heartbeat = NOW
    assert store.reap_stale_jobs(stale_minutes=10) == 0
    assert store.get_job(jid).status == "running"


def test_terminal_job_is_untouched(seeded):
    # A completed job, even with a backdated heartbeat, is not in claimed/running
    # -> never reaped (and fail()'s non-terminal guard would protect it anyway).
    store = GenerationJobStore(TEST_TENANT_ID)
    jid = _make_running_job(store, seeded["v1"])
    store.complete(jid)
    _backdate_heartbeat(jid, 20)
    assert store.reap_stale_jobs(stale_minutes=10) == 0
    assert store.get_job(jid).status == "completed"


# ---------------------------------------------------------------------------
# run_s3_reaper_tick — per-tenant isolation
# ---------------------------------------------------------------------------

def test_reaper_tick_isolates_per_tenant_failure(seeded):
    store = GenerationJobStore(TEST_TENANT_ID)
    jid = _make_running_job(store, seeded["v1"])
    _backdate_heartbeat(jid, 20)

    # tenant 999999 has no schema -> its reap raises -> isolated (counts 0); the
    # good tenant is still reaped.
    reaped = run_s3_reaper_tick([999999, TEST_TENANT_ID], stale_minutes=10)
    assert reaped[999999] == 0
    assert reaped[TEST_TENANT_ID] == 1
    assert store.get_job(jid).status == "failed"
