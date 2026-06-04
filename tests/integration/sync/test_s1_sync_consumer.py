"""Governance tests for the S1-sync consumer (D-152, cutover Step 0 / S0.3).

The consumer's orchestration against the governance DB, with an **injected fake
engine** (writing controllable ``sync_runs`` rows) + a **stub sf_client_resolver**
— no Salesforce, no real ``run_sync``. (Real entities/queue rows only materialize
in the sandbox e2e, ``test_e2e_sync_scenarios.py`` — ops-deferred.)

Covers: claim → run → ``complete`` (anchor recorded); a ``sync_run`` left
``status='failure'`` → job ``failed`` (``sync_failed`` + message); ``run_sync``
raising → job ``failed`` (``sync_engine_error``); the resume-on-reap story —
``create_or_get_job`` carry-forward seeds the anchor from an incomplete
``sync_run`` and the consumer passes it as ``resume_sync_run_id`` (a complete one →
fresh); per-tenant tick isolation + empty-queue.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from primeqa.semantic.connection import get_tenant_connection
from primeqa.sync.consumer import (
    process_sync_job_for_tenant,
    run_s1_sync_consumer_tick,
)
from primeqa.sync.credentials import ensure_connected_org_for_environment
from primeqa.sync.exceptions import SyncEngineError

TENANT = 1


# --- helpers ---------------------------------------------------------------

def _seed_org(env_id: int, url: str = "https://t.my.salesforce.com") -> str:
    """Provision a connected_orgs sync target (D-150, idempotent on env_id)."""
    with get_tenant_connection(TENANT) as conn:
        return ensure_connected_org_for_environment(conn, env_id, url)


# sync_runs_completion_implies_terminal (20260430_0020): status='running' iff
# completed_at IS NULL; a terminal status iff completed_at IS NOT NULL.
_TERMINAL_COMPLETED = (
    "CASE WHEN :s IN ('success','partial_success','failure') "
    "THEN NOW() ELSE NULL END")


def _insert_sync_run(org_id, *, status: str = "running",
                     last_completed_phase: str = "Field") -> str:
    """Insert a sync_run row directly (simulate a reaped/failed sync)."""
    with get_tenant_connection(TENANT) as conn:
        return str(conn.execute(text(
            "INSERT INTO sync_runs (source_org_id, status, phase, "
            "last_completed_phase, completed_at) "
            f"VALUES (CAST(:o AS uuid), :s, 'structural', :lcp, {_TERMINAL_COMPLETED}) "
            "RETURNING id"
        ), {"o": str(org_id), "s": status, "lcp": last_completed_phase}).scalar())


class _FakeEngine:
    """Stand-in for SyncEngine. ``run_sync`` writes a controllable ``sync_run``
    row (so the consumer's status-read has something to read) and returns its id,
    or raises a configured exception. Records its call args."""

    def __init__(self, *, status: str = "running", error_message=None,
                 last_completed_phase: str = "Flow", raise_exc=None):
        self.status = status
        self.error_message = error_message
        self.last_completed_phase = last_completed_phase
        self.raise_exc = raise_exc
        self.calls: list[dict] = []
        self.returned_id = None

    def run_sync(self, connected_org_id, resume_sync_run_id=None):
        self.calls.append({"org": str(connected_org_id),
                           "resume": resume_sync_run_id})
        if self.raise_exc is not None:
            raise self.raise_exc
        with get_tenant_connection(TENANT) as conn:
            sr_id = conn.execute(text(
                "INSERT INTO sync_runs (source_org_id, status, phase, "
                "last_completed_phase, error_message, completed_at) "
                "VALUES (CAST(:o AS uuid), :s, 'structural', :lcp, :em, "
                f"{_TERMINAL_COMPLETED}) RETURNING id"
            ), {"o": str(connected_org_id), "s": self.status,
                "lcp": self.last_completed_phase,
                "em": self.error_message}).scalar()
        self.returned_id = str(sr_id)
        return self.returned_id


def _stub_resolver(tenant_id, environment_id):
    """A dummy SF client — the fake engine ignores it."""
    return object()


def _factory(fake):
    return lambda engine_db, sf_client, tenant_schema: fake


# --- complete / fail paths -------------------------------------------------

def test_consumer_runs_and_completes(store):
    org = _seed_org(990101)
    job = store.create_or_get_job(connected_org_id=org, environment_id=990101)
    fake = _FakeEngine(status="running")        # structural-complete success

    out = process_sync_job_for_tenant(
        TENANT, sf_client_resolver=_stub_resolver, engine_factory=_factory(fake))

    assert out == job.id
    done = store.get_job(job.id)
    assert done.status == "completed"
    assert done.last_sync_run_id == fake.returned_id      # anchor recorded
    assert fake.calls == [{"org": str(org), "resume": None}]   # fresh → no resume


def test_consumer_fails_on_failure_status(store):
    org = _seed_org(990102)
    job = store.create_or_get_job(connected_org_id=org, environment_id=990102)
    fake = _FakeEngine(status="failure", error_message="phase Object blew up",
                       last_completed_phase="Object")

    process_sync_job_for_tenant(
        TENANT, sf_client_resolver=_stub_resolver, engine_factory=_factory(fake))

    failed = store.get_job(job.id)
    assert failed.status == "failed"
    assert failed.error_code == "sync_failed"
    assert "phase Object blew up" in (failed.error_message or "")


def test_consumer_fails_on_run_sync_raise(store):
    org = _seed_org(990103)
    job = store.create_or_get_job(connected_org_id=org, environment_id=990103)
    fake = _FakeEngine(raise_exc=SyncEngineError("db connectivity lost"))

    process_sync_job_for_tenant(
        TENANT, sf_client_resolver=_stub_resolver, engine_factory=_factory(fake))

    failed = store.get_job(job.id)
    assert failed.status == "failed"
    assert failed.error_code == "sync_engine_error"


# --- resume-on-reap (carry-forward + consumer passes the anchor) -----------

def test_carry_forward_seeds_anchor_and_consumer_resumes(store):
    org = _seed_org(990104)
    sr = _insert_sync_run(org, status="running", last_completed_phase="Field")
    job = store.create_or_get_job(connected_org_id=org, environment_id=990104)
    assert job.last_sync_run_id == sr               # carry-forward seeded it

    fake = _FakeEngine(status="running")
    process_sync_job_for_tenant(
        TENANT, sf_client_resolver=_stub_resolver, engine_factory=_factory(fake))
    assert fake.calls[0]["resume"] == sr            # consumer passed it to run_sync


def test_carry_forward_skips_complete_sync_run(store):
    # structurally-complete (last_completed_phase='Flow') → not resumable → fresh
    org = _seed_org(990105)
    _insert_sync_run(org, status="running", last_completed_phase="Flow")
    job = store.create_or_get_job(connected_org_id=org, environment_id=990105)
    assert job.last_sync_run_id is None

    # terminal-success → also skipped
    org2 = _seed_org(990106)
    _insert_sync_run(org2, status="success", last_completed_phase="Flow")
    job2 = store.create_or_get_job(connected_org_id=org2, environment_id=990106)
    assert job2.last_sync_run_id is None


# --- tick: isolation + lifecycle -------------------------------------------

def test_consumer_empty_queue():
    out = process_sync_job_for_tenant(
        TENANT, sf_client_resolver=_stub_resolver,
        engine_factory=_factory(_FakeEngine()))
    assert out is None


def test_tick_per_tenant_isolation():
    # tenant 99999 has no schema → its claim raises → isolated to error:<Type>;
    # tenant 1's queue is empty → 'empty'. One tenant's failure never starves the other.
    out = run_s1_sync_consumer_tick(
        [TENANT, 99999], sf_client_resolver=_stub_resolver,
        engine_factory=_factory(_FakeEngine()))
    assert out[TENANT] == "empty"
    assert out[99999].startswith("error:")


def test_tick_processes_then_empty(store):
    org = _seed_org(990107)
    job = store.create_or_get_job(connected_org_id=org, environment_id=990107)

    out1 = run_s1_sync_consumer_tick(
        [TENANT], sf_client_resolver=_stub_resolver,
        engine_factory=_factory(_FakeEngine(status="running")))
    assert out1[TENANT] == f"processed:{job.id}"

    out2 = run_s1_sync_consumer_tick(           # the job is now terminal
        [TENANT], sf_client_resolver=_stub_resolver,
        engine_factory=_factory(_FakeEngine(status="running")))
    assert out2[TENANT] == "empty"
