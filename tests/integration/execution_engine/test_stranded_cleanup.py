"""Integration: durable data-path cleanup (D-230, 2.4) — the write-ahead sink +
the crash-recovery reaper, against the governance DB (the migrated
``s4_created_records`` incl. ``environment_id``).

The sink writes/marks in its own committed transactions; the reaper finds
stranded rows, deletes via an injected client, and marks them cleaned. The live
Salesforce client is injected (a fake) — no creds, no real org.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from primeqa.semantic.connection import get_tenant_connection
from primeqa.execution_engine.stranded_cleanup import (
    StrandedRecordSink,
    reap_stranded_records,
)

from tests.integration.generation.conftest import TEST_TENANT_ID

pytestmark = pytest.mark.usefixtures("db_setup")


@pytest.fixture(autouse=True)
def _clean():
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        conn.execute(text("DELETE FROM s4_created_records"))
    yield


class _FakeClient:
    """Records delete calls; ``fail_on`` record_ids raise (transient failure)."""
    def __init__(self, fail_on=()):
        self.deleted = []
        self._fail_on = set(fail_on)

    def delete(self, sobject, record_id):
        if record_id in self._fail_on:
            raise RuntimeError("transient delete failure")
        self.deleted.append((sobject, record_id))
        return {"success": True}


def _rows():
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        return [dict(r) for r in conn.execute(text(
            "SELECT run_id, sobject, record_id, created_seq, cleaned, environment_id "
            "FROM s4_created_records ORDER BY created_seq")).mappings()]


def _seed(run_id, sobject, record_id, *, env=7, cleaned=False, age_minutes=60):
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        conn.execute(text(
            "INSERT INTO s4_created_records "
            "(run_id, sobject, record_id, created_seq, cleaned, environment_id, created_at) "
            "VALUES (CAST(:r AS uuid), :so, :rid, 0, :cl, :env, "
            f"NOW() - INTERVAL '{int(age_minutes)} minutes')"
        ), {"r": str(run_id), "so": sobject, "rid": record_id, "cl": cleaned, "env": env})


# ---------------------------------------------------------------------------
# The sink (write-ahead)
# ---------------------------------------------------------------------------

def test_sink_writes_row_uncleaned_with_environment():
    run_id = uuid4()
    sink = StrandedRecordSink(TEST_TENANT_ID, environment_id=42)
    sink.created(run_id, "Case", "500AAA", 0)
    rows = _rows()
    assert len(rows) == 1
    r = rows[0]
    assert r["sobject"] == "Case" and r["record_id"] == "500AAA"
    assert r["cleaned"] is False and r["environment_id"] == 42


def test_sink_marks_cleaned():
    run_id = uuid4()
    sink = StrandedRecordSink(TEST_TENANT_ID, environment_id=42)
    sink.created(run_id, "Case", "500AAA", 0)
    sink.cleaned(run_id, "500AAA")
    assert _rows()[0]["cleaned"] is True


def test_sink_never_raises_on_bad_tenant():
    # Best-effort: a write failure (bad tenant) is swallowed, not raised.
    StrandedRecordSink(-1, environment_id=1).created(uuid4(), "Case", "x", 0)
    StrandedRecordSink(-1, environment_id=1).cleaned(uuid4(), "x")  # no raise


# ---------------------------------------------------------------------------
# The reaper
# ---------------------------------------------------------------------------

def test_reaper_deletes_stranded_and_marks_cleaned():
    _seed(uuid4(), "Case", "500STRANDED", env=7, age_minutes=60)
    client = _FakeClient()
    n = reap_stranded_records(
        TEST_TENANT_ID, stale_minutes=15,
        client_resolver=lambda session, env_id: client)
    assert n == 1
    assert client.deleted == [("Case", "500STRANDED")]
    assert _rows()[0]["cleaned"] is True


def test_reaper_skips_recent_cleaned_and_null_env():
    _seed(uuid4(), "Case", "RECENT", env=7, age_minutes=1)        # too fresh
    _seed(uuid4(), "Case", "ALREADY", env=7, cleaned=True, age_minutes=60)
    # NULL env (a pre-D-230 finalize-written row) — seed with explicit NULL
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        conn.execute(text(
            "INSERT INTO s4_created_records (run_id, sobject, record_id, created_seq, "
            "cleaned, environment_id, created_at) VALUES "
            "(CAST(:r AS uuid), 'Case', 'NOENV', 0, false, NULL, NOW() - INTERVAL '60 minutes')"
        ), {"r": str(uuid4())})
    client = _FakeClient()
    n = reap_stranded_records(
        TEST_TENANT_ID, stale_minutes=15,
        client_resolver=lambda session, env_id: client)
    assert n == 0
    assert client.deleted == []
    # none flipped
    assert {r["record_id"]: r["cleaned"] for r in _rows()} == {
        "RECENT": False, "ALREADY": True, "NOENV": False}


def test_reaper_transient_delete_failure_leaves_uncleaned_for_retry():
    _seed(uuid4(), "Case", "FLAKY", env=7, age_minutes=60)
    client = _FakeClient(fail_on={"FLAKY"})
    n = reap_stranded_records(
        TEST_TENANT_ID, stale_minutes=15,
        client_resolver=lambda session, env_id: client)
    assert n == 0
    assert _rows()[0]["cleaned"] is False            # left for the next tick


def test_reaper_groups_clients_per_environment():
    _seed(uuid4(), "Case", "E7", env=7, age_minutes=60)
    _seed(uuid4(), "Lead", "E9", env=9, age_minutes=60)
    seen_envs = []

    def resolver(session, env_id):
        seen_envs.append(env_id)
        return _FakeClient()

    n = reap_stranded_records(TEST_TENANT_ID, stale_minutes=15, client_resolver=resolver)
    assert n == 2
    assert sorted(seen_envs) == [7, 9]               # one resolve per env
    assert all(r["cleaned"] for r in _rows())


def test_reaper_unresolvable_env_skips_without_raise():
    _seed(uuid4(), "Case", "NOCLIENT", env=7, age_minutes=60)

    def resolver(session, env_id):
        raise RuntimeError("no connection for env")

    n = reap_stranded_records(TEST_TENANT_ID, stale_minutes=15, client_resolver=resolver)
    assert n == 0
    assert _rows()[0]["cleaned"] is False


# ---------------------------------------------------------------------------
# Review #2 (blocker): the run-liveness interlock — never reap a live run
# ---------------------------------------------------------------------------

def _seed_job(env, status):
    # The conftest's autouse `clean_jobs` truncates s4_execution_jobs before each
    # test, so a seeded job here is the only one the interlock sees.
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        conn.execute(text(
            "INSERT INTO s4_execution_jobs (test_id, environment_id, status) "
            "VALUES (CAST(:t AS uuid), :env, :st)"
        ), {"t": str(uuid4()), "env": env, "st": status})


def test_reaper_skips_records_whose_env_has_an_active_job():
    # A live run holds a 'running' (or queued/claimed) job for its env — the
    # reaper must NOT delete that env's records mid-run (the false-15-min-safety
    # blocker). Even an old, otherwise-reapable row is held back.
    _seed(uuid4(), "Case", "LIVE", env=7, age_minutes=60)
    _seed_job(env=7, status="running")
    client = _FakeClient()
    n = reap_stranded_records(
        TEST_TENANT_ID, stale_minutes=15,
        client_resolver=lambda session, env_id: client)
    assert n == 0
    assert client.deleted == []
    assert _rows()[0]["cleaned"] is False


def test_reaper_reaps_once_the_env_job_is_terminal():
    # Same record, but the job reached a terminal status (the run is dead) → reap.
    _seed(uuid4(), "Case", "DEAD", env=7, age_minutes=60)
    _seed_job(env=7, status="failed")
    client = _FakeClient()
    n = reap_stranded_records(
        TEST_TENANT_ID, stale_minutes=15,
        client_resolver=lambda session, env_id: client)
    assert n == 1
    assert client.deleted == [("Case", "DEAD")]


def test_reaper_active_job_on_other_env_does_not_block():
    # An active job on env 9 must not hold back env 7's reapable records.
    _seed(uuid4(), "Case", "E7", env=7, age_minutes=60)
    _seed_job(env=9, status="running")
    client = _FakeClient()
    n = reap_stranded_records(
        TEST_TENANT_ID, stale_minutes=15,
        client_resolver=lambda session, env_id: client)
    assert n == 1
    assert client.deleted == [("Case", "E7")]


def test_reaper_gives_up_on_rows_past_the_7day_window():
    # Review #3/#4: a row older than the give-up window drops out of the working
    # set (so a poison row can't be retried forever nor block newer rows).
    _seed(uuid4(), "Case", "ANCIENT", env=7, age_minutes=8 * 24 * 60)   # 8 days
    client = _FakeClient()
    n = reap_stranded_records(
        TEST_TENANT_ID, stale_minutes=15,
        client_resolver=lambda session, env_id: client)
    assert n == 0
    assert client.deleted == []
    assert _rows()[0]["cleaned"] is False               # left, but not retried
