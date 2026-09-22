"""Round 4, part A (AUD-028), DB-real: the run row before provisioning.

Against the governance harness database (``db_setup``, migrated to head — the
round-4 tenant migration included):

1. ``StrandedRecordSink.run_opened`` writes ONE row: outcome running,
   finished_at NULL — and the CHECK refuses the two disagreeing shapes;
2. finalize COMPLETES that row (same run_id, one row, final outcome);
3. ``close_interrupted`` on the worker's KeyboardInterrupt closes it as
   errored with ``worker_shutdown`` on the error surface — and a record the
   run had provisioned points at a run that EXISTS (the key holds);
4. the job reaper closes a row left running past the timeout (SIGKILL case);
5. a running row is INVISIBLE to the product's readers: the runs list, the
   per-claim latest-run read and the readiness resolver all see none of it —
   the visibility every reader had before the write-ahead row existed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from primeqa.execution_engine.evidence import RunEvidence
from primeqa.execution_engine.jobs import ExecutionJobStore
from primeqa.execution_engine.result_store import (
    RUNNING, WORKER_SHUTDOWN, close_stale_running_runs, interrupted_evidence,
    persist_run_evidence)
from primeqa.execution_engine.stranded_cleanup import StrandedRecordSink
from primeqa.semantic.connection import get_tenant_connection

from .conftest import TEST_TENANT_ID

ENV = 7


def _identity():
    return {"run_id": uuid4(), "recipe_id": uuid4(), "recipe_version_seq": 1,
            "claim_test_id": uuid4(), "claim_version_seq": None,
            "started_at": datetime.now(timezone.utc)}


def _row(run_id):
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        return conn.execute(text(
            "SELECT outcome::text AS outcome, finished_at, evidence, failure_category "
            "FROM s4_execution_runs WHERE run_id = CAST(:r AS uuid)"), {"r": str(run_id)}).mappings().first()


def _count(run_id):
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        return conn.execute(text("SELECT count(*) FROM s4_execution_runs WHERE run_id = CAST(:r AS uuid)"),
                            {"r": str(run_id)}).scalar()


@pytest.fixture
def sink(db_setup):
    s = StrandedRecordSink(TEST_TENANT_ID, ENV)
    opened = []
    yield s, opened
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        for rid in opened:
            conn.execute(text("DELETE FROM s4_created_records WHERE run_id = CAST(:r AS uuid)"), {"r": str(rid)})
            conn.execute(text("DELETE FROM s4_execution_runs WHERE run_id = CAST(:r AS uuid)"), {"r": str(rid)})


def test_run_opened_writes_the_running_row_and_the_check_holds(sink):
    s, opened = sink
    ident = _identity(); opened.append(ident["run_id"])
    s.run_opened(**ident)
    row = _row(ident["run_id"])
    assert row["outcome"] == RUNNING and row["finished_at"] is None
    assert row["evidence"]["run_state"] == "running" and row["evidence"]["steps"] == []
    # the CHECK: running must be unfinished, finished must not be running
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        for sql in ("UPDATE s4_execution_runs SET finished_at = now() WHERE run_id = CAST(:r AS uuid)",
                    "UPDATE s4_execution_runs SET outcome = 'passed' WHERE run_id = CAST(:r AS uuid)"):
            with pytest.raises(Exception) as ex:
                with conn.begin_nested():             # the savepoint rolls back on the raise
                    conn.execute(text(sql), {"r": str(ident["run_id"])})
            assert type(getattr(ex.value, "orig", ex.value)).__name__ == "CheckViolation", sql


def test_finalize_completes_the_opened_row(sink):
    s, opened = sink
    ident = _identity(); opened.append(ident["run_id"])
    s.run_opened(**ident)
    ev = RunEvidence(run_id=ident["run_id"], recipe_id=ident["recipe_id"], recipe_version_seq=1,
                     claim_test_id=ident["claim_test_id"], claim_version_seq=None, environment_id=ENV,
                     api_choice="rest", outcome="passed", started_at=ident["started_at"],
                     finished_at=datetime.now(timezone.utc), steps=(), error=None)
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        persist_run_evidence(Session(bind=conn), ev)
    assert _count(ident["run_id"]) == 1
    row = _row(ident["run_id"])
    assert row["outcome"] == "passed" and row["finished_at"] is not None
    assert row["evidence"]["api_choice"] == "rest" and "run_state" not in row["evidence"]
    s.run_returned(ident["run_id"])
    assert s.close_interrupted(KeyboardInterrupt()) is None      # nothing left open


def test_a_shutdown_closes_the_run_as_errored_and_its_records_point_at_it(sink):
    s, opened = sink
    ident = _identity(); opened.append(ident["run_id"])
    s.run_opened(**ident)
    s.created(ident["run_id"], "Case", "500PROVISIONED", 0)       # the write-ahead record
    closed = s.close_interrupted(KeyboardInterrupt("SIGTERM 15"))
    assert closed == ident["run_id"]
    row = _row(ident["run_id"])
    assert row["outcome"] == "errored" and row["finished_at"] is not None
    assert row["evidence"]["error"]["error_type"] == WORKER_SHUTDOWN
    assert "shutdown" in row["evidence"]["error"]["message"]
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        orphans = conn.execute(text(
            "SELECT count(*) FROM s4_created_records c WHERE c.run_id = CAST(:r AS uuid) "
            "AND NOT EXISTS (SELECT 1 FROM s4_execution_runs r WHERE r.run_id = c.run_id)"),
            {"r": str(ident["run_id"])}).scalar()
        recs = conn.execute(text("SELECT count(*) FROM s4_created_records WHERE run_id = CAST(:r AS uuid)"),
                            {"r": str(ident["run_id"])}).scalar()
    assert (recs, orphans) == (1, 0)
    assert s.pop_last_closed() == ident["run_id"] and s.pop_last_closed() is None


def test_the_reaper_closes_a_row_left_running_past_the_timeout(sink):
    s, opened = sink
    old = _identity(); old["started_at"] = datetime.now(timezone.utc) - timedelta(minutes=30)
    fresh = _identity()
    opened += [old["run_id"], fresh["run_id"]]
    s.run_opened(**old)
    s.run_opened(**fresh)
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        assert close_stale_running_runs(conn, stale_minutes=10) == 1
    assert _row(old["run_id"])["outcome"] == "errored"
    assert _row(old["run_id"])["evidence"]["error"]["error_type"] == "stale_timeout"
    assert _row(fresh["run_id"])["outcome"] == RUNNING
    # and the job store's reaper tick does the same through its own path
    stale2 = _identity(); stale2["started_at"] = datetime.now(timezone.utc) - timedelta(minutes=30)
    opened.append(stale2["run_id"]); s.run_opened(**stale2)
    ExecutionJobStore(TEST_TENANT_ID).reap_stale_jobs(stale_minutes=10)
    assert _row(stale2["run_id"])["outcome"] == "errored"


def test_a_running_row_is_invisible_to_the_readers(sink):
    from primeqa.intelligence.s4_execution_console import _list_runs, _runs_where  # noqa: F401
    from primeqa.sync import readiness

    s, opened = sink
    ident = _identity(); opened.append(ident["run_id"])
    s.run_opened(**ident)
    rid, claim = str(ident["run_id"]), ident["claim_test_id"]
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        total, rows = _list_runs(conn, limit=50, offset=0, environment_id=ENV)
        assert rid not in {r["run_id"] for r in rows}
        latest = conn.execute(text(
            "SELECT CAST(run_id AS text) FROM s4_execution_runs WHERE claim_test_id = CAST(:c AS uuid) "
            "AND environment_id = :e AND finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT 1"),
            {"c": str(claim), "e": ENV}).scalar()
        assert latest is None
        bulk = conn.execute(text(readiness._BULK_LATEST_RUN_SQL),
                            {"claims": [str(claim)], "envs": [ENV]}).mappings().all()
        assert bulk == []
