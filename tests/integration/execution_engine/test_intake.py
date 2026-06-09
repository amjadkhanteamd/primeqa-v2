"""Integration: the S4 execution enqueue boundary (`enqueue_s4_execution`) + the
full offline spine loop (enqueue -> worker tick -> completed).

Per-tenant (local-PG governance schema, via conftest's `db_setup` + the B1 queue).
DB-gated: the module skips when local PostgreSQL is unreachable.
"""
from __future__ import annotations

from uuid import uuid4

from primeqa.execution_engine.consumer import run_s4_execution_tick
from primeqa.execution_engine.intake import enqueue_s4_execution
from primeqa.execution_engine.jobs import ExecutionJobStore

from .conftest import TEST_TENANT_ID


def _store():
    return ExecutionJobStore(TEST_TENANT_ID)


def test_enqueue_creates_a_queued_job():
    tid = uuid4()
    job = enqueue_s4_execution(
        tenant_id=TEST_TENANT_ID, test_id=tid, environment_id=7, created_by=3)
    assert job.status == "queued"
    assert job.test_id == str(tid) and job.environment_id == 7


def test_enqueue_is_idempotent_while_active():
    tid = uuid4()
    a = enqueue_s4_execution(tenant_id=TEST_TENANT_ID, test_id=tid, environment_id=7)
    b = enqueue_s4_execution(tenant_id=TEST_TENANT_ID, test_id=tid, environment_id=7)
    assert a.id == b.id                              # same active job, not a duplicate


def test_enqueue_rerunnable_after_terminal():
    tid = uuid4()
    a = enqueue_s4_execution(tenant_id=TEST_TENANT_ID, test_id=tid, environment_id=7)
    _store().complete(a.id)                          # terminal
    c = enqueue_s4_execution(tenant_id=TEST_TENANT_ID, test_id=tid, environment_id=7)
    assert c.id != a.id                              # a fresh job once the prior is terminal


def test_enqueue_then_tick_completes_the_job():
    # The full spine with PRODUCTION DEFAULTS (no client_resolver, the sync run_fn):
    # enqueue -> the worker tick claims + runs it -> completed. No seeded recipe, so
    # the run returns ran=False (no_eligible_recipe) before any client/SF is touched
    # — proving the enqueue -> consume -> run-path -> complete wiring end-to-end,
    # offline. (The same tick the worker fires, so green here ⇒ the production loop
    # runs an enqueued job on the next poll.)
    tid = uuid4()
    job = enqueue_s4_execution(tenant_id=TEST_TENANT_ID, test_id=tid, environment_id=7)
    outcomes = run_s4_execution_tick([TEST_TENANT_ID])           # production defaults
    assert outcomes[TEST_TENANT_ID] == f"processed:{job.id}"
    assert _store().get_job(job.id).status == "completed"
