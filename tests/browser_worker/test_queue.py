"""Queue tests for ui-s2.3 — env-gated like the smoke test.

Enable with SPIKE_DATABASE_URL pointing at a NON-production database whose
tenant_1 schema carries revision 20260823_0010 (2.3 queue + 2.4 manifests). Default test runs skip the
module entirely (and pytest.ini testpaths exclude this directory anyway).
The happy-path consume test additionally needs SPIKE_BROWSER=1 (real
chromium scan).
"""

import json
import os

import pytest

SPIKE_DB = os.environ.get("SPIKE_DATABASE_URL")
# DESTRUCTIVE: the `session` fixture DELETEs all queue rows on setup. This
# whole module additionally requires SPIKE_DB_TESTS_OK=1 so it cannot run
# (and cannot wipe a live spike queue) unless explicitly opted in. Live a-e
# sequences never set it — the wipe class is structurally impossible there.
_DB_TESTS_OK = os.environ.get("SPIKE_DB_TESTS_OK") == "1"

pytestmark = pytest.mark.skipif(
    not SPIKE_DB or not _DB_TESTS_OK,
    reason="destructive queue tests disabled (need SPIKE_DATABASE_URL and "
           "SPIKE_DB_TESTS_OK=1; they DELETE queue rows — never set during "
           "live sequences)",
)


@pytest.fixture()
def session():
    from sqlalchemy import text

    from primeqa.browser_worker.queue import open_tenant_session

    s = open_tenant_session(1, SPIKE_DB)
    # Dedicated spike DB: start each test from an empty queue.
    s.execute(text("DELETE FROM s4_ui_inspection_jobs"))
    s.commit()
    yield s
    s.close()


@pytest.fixture()
def manifest_id(session):
    # 2.4: jobs.manifest_id is NOT NULL — every enqueued job needs a
    # persisted manifest first (D-281 idiom).
    from primeqa.browser_worker.manifest import create_manifest
    return create_manifest(session, {
        "surfaces": [], "pins": {}, "stabilisation": {},
        "execution": {"mode": "manual-spike"},
    })


def _job_row(session, job_id):
    from sqlalchemy import text
    r = session.execute(text("""
        SELECT status, attempts, reaps, claimed_by, claimed_at, heartbeat_at
        FROM s4_ui_inspection_jobs WHERE id = :id
    """), {"id": job_id}).fetchone()
    return {"status": r[0], "attempts": r[1], "reaps": r[2],
            "claimed_by": r[3], "claimed_at": r[4], "heartbeat_at": r[5]}


@pytest.mark.skipif(os.environ.get("SPIKE_BROWSER") != "1",
                    reason="needs chromium (set SPIKE_BROWSER=1)")
def test_enqueue_consume_happy_path(session, manifest_id):
    from sqlalchemy import text

    from primeqa.browser_worker import queue as q
    from primeqa.browser_worker.consume import consume_job

    job_id = q.enqueue(session, {"surfaces": [
        {"key": "example-home", "url": "https://example.com"},
    ]}, manifest_id)
    job = q.claim_one(session)
    assert job is not None and job["job_id"] == job_id
    consume_job(session, job)

    assert _job_row(session, job_id)["status"] == "succeeded"
    rows = session.execute(text("""
        SELECT surface_key, attempt, observation->>'status'
        FROM s4_ui_inspection_results WHERE job_id = :id
    """), {"id": job_id}).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "example-home"
    assert rows[0][1] == 1
    assert rows[0][2] == "OK"


def test_reap_returns_stale_to_pending(session, manifest_id):
    from sqlalchemy import text

    from primeqa.browser_worker import queue as q

    job_id = q.enqueue(session, {"surfaces": []}, manifest_id)
    claimed = q.claim_one(session)
    assert claimed["attempts"] == 1

    # Simulate a dead worker: lease held, heartbeat 10 minutes stale.
    session.execute(text("""
        UPDATE s4_ui_inspection_jobs
        SET heartbeat_at = NOW() - INTERVAL '10 minutes' WHERE id = :id
    """), {"id": job_id})
    session.commit()

    assert q.reap_stalled(session) == 1
    row = _job_row(session, job_id)
    assert row["status"] == "pending"
    assert row["attempts"] == 1          # claim-only charging: reap adds nothing
    assert row["reaps"] == 1
    assert row["claimed_by"] is None
    assert row["claimed_at"] is None
    assert row["heartbeat_at"] is None


def test_finalize_upsert_idempotent(session, manifest_id):
    from sqlalchemy import text

    from primeqa.browser_worker import queue as q

    job_id = q.enqueue(session, {"surfaces": []}, manifest_id)
    q.finalize_surface(session, job_id, "s1", 1, {"status": "OK", "n": 1})
    q.finalize_surface(session, job_id, "s1", 2, {"status": "OK", "n": 2})

    rows = session.execute(text("""
        SELECT attempt, observation FROM s4_ui_inspection_results
        WHERE job_id = :id AND surface_key = 's1'
    """), {"id": job_id}).fetchall()
    assert len(rows) == 1                # one row, by constraint
    assert rows[0][0] == 2               # the rewrite won
    obs = rows[0][1] if isinstance(rows[0][1], dict) else json.loads(rows[0][1])
    assert obs["n"] == 2


def test_poison_cap_reaps_to_failed_permanent(session, manifest_id):
    from sqlalchemy import text

    from primeqa.browser_worker import queue as q

    job_id = q.enqueue(session, {"surfaces": []}, manifest_id)
    q.claim_one(session)
    # A poison batch on its 5th start whose worker just died.
    session.execute(text("""
        UPDATE s4_ui_inspection_jobs
        SET attempts = 5, heartbeat_at = NOW() - INTERVAL '10 minutes'
        WHERE id = :id
    """), {"id": job_id})
    session.commit()

    assert q.reap_stalled(session) == 1
    row = _job_row(session, job_id)
    assert row["status"] == "failed_permanent"   # never pending
    assert row["reaps"] == 1
