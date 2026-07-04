"""Integration: S3 generation-job queue + two-layer idempotency (D-106.4 slice 1).

Per-tenant (local-PG governance schema, via conftest's session db_setup). Model +
idempotency + attempt-lineage ONLY — no consumer, no run_generation, no endpoint
(slices 3-4). ``seeded["v1"]`` supplies an FK-valid ``s1_version_seq``
(logical_versions row); unique requirement_keys keep cases independent.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from primeqa.generation.jobs import GenerationJobStore
from primeqa.semantic.connection import get_tenant_connection

from .conftest import TEST_TENANT_ID


def _key() -> str:
    return f"REQ-{uuid4().hex[:8]}"


def _store() -> GenerationJobStore:
    return GenerationJobStore(TEST_TENANT_ID)


# ---------------------------------------------------------------------------
# Idempotency layer 1 — get-or-create on (requirement_key, s1_version_seq)
# ---------------------------------------------------------------------------

def test_create_or_get_job_is_idempotent(seeded):
    store = _store()
    key, seq = _key(), seeded["v1"]
    j1 = store.create_or_get_job(requirement_key=key, s1_version_seq=seq, created_by=1)
    j2 = store.create_or_get_job(requirement_key=key, s1_version_seq=seq, created_by=999)
    assert j1.id == j2.id                       # same key -> same job, no dup
    assert j1.status == "queued" and j1.attempt_count == 0
    assert j1.current_request_id is None
    assert j2.created_by == 1                    # ON CONFLICT DO NOTHING: first wins


def test_distinct_keys_are_distinct_jobs(seeded):
    store = _store()
    seq = seeded["v1"]
    a = store.create_or_get_job(requirement_key=_key(), s1_version_seq=seq)
    b = store.create_or_get_job(requirement_key=_key(), s1_version_seq=seq)
    assert a.id != b.id


def test_unique_constraint_is_a_real_db_constraint(seeded):
    # Layer 1 is a DB UNIQUE (not app-only, unlike v1's partial-unique workaround).
    key, seq = _key(), seeded["v1"]
    _store().create_or_get_job(requirement_key=key, s1_version_seq=seq)
    with pytest.raises(IntegrityError):
        with get_tenant_connection(TEST_TENANT_ID) as conn:
            conn.execute(text(
                "INSERT INTO s3_generation_jobs (requirement_key, s1_version_seq) "
                "VALUES (:rk, :sv)"), {"rk": key, "sv": seq})


# ---------------------------------------------------------------------------
# D-314 — force_rerun: the re-queue TRIGGER completing the migration's
# documented "retry in place via start_attempt" model. An explicit UI/API
# "Generate" click re-runs a TERMINAL job on the same row; an ACTIVE job is
# never disturbed (double-click safe); force_rerun=False is unchanged.
# ---------------------------------------------------------------------------

def _terminal_completed(store, key, seq, *, env=71, txt="orig"):
    """A completed job at (key, seq): create -> attempt -> complete. Leaves
    attempt_count=1, current_request_id + completed_at set."""
    jid = store.create_or_get_job(
        requirement_key=key, s1_version_seq=seq, environment_id=env,
        requirement_text=txt, created_by=1).id
    store.start_attempt(jid)
    store.complete(jid)
    return jid


def test_force_rerun_requeues_a_terminal_job_in_place(seeded):
    store = _store()
    key, seq = _key(), seeded["v1"]
    jid = _terminal_completed(store, key, seq)
    done = store.get_job(jid)
    assert done.status == "completed" and done.attempt_count == 1
    assert done.current_request_id is not None and done.completed_at is not None

    re = store.create_or_get_job(requirement_key=key, s1_version_seq=seq,
                                 force_rerun=True)
    assert re.id == jid                              # same row (retry in place)
    assert re.status == "queued"                     # re-queued for the consumer
    assert re.current_request_id is None             # run fields cleared
    assert re.completed_at is None
    assert re.error_code is None and re.error_message is None
    assert re.attempt_count == 1                     # lineage PRESERVED (not reset)
    # the next attempt bumps the retry lineage from where it left off
    a = store.start_attempt(jid)
    assert a.attempt_no == 2


def test_force_rerun_requeues_a_failed_job_and_clears_the_error(seeded):
    store = _store()
    key, seq = _key(), seeded["v1"]
    store.create_or_get_job(requirement_key=key, s1_version_seq=seq)
    jid = store.create_or_get_job(requirement_key=key, s1_version_seq=seq).id
    store.start_attempt(jid)
    store.fail(jid, error_code="llm_error", error_message="provider 503")
    assert store.get_job(jid).status == "failed"

    re = store.create_or_get_job(requirement_key=key, s1_version_seq=seq,
                                 force_rerun=True)
    assert re.id == jid and re.status == "queued"
    assert re.error_code is None and re.error_message is None


def test_force_rerun_false_leaves_a_terminal_job_untouched(seeded):
    # The default (auto/scheduled/repair paths): a finished job stays finished.
    store = _store()
    key, seq = _key(), seeded["v1"]
    jid = _terminal_completed(store, key, seq)
    same = store.create_or_get_job(requirement_key=key, s1_version_seq=seq)  # default False
    assert same.id == jid and same.status == "completed"     # DO NOTHING, unchanged
    assert same.completed_at is not None


def test_force_rerun_does_not_disturb_an_active_job(seeded):
    # Double-click during an in-flight run: the WHERE status IN (terminal) guard
    # makes the DO UPDATE a no-op; the running job + its attempt are untouched.
    store = _store()
    key, seq = _key(), seeded["v1"]
    store.create_or_get_job(requirement_key=key, s1_version_seq=seq)
    jid = store.create_or_get_job(requirement_key=key, s1_version_seq=seq).id
    a = store.start_attempt(jid)                     # -> running
    assert store.get_job(jid).status == "running"

    re = store.create_or_get_job(requirement_key=key, s1_version_seq=seq,
                                 force_rerun=True)
    assert re.id == jid
    assert re.status == "running"                    # NOT re-queued
    assert re.current_request_id == a.request_id     # in-flight attempt intact
    assert re.attempt_count == 1


def test_force_rerun_creates_a_fresh_job_when_none_exists(seeded):
    # force_rerun on a never-generated requirement is a plain create (no conflict).
    store = _store()
    key, seq = _key(), seeded["v1"]
    j = store.create_or_get_job(requirement_key=key, s1_version_seq=seq,
                                force_rerun=True)
    assert j.status == "queued" and j.attempt_count == 0


def test_force_rerun_refreshes_inputs_but_preserves_ownership(seeded):
    # A re-run uses THIS trigger's env/text (the requirement may have been edited,
    # or the user picked another env in the same org == same seq) — but created_by
    # is the job's OWNER (the cancel endpoint gates on it), so it is NOT
    # transferred to the re-triggering user (D-314 review fix #3).
    store = _store()
    key, seq = _key(), seeded["v1"]
    _terminal_completed(store, key, seq, env=71, txt="stale text")  # created_by=1
    re = store.create_or_get_job(
        requirement_key=key, s1_version_seq=seq, environment_id=72,
        requirement_text="fresh text", created_by=42, force_rerun=True)
    assert re.environment_id == 72                    # input refreshed
    assert re.requirement_text == "fresh text"        # input refreshed
    assert re.created_by == 1                          # ownership PRESERVED, not 42


# ---------------------------------------------------------------------------
# Idempotency layer 2 — fresh request_id per attempt (B-job)
# ---------------------------------------------------------------------------

def test_start_attempt_mints_fresh_request_id_no_collision(seeded):
    store = _store()
    job = store.create_or_get_job(requirement_key=_key(), s1_version_seq=seeded["v1"])
    a1 = store.start_attempt(job.id)
    a2 = store.start_attempt(job.id)             # retry in place — no PK collision
    assert (a1.attempt_no, a2.attempt_no) == (1, 2)
    assert a1.request_id != a2.request_id         # fresh UUID per attempt
    j = store.get_job(job.id)
    assert j.attempt_count == 2
    assert j.current_request_id == a2.request_id  # job points at the latest
    assert j.status == "running"
    # the job owns the per-attempt history (lineage is job-level, not the ledger)
    attempts = store.get_attempts(job.id)
    assert [r["attempt_no"] for r in attempts] == [1, 2]
    assert {r["request_id"] for r in attempts} == {a1.request_id, a2.request_id}


def test_start_attempt_unknown_job_raises(seeded):
    with pytest.raises(ValueError):
        _store().start_attempt(999_999)


# ---------------------------------------------------------------------------
# Status transitions + heartbeat
# ---------------------------------------------------------------------------

def test_lifecycle_complete_finalizes_attempt(seeded):
    store = _store()
    job = store.create_or_get_job(requirement_key=_key(), s1_version_seq=seeded["v1"])
    store.claim(job.id)
    assert store.get_job(job.id).status == "claimed"
    store.start_attempt(job.id)
    assert store.get_job(job.id).status == "running"
    store.complete(job.id)
    j = store.get_job(job.id)
    assert j.status == "completed" and j.completed_at is not None
    assert store.get_attempts(job.id)[-1]["status"] == "completed"


def test_lifecycle_fail_records_classified_error(seeded):
    store = _store()
    job = store.create_or_get_job(requirement_key=_key(), s1_version_seq=seeded["v1"])
    store.start_attempt(job.id)
    store.fail(job.id, error_code="llm_error", error_message="provider 503")
    j = store.get_job(job.id)
    assert j.status == "failed" and j.error_code == "llm_error"
    att = store.get_attempts(job.id)[-1]
    assert att["status"] == "failed" and att["error_code"] == "llm_error"
    assert att["finished_at"] is not None


def test_heartbeat_sets_timestamp(seeded):
    store = _store()
    job = store.create_or_get_job(requirement_key=_key(), s1_version_seq=seeded["v1"])
    store.start_attempt(job.id)
    assert store.get_job(job.id).heartbeat_at is None
    store.heartbeat(job.id)
    assert store.get_job(job.id).heartbeat_at is not None
