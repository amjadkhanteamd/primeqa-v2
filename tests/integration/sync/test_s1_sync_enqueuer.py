"""Governance tests for the S1-sync enqueuer + reaper ticks (D-153, S0.4).

``run_s1_sync_enqueuer_tick`` (the cadence) + ``run_s1_sync_reaper_tick`` against
the governance DB. The enqueuer scans ``connected_orgs`` per tenant, so the
assertions are **per-org** (``_has_active_job`` / ``_active_job_anchor``) rather
than on the global enqueued count — robust to other orgs in the tenant.
``clean_jobs`` (conftest) wipes ``s1_sync_jobs`` per test.
"""
from __future__ import annotations

from sqlalchemy import text

from primeqa.semantic.connection import get_tenant_connection
from primeqa.sync.consumer import (
    run_s1_sync_enqueuer_tick,
    run_s1_sync_reaper_tick,
)
from primeqa.sync.credentials import ensure_connected_org_for_environment
from primeqa.sync.jobs import SyncJobStore

TENANT = 1


# --- helpers ---------------------------------------------------------------

def _seed_org(env_id: int) -> str:
    with get_tenant_connection(TENANT) as conn:
        return ensure_connected_org_for_environment(
            conn, env_id, "https://t.my.salesforce.com")


def _seed_org_no_env(label: str) -> str:
    """A connected_org WITHOUT environment_id (no credential source)."""
    with get_tenant_connection(TENANT) as conn:
        return str(conn.execute(text(
            "INSERT INTO connected_orgs (org_type, sf_instance_url, label) "
            "VALUES ('production', :u, :l) RETURNING id"
        ), {"u": "https://x.my.salesforce.com", "l": label}).scalar())


def _insert_sync_run(org_id, *, status: str = "success",
                     last_completed_phase: str = "Flow",
                     started_hours_ago: float = 0.0) -> str:
    """Insert a sync_run with a backdated started_at. completed_at honors the
    sync_runs_completion_implies_terminal CHECK (terminal ⟺ completed_at set)."""
    mins = int(started_hours_ago * 60)
    with get_tenant_connection(TENANT) as conn:
        return str(conn.execute(text(
            "INSERT INTO sync_runs (source_org_id, status, phase, "
            "last_completed_phase, started_at, completed_at) "
            "VALUES (CAST(:o AS uuid), :s, 'structural', :lcp, "
            "  NOW() - make_interval(mins => :m), "
            "  CASE WHEN :s IN ('success','partial_success','failure') "
            "       THEN NOW() - make_interval(mins => :m) ELSE NULL END) "
            "RETURNING id"
        ), {"o": str(org_id), "s": status, "lcp": last_completed_phase,
            "m": mins}).scalar())


def _has_active_job(org_id) -> int:
    with get_tenant_connection(TENANT) as conn:
        return conn.execute(text(
            "SELECT count(*) FROM s1_sync_jobs "
            "WHERE connected_org_id = CAST(:o AS uuid) "
            "AND status IN ('queued','claimed','running')"), {"o": str(org_id)}).scalar()


def _active_job_anchor(org_id):
    with get_tenant_connection(TENANT) as conn:
        row = conn.execute(text(
            "SELECT last_sync_run_id FROM s1_sync_jobs "
            "WHERE connected_org_id = CAST(:o AS uuid) "
            "AND status IN ('queued','claimed','running') "
            "ORDER BY created_at DESC LIMIT 1"), {"o": str(org_id)}).scalar()
        return str(row) if row else None


# --- enqueuer policy -------------------------------------------------------

def test_enqueuer_enqueues_never_synced_org():
    org = _seed_org(990201)                          # provisioned, no sync_run
    run_s1_sync_enqueuer_tick([TENANT])
    assert _has_active_job(org) == 1


def test_enqueuer_enqueues_stale_complete_org():
    org = _seed_org(990202)
    _insert_sync_run(org, status="success", last_completed_phase="Flow",
                     started_hours_ago=25)            # > 24h cadence → stale
    run_s1_sync_enqueuer_tick([TENANT])
    assert _has_active_job(org) == 1
    assert _active_job_anchor(org) is None            # complete → fresh, no resume


def test_enqueuer_skips_fresh_complete_org():
    org = _seed_org(990203)
    _insert_sync_run(org, status="success", last_completed_phase="Flow",
                     started_hours_ago=1)             # < 24h → fresh
    run_s1_sync_enqueuer_tick([TENANT])
    assert _has_active_job(org) == 0


def test_enqueuer_resumes_incomplete_org():
    org = _seed_org(990204)
    sr = _insert_sync_run(org, status="running", last_completed_phase="Field",
                          started_hours_ago=0.2)      # reaped — incomplete
    run_s1_sync_enqueuer_tick([TENANT])
    assert _has_active_job(org) == 1
    assert _active_job_anchor(org) == sr              # carry-forward → resume


def test_enqueuer_skips_org_without_environment_id():
    org = _seed_org_no_env("_enq_no_env")
    run_s1_sync_enqueuer_tick([TENANT])
    assert _has_active_job(org) == 0                  # no creds → not enqueued


def test_enqueuer_skips_org_with_active_job():
    org = _seed_org(990205)
    SyncJobStore(TENANT).create_or_get_job(
        connected_org_id=org, environment_id=990205)  # already active
    run_s1_sync_enqueuer_tick([TENANT])
    assert _has_active_job(org) == 1                  # not a second job


def test_enqueuer_idempotent_across_ticks():
    org = _seed_org(990206)
    run_s1_sync_enqueuer_tick([TENANT])
    assert _has_active_job(org) == 1
    run_s1_sync_enqueuer_tick([TENANT])               # the org now has an active job
    assert _has_active_job(org) == 1                  # still one


def test_enqueuer_per_tenant_isolation():
    _seed_org(990207)
    out = run_s1_sync_enqueuer_tick([TENANT, 99999])  # 99999 has no schema
    assert out[99999] == 0                            # isolated, not raised
    assert TENANT in out


# --- reaper tick -----------------------------------------------------------

def test_reaper_tick_fails_stale_job():
    org = _seed_org(990208)
    store = SyncJobStore(TENANT)
    job = store.create_or_get_job(connected_org_id=org, environment_id=990208)
    store.claim_next_queued_job()
    store.mark_running(job.id)
    with get_tenant_connection(TENANT) as conn:                 # backdate → stale
        conn.execute(text(
            "UPDATE s1_sync_jobs SET claimed_at = NOW() - make_interval(mins => 60), "
            "heartbeat_at = NOW() - make_interval(mins => 60) WHERE id = :id"),
            {"id": job.id})

    out = run_s1_sync_reaper_tick([TENANT])
    assert out[TENANT] == 1
    assert store.get_job(job.id).status == "failed"


def test_reaper_tick_per_tenant_isolation():
    out = run_s1_sync_reaper_tick([TENANT, 99999])
    assert out[99999] == 0
    assert out[TENANT] == 0                            # no stale jobs this tenant
