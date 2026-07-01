"""Integration: S3 worker consumer (D-106.4 slice 3) — per-tenant, scripted LLM.

Drives ``process_job_for_tenant`` / ``run_s3_generation_tick`` against the
local-PG governance schema with an injected ``tool_turn_fn`` (no live gateway)
and a stub ``api_key_resolver`` (no Fernet connection). ``clean_ledger`` clears
``s3_generation_jobs`` between tests, so the claim-oldest semantics are
deterministic.
"""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import text

from primeqa.generation.consumer import (
    process_job_for_tenant, run_s3_generation_tick,
)
from primeqa.generation.jobs import GenerationJobStore
from primeqa.semantic.connection import get_engine, get_tenant_connection

from .conftest import (
    FakeTurn, FakeToolTurn, TEST_ENV_ID, TEST_TENANT_ID, intent, propose_turn,
    query_outcome_rows,
)

# A stub api_key resolver — the scripted tool_turn_fn means the key is unused
# (run_generation skips build_tool_turn_fn when a seam is injected).
_STUB_KEY = lambda tenant_id, environment_id: "test-key-unused"


def _emit_draft_turn() -> FakeTurn:
    return FakeTurn([{"type": "tool_use", "id": f"tu_{uuid4().hex[:6]}",
                      "name": "emit_outcome",
                      "input": {"outcome_kind": "draft",
                                "payload": {"admissibility_layer": "layer_1"}}}])


def _case_negative_seam() -> FakeToolTurn:
    # "Case" HAS a VR (APPLIES_TO) but its VR has no formula -> D-293 refuses it
    # behaviour-incomplete. Either way the job runs to COMPLETION with one durable
    # outcome row (a refusal is a valid governed outcome) — which is all these
    # consumer/tick tests assert.
    return FakeToolTurn([
        propose_turn(intent(claim_kind="prohibition-claim", polarity="negative",
                            sf_api_name="Case")),
        _emit_draft_turn(),
    ])


def _seed_job(*, requirement_text, s1_version_seq, environment_id=TEST_ENV_ID) -> int:
    """Create a queued job and pin the enqueue fields (slice 4 will do this from
    the endpoint; here we seed them directly)."""
    store = GenerationJobStore(TEST_TENANT_ID)
    job = store.create_or_get_job(
        requirement_key=f"REQ-{uuid4().hex[:8]}", s1_version_seq=s1_version_seq,
        created_by=1)
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        conn.execute(text(
            "UPDATE s3_generation_jobs SET requirement_text = :t, environment_id = :e "
            "WHERE id = :id"), {"t": requirement_text, "e": environment_id, "id": job.id})
    return job.id


# ---------------------------------------------------------------------------
# Happy path — claim -> run -> complete, ledger persisted, attempt finalized
# ---------------------------------------------------------------------------

def test_consumer_happy_path(seeded):
    jid = _seed_job(requirement_text="Users must not save a Case without a reason.",
                    s1_version_seq=seeded["v1"])
    processed = process_job_for_tenant(
        TEST_TENANT_ID, api_key_resolver=_STUB_KEY, tool_turn_fn=_case_negative_seam())
    assert processed == jid

    store = GenerationJobStore(TEST_TENANT_ID)
    job = store.get_job(jid)
    assert job.status == "completed" and job.completed_at is not None
    assert job.attempt_count == 1 and job.current_request_id is not None
    # the attempt is finalized; its request_id is the one run_generation used
    att = store.get_attempts(jid)[-1]
    assert att["status"] == "completed" and att["request_id"] == job.current_request_id
    # the governed outcome is durable in the ledger (keyed by that request_id)
    rows = query_outcome_rows()
    assert len(rows["outcomes"]) == 1


def test_consumer_empty_queue_returns_none(seeded):
    assert process_job_for_tenant(
        TEST_TENANT_ID, api_key_resolver=_STUB_KEY, tool_turn_fn=FakeToolTurn([])) is None


# ---------------------------------------------------------------------------
# Failure path — a run-flow error aborts the job to failed (D-106.3)
# ---------------------------------------------------------------------------

def test_consumer_failure_marks_failed_and_finalizes_attempt(seeded):
    jid = _seed_job(requirement_text="x", s1_version_seq=seeded["v1"])

    def _boom(tenant_id, environment_id):
        raise RuntimeError("cannot resolve api key")

    processed = process_job_for_tenant(
        TEST_TENANT_ID, api_key_resolver=_boom, tool_turn_fn=FakeToolTurn([]))
    assert processed == jid

    store = GenerationJobStore(TEST_TENANT_ID)
    job = store.get_job(jid)
    assert job.status == "failed" and job.error_code == "generation_error"
    # the attempt was still opened (start_attempt) and is finalized as failed
    att = store.get_attempts(jid)[-1]
    assert att["status"] == "failed" and att["finished_at"] is not None


# ---------------------------------------------------------------------------
# Claim concurrency — SELECT FOR UPDATE SKIP LOCKED never double-claims
# ---------------------------------------------------------------------------

def test_claim_skips_a_locked_row(seeded):
    store = GenerationJobStore(TEST_TENANT_ID)
    job = store.create_or_get_job(
        requirement_key=f"REQ-{uuid4().hex[:8]}", s1_version_seq=seeded["v1"])

    # Hold a row lock in a separate transaction (simulating worker A).
    conn1 = get_engine().connect()
    trans = conn1.begin()
    try:
        conn1.execute(text(f'SET LOCAL search_path TO "tenant_{TEST_TENANT_ID}", public'))
        locked = conn1.execute(text(
            "SELECT id FROM s3_generation_jobs WHERE id = :id FOR UPDATE SKIP LOCKED"
        ), {"id": job.id}).mappings().first()
        assert locked is not None                       # worker A holds the lock
        # worker B claims -> must SKIP the locked row -> nothing else queued -> None
        assert store.claim_next_queued_job() is None
    finally:
        trans.rollback()
        conn1.close()

    # lock released -> claimable again
    assert store.claim_next_queued_job().id == job.id


# ---------------------------------------------------------------------------
# Tenant-discovery resilience — one tenant's failure doesn't starve others
# ---------------------------------------------------------------------------

def test_tick_isolates_per_tenant_failure(seeded):
    jid = _seed_job(requirement_text="Users must not save a Case without a reason.",
                    s1_version_seq=seeded["v1"])
    # tenant 999999 has no schema -> its claim raises -> isolated; the good
    # tenant still runs to completion.
    outcomes = run_s3_generation_tick(
        [999999, TEST_TENANT_ID], api_key_resolver=_STUB_KEY,
        tool_turn_fn=_case_negative_seam())
    assert outcomes[999999].startswith("error")
    assert outcomes[TEST_TENANT_ID] == f"processed:{jid}"
    assert GenerationJobStore(TEST_TENANT_ID).get_job(jid).status == "completed"
