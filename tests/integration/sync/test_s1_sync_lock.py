"""Governance tests for the D-341 org advisory lock (zombie/resume race).

A REAL ``SyncEngine`` against the governance DB, with phases patched to
instant success/failure — no Salesforce. Proves the run-level fence:

* a held lock → ``run_sync`` refuses with ``SyncAlreadyRunningError`` and
  leaves ZERO droppings (no sync_run row);
* a completed run releases the lock (a fresh probe acquires it);
* a phase failure ALSO releases the lock (the finally path).

The lock is session-scoped (``pg_try_advisory_lock``), so "held" is simulated
with a dedicated AUTOCOMMIT connection kept open across the run attempt —
exactly the shape of a live zombie worker's session.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text

from primeqa.semantic.connection import get_engine, get_tenant_connection
from primeqa.sync.credentials import ensure_connected_org_for_environment
from primeqa.sync.exceptions import SyncAlreadyRunningError
from primeqa.sync.result import PhaseResult

TENANT = 1

_LOCK_SQL = ("SELECT pg_try_advisory_lock("
             "hashtextextended('s1_sync:' || CAST(:org AS text), 0))")
_UNLOCK_ALL = "SELECT pg_advisory_unlock_all()"


def _seed_org(env_id: int) -> str:
    with get_tenant_connection(TENANT) as conn:
        return ensure_connected_org_for_environment(
            conn, env_id, "https://t.my.salesforce.com")


def _make_engine():
    """A real SyncEngine on the governance DB. The SF client is a configured
    mock: finalize reads describe_calls + metadata_gaps off it."""
    from primeqa.sync.engine import SyncEngine
    sf = MagicMock(name="sf_client")
    sf.describe_calls = 0
    sf.metadata_gaps = []
    return SyncEngine(engine_db=get_engine(), sf_client=sf,
                      tenant_schema=f"tenant_{TENANT}")


def _success_phase(ctx, conn) -> PhaseResult:
    return PhaseResult(entity_type="x")


def _failing_phase(ctx, conn) -> PhaseResult:
    raise RuntimeError("boom")


@pytest.fixture
def holder():
    """A dedicated session that can hold the org lock (the zombie's shape).
    Always unlocks + closes at teardown."""
    conn = get_engine().connect().execution_options(isolation_level="AUTOCOMMIT")
    yield conn
    try:
        conn.execute(text(_UNLOCK_ALL))
        conn.close()
    except Exception:
        conn.invalidate()


def _run_count(org: str) -> int:
    with get_tenant_connection(TENANT) as conn:
        return int(conn.execute(text(
            "SELECT COUNT(*) FROM sync_runs WHERE source_org_id = CAST(:o AS uuid)"),
            {"o": org}).scalar() or 0)


def test_lock_held_refuses_and_leaves_no_droppings(holder):
    org = _seed_org(991201)
    eng = _make_engine()
    assert holder.execute(text(_LOCK_SQL), {"org": org}).scalar() is True

    before = _run_count(org)
    with pytest.raises(SyncAlreadyRunningError):
        eng.run_sync(org)
    assert _run_count(org) == before          # refused BEFORE any row was written


def test_lock_released_after_successful_run(holder):
    org = _seed_org(991202)
    eng = _make_engine()
    # Resume path (skips the org-level skip gate — no SF probe needed): create
    # the run row the way the engine does, then resume it through all phases.
    sr_id, _seq = eng._create_sync_run_row(org)
    with patch("primeqa.sync.engine.get_phase_function",
               return_value=_success_phase):
        out = eng.run_sync(org, resume_sync_run_id=sr_id)
    assert out == sr_id

    # The run finished → the lock is free: a fresh probe acquires it.
    assert holder.execute(text(_LOCK_SQL), {"org": org}).scalar() is True
    with get_tenant_connection(TENANT) as conn:
        row = conn.execute(text(
            "SELECT phase, status, attempt_passes, active_seconds "
            "FROM sync_runs WHERE id = CAST(:i AS uuid)"), {"i": sr_id}
        ).mappings().first()
    # structural-complete: phase advanced to enrichment (or already finalized
    # to done — with empty enrichment queues maybe_finalize_run may finish it
    # immediately; either way the pass completed and the lock must be free).
    assert row["phase"] in ("enrichment", "done")
    assert row["status"] in ("running", "success", "partial_success")
    assert row["attempt_passes"] == 1         # D-341 pass accounting
    assert row["active_seconds"] >= 0


def test_lock_released_after_phase_failure(holder):
    org = _seed_org(991203)
    eng = _make_engine()
    sr_id, _seq = eng._create_sync_run_row(org)
    with patch("primeqa.sync.engine.get_phase_function",
               return_value=_failing_phase):
        out = eng.run_sync(org, resume_sync_run_id=sr_id)
    assert out == sr_id

    assert holder.execute(text(_LOCK_SQL), {"org": org}).scalar() is True
    with get_tenant_connection(TENANT) as conn:
        status = conn.execute(text(
            "SELECT status FROM sync_runs WHERE id = CAST(:i AS uuid)"),
            {"i": sr_id}).scalar()
    assert status == "failure"                # finalized, and the lock still freed


def test_fence_probe_consulted_between_phases():
    """The engine-side fence: a probe that flips mid-run aborts the loop with
    NO failure stamp and NO structural-complete — the run stays resumable."""
    org = _seed_org(991204)
    eng = _make_engine()
    sr_id, _seq = eng._create_sync_run_row(org)

    calls = {"n": 0}

    def probe():
        calls["n"] += 1
        return "fenced by test" if calls["n"] >= 3 else None

    with patch("primeqa.sync.engine.get_phase_function",
               return_value=_success_phase):
        out = eng.run_sync(org, resume_sync_run_id=sr_id, should_abort=probe)
    assert out == sr_id
    assert calls["n"] == 3                    # aborted before the 3rd phase
    with get_tenant_connection(TENANT) as conn:
        row = conn.execute(text(
            "SELECT status, phase, last_completed_phase, error_message "
            "FROM sync_runs WHERE id = CAST(:i AS uuid)"), {"i": sr_id}
        ).mappings().first()
    assert row["status"] == "running"         # resumable: no failure stamp
    assert row["phase"] == "structural"       # never structural-completed
    assert row["error_message"] is None
    assert row["last_completed_phase"] == "PicklistValueSet"  # 2 phases committed
