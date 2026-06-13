"""Integration: S4 execution consumer + reaper ticks (D-131, Phase 3 slice B2).

Per-tenant (local-PG governance schema, via conftest's ``db_setup`` + the B1 queue).
The async run is injected (``run_fn``) so these tests isolate the consumer's
orchestration — claim -> attempt -> complete/fail -> per-tenant isolation — without
seeding a full approved recipe (the async run itself is B0-tested). DB-gated: the
module skips when local PostgreSQL is unreachable.
"""
from __future__ import annotations

import types
from uuid import UUID, uuid4

from primeqa.execution_engine.consumer import (
    process_execution_job_for_tenant,
    run_s4_execution_tick,
    run_s4_reaper_tick,
)
from primeqa.execution_engine.errors import CredentialResolutionError, PlanTranslationError
from primeqa.execution_engine.jobs import ExecutionJobStore
from primeqa.execution_engine.run import RunPathResult
from primeqa.semantic.connection import get_tenant_connection
from sqlalchemy import text

from .conftest import TEST_TENANT_ID


def _store():
    return ExecutionJobStore(TEST_TENANT_ID)


def _stub_client_resolver(tenant_id, environment_id):
    return types.SimpleNamespace(query=lambda soql: [])


class _RecordingRun:
    """A fake ``run_fn`` recording its call + returning a canned result."""

    def __init__(self, result=None, raises=None):
        self._result = result or RunPathResult(
            ran=True, selected_recipe_id=uuid4(),
            evidence=types.SimpleNamespace(outcome="passed"))
        self._raises = raises
        self.calls = []

    def __call__(self, tenant_id, test_id, *, environment_id, client):
        self.calls.append({"tenant_id": tenant_id, "test_id": test_id,
                           "environment_id": environment_id, "client": client})
        if self._raises is not None:
            raise self._raises
        return self._result


# ---------------------------------------------------------------------------
# process_execution_job_for_tenant
# ---------------------------------------------------------------------------

def test_empty_queue_returns_none():
    assert process_execution_job_for_tenant(
        TEST_TENANT_ID, client_resolver=_stub_client_resolver, run_fn=_RecordingRun()) is None


def test_one_job_runs_and_completes():
    s = _store()
    tid = uuid4()
    job = s.create_or_get_job(test_id=tid, environment_id=7)
    run = _RecordingRun()

    jid = process_execution_job_for_tenant(
        TEST_TENANT_ID, client_resolver=_stub_client_resolver, run_fn=run)

    assert jid == job.id
    # the consumer called the async run with the job's pinned (test_id, env) + a client.
    assert len(run.calls) == 1
    call = run.calls[0]
    assert call["test_id"] == UUID(job.test_id) and call["environment_id"] == 7
    assert call["client"] is not None
    # lifecycle: claimed -> attempted -> completed.
    j = s.get_job(job.id)
    assert j.status == "completed" and j.attempt_count == 1
    assert s.get_attempts(job.id)[-1]["status"] == "completed"


def test_run_raise_fails_job_with_classified_code():
    s = _store()
    job = s.create_or_get_job(test_id=uuid4(), environment_id=7)
    run = _RecordingRun(raises=PlanTranslationError("boom"))

    process_execution_job_for_tenant(
        TEST_TENANT_ID, client_resolver=_stub_client_resolver, run_fn=run)

    j = s.get_job(job.id)
    assert j.status == "failed" and j.error_code == "execution_error"
    assert s.get_attempts(job.id)[-1]["status"] == "failed"


def test_client_resolver_raise_fails_with_credential_code():
    s = _store()
    job = s.create_or_get_job(test_id=uuid4(), environment_id=7)

    def _bad_resolver(tenant_id, environment_id):
        raise CredentialResolutionError("no token")

    process_execution_job_for_tenant(
        TEST_TENANT_ID, client_resolver=_bad_resolver, run_fn=_RecordingRun())

    j = s.get_job(job.id)
    assert j.status == "failed" and j.error_code == "credential_error"


def test_ran_false_completes_the_job():
    # no-eligible-recipe is not a worker failure — the job completes.
    s = _store()
    job = s.create_or_get_job(test_id=uuid4(), environment_id=7)
    run = _RecordingRun(result=RunPathResult(ran=False, reason="no_eligible_recipe"))

    process_execution_job_for_tenant(
        TEST_TENANT_ID, client_resolver=_stub_client_resolver, run_fn=run)

    assert s.get_job(job.id).status == "completed"


def test_default_path_no_resolver_passes_client_none():
    # Production default: no client_resolver injected → the consumer passes
    # client=None, so the sync run_fn self-resolves the per-kind client (Tooling
    # for metadata, Data for data) after it selects the recipe.
    s = _store()
    job = s.create_or_get_job(test_id=uuid4(), environment_id=7)
    run = _RecordingRun()
    jid = process_execution_job_for_tenant(TEST_TENANT_ID, run_fn=run)   # no resolver
    assert jid == job.id
    assert run.calls[0]["client"] is None
    assert s.get_job(job.id).status == "completed"


def test_default_run_fn_is_the_async_path():
    # Drift-guard for the D-230.2 consumer flip: the default run_fn is the ASYNC path
    # — which now runs ALL recipe kinds (metadata + data) holding NO DB connection
    # across SF I/O (the data-path deferral was lifted by WorldPlan pre-resolution).
    # The sync run_recipe_execution_for_tenant remains the live-proven fallback.
    import inspect

    from primeqa.execution_engine.run import (
        run_recipe_execution_async,
        run_recipe_execution_for_tenant,
    )
    default = inspect.signature(
        process_execution_job_for_tenant).parameters["run_fn"].default
    assert default is run_recipe_execution_async
    assert default is not run_recipe_execution_for_tenant


# ---------------------------------------------------------------------------
# run_s4_execution_tick — per-tenant outcomes + isolation
# ---------------------------------------------------------------------------

def test_tick_processes_and_isolates_a_failing_tenant():
    s = _store()
    job = s.create_or_get_job(test_id=uuid4(), environment_id=7)
    _BOGUS = 987_654   # no such tenant schema → its claim raises → isolated

    outcomes = run_s4_execution_tick(
        [TEST_TENANT_ID, _BOGUS],
        client_resolver=_stub_client_resolver, run_fn=_RecordingRun())

    assert outcomes[TEST_TENANT_ID] == f"processed:{job.id}"
    assert outcomes[_BOGUS].startswith("error:")     # isolated, not raised
    assert s.get_job(job.id).status == "completed"    # the real tenant still ran


def test_tick_reports_empty_for_a_drained_tenant():
    outcomes = run_s4_execution_tick(
        [TEST_TENANT_ID], client_resolver=_stub_client_resolver, run_fn=_RecordingRun())
    assert outcomes[TEST_TENANT_ID] == "empty"


# ---------------------------------------------------------------------------
# run_s4_reaper_tick
# ---------------------------------------------------------------------------

def test_reaper_tick_reaps_a_stale_job():
    s = _store()
    job = s.create_or_get_job(test_id=uuid4(), environment_id=7)
    s.claim(job.id)
    s.start_attempt(job.id)
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        conn.execute(text(
            "UPDATE s4_execution_jobs SET claimed_at = NOW() - INTERVAL '1 hour', "
            "heartbeat_at = NOW() - INTERVAL '1 hour' WHERE id = :id"), {"id": job.id})

    reaped = run_s4_reaper_tick([TEST_TENANT_ID], stale_minutes=10)

    assert reaped[TEST_TENANT_ID] == 1
    assert s.get_job(job.id).status == "failed"
