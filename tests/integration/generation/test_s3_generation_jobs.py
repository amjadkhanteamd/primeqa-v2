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
