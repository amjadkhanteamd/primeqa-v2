"""S3 generation console bridge — governance on the generation harness (D-165, UI 2a).

Exercises the v1→substrate read bridge the requirement detail uses:
  - `read_requirement_claims` / `_read_claims` over the real claim + recipe +
    generated_from link a draft run writes (key "R0" — see `_emit_run`),
  - `_read_latest_job` / `read_latest_s3_job` over a seeded s3_generation_jobs row,
  - the best-effort wrappers (a bad tenant returns available=False, never raises).

Reuses the draft-vertical setup so the claims under test are produced by the real
S3 pipeline, not hand-seeded. `clean_ledger` (autouse) truncates S2 + the job
queue between tests, so each starts clean.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from primeqa.generation.jobs import GenerationJobStore
from primeqa.generation.persistence import LedgerPersister
from primeqa.intelligence.s3_generation_console import (
    _read_claims,
    _read_latest_job,
    read_claim_detail,
    read_latest_s3_job,
    read_requirement_claims,
)

from .conftest import TEST_TENANT_ID
from .test_draft_vertical import _emit_run, _grounded_rel


def test_read_requirement_claims_returns_generated_plan(seeded):
    # a real S3 draft writes a claim + recipe + the generated_from link (key "R0")
    _, res = _emit_run(seeded, [_grounded_rel()], persister=LedgerPersister(TEST_TENANT_ID))
    test_id = str(res.results[0].outcome.claims_written[0].test_id)

    out = read_requirement_claims(TEST_TENANT_ID, "R0")
    assert out["available"] is True
    assert [c["test_id"] for c in out["claims"]] == [test_id]
    c = out["claims"][0]
    assert c["archetype"] == "configuration"
    assert c["claim_kind"] == "metadata-relationship-claim"
    assert c["recipe_count"] == 1
    assert c["recipes"][0]["recipe_kind"] == "metadata-recipe"


def test_read_claims_empty_for_unknown_key(seeded):
    _emit_run(seeded, [_grounded_rel()], persister=LedgerPersister(TEST_TENANT_ID))
    from primeqa.semantic.connection import get_tenant_connection
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        session = Session(bind=conn)
        try:
            assert _read_claims(session, "NO-SUCH-REQ") == []
        finally:
            session.close()


def test_read_latest_s3_job(seeded):
    job = GenerationJobStore(TEST_TENANT_ID).create_or_get_job(
        requirement_key="RX-9", s1_version_seq=seeded["v1"])
    from primeqa.semantic.connection import get_tenant_connection
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        j = _read_latest_job(conn, "RX-9")
    assert j is not None and j["id"] == job.id
    assert j["status"] == "queued" and j["active"] is True


def test_read_latest_s3_job_none_when_absent(seeded):
    out = read_latest_s3_job(TEST_TENANT_ID, "NO-JOB-KEY")
    assert out["available"] is True and out["job"] is None


def test_best_effort_bad_tenant():
    # tenant -1 has no schema -> get_tenant_connection fails -> available=False.
    assert read_requirement_claims(-1, "R0")["available"] is False
    assert read_latest_s3_job(-1, "R0")["available"] is False


# --- 2b: single-claim semantic detail ----------------------------------------

def test_read_claim_detail_returns_claim_and_recipes(seeded):
    _, res = _emit_run(seeded, [_grounded_rel()], persister=LedgerPersister(TEST_TENANT_ID))
    test_id = res.results[0].outcome.claims_written[0].test_id

    out = read_claim_detail(TEST_TENANT_ID, test_id)
    assert out["available"] is True and out["found"] is True
    c = out["claim"]
    assert c["test_id"] == str(test_id)
    assert c["archetype"] == "configuration"
    assert c["claim_kind"] == "metadata-relationship-claim"
    assert isinstance(c["asserted_truth"], dict) and c["asserted_truth"]   # body dumped
    assert isinstance(c["semantic_conditions"], dict)
    assert len(c["recipes"]) == 1
    r = c["recipes"][0]
    assert r["recipe_kind"] == "metadata-recipe" and r["trigger_kind"] == "inspection-trigger"
    assert isinstance(r["causal_initiation"], dict)


def test_read_claim_detail_not_found_for_unknown_id(seeded):
    import uuid
    out = read_claim_detail(TEST_TENANT_ID, uuid.uuid4())
    assert out["available"] is True and out["found"] is False and out["claim"] is None


def test_read_claim_detail_best_effort_bad_tenant():
    import uuid
    assert read_claim_detail(-1, uuid.uuid4())["available"] is False
