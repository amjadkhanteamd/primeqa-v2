"""Integration: S3 enqueue (D-106.4 slice 4) — substrate side + the first full
vertical (enqueue -> consumer -> complete).

The v1 read (requirement) is route-side (unit-tested via _requirement_to_ref);
``enqueue_s3_generation`` is caller-fed — it takes a resolved ``{key, text}``.
Per-tenant governance DB; scripted LLM + stub api_key resolver.
"""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import text

from primeqa.generation.consumer import process_job_for_tenant
from primeqa.generation.intake import enqueue_s3_generation, resolve_current_s1_version
from primeqa.generation.jobs import GenerationJobStore
from primeqa.semantic.connection import get_tenant_connection

from .conftest import (
    FakeTurn, FakeToolTurn, TEST_ENV_ID, TEST_TENANT_ID, intent, propose_turn,
    query_outcome_rows, seed_org_id,
)

_STUB_KEY = lambda tenant_id, environment_id: "test-key-unused"


def _emit_draft_turn() -> FakeTurn:
    return FakeTurn([{"type": "tool_use", "id": f"tu_{uuid4().hex[:6]}",
                      "name": "emit_outcome",
                      "input": {"outcome_kind": "draft",
                                "payload": {"admissibility_layer": "layer_1"}}}])


def _seed_object_with_vr_at_new_version(object_name: str) -> int:
    """Seed a fresh logical_version + an Object + a ValidationRule APPLIES_TO it,
    so the tenant's CURRENT version (MAX(version_seq)) carries valid grounding for
    ``object_name``. Makes the enqueue-pins-current-version e2e order-independent
    (other suites insert later versions that close the shared seed fixture's
    entities). Returns the new version_seq."""
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        org = seed_org_id(conn)        # D-286: tag the new version + entities/edge
        v = conn.execute(text(
            "INSERT INTO logical_versions (version_name, version_type, connected_org_id) "
            "VALUES (:n, 'manual_checkpoint', CAST(:org AS uuid)) RETURNING version_seq"
        ), {"n": f"enq_{uuid4().hex[:8]}", "org": org}).scalar()
        ins = ("INSERT INTO entities (entity_type, sf_id, sf_api_name, display_name, "
               "attributes, connected_org_id, valid_from_seq, valid_to_seq, last_synced_at) "
               "VALUES (:et,NULL,:api,:api,'{}'::jsonb,CAST(:org AS uuid),:vf,NULL,NOW()) "
               "RETURNING id")
        obj = conn.execute(text(ins),
                           {"et": "Object", "api": object_name, "vf": v, "org": org}).scalar()
        vr = conn.execute(text(ins),
                          {"et": "ValidationRule", "api": f"{object_name}.Rule",
                           "vf": v, "org": org}).scalar()
        conn.execute(text(
            "INSERT INTO edges (source_entity_id, target_entity_id, edge_type, "
            "edge_category, properties, connected_org_id, valid_from_seq, valid_to_seq) "
            "VALUES (CAST(:s AS uuid),CAST(:t AS uuid),'APPLIES_TO','BEHAVIOR','{}'::jsonb,"
            "CAST(:org AS uuid),:vf,NULL)"
        ), {"s": str(vr), "t": str(obj), "vf": v, "org": org})
        return int(v)


# ---------------------------------------------------------------------------
# enqueue_s3_generation — pins the resolved requirement + env + current version
# ---------------------------------------------------------------------------

def test_enqueue_pins_text_env_and_current_version(seeded):
    ref = {"key": f"REQ-{uuid4().hex[:8]}",
           "text": "Users must not save a Case without a reason."}
    job = enqueue_s3_generation(
        tenant_id=TEST_TENANT_ID, requirement_ref=ref,
        environment_id=TEST_ENV_ID, created_by=5)

    seq, name = resolve_current_s1_version(TEST_TENANT_ID, TEST_ENV_ID)
    assert job.status == "queued" and job.attempt_count == 0
    assert job.requirement_key == ref["key"]
    assert job.requirement_text == ref["text"]
    assert job.environment_id == TEST_ENV_ID
    assert job.s1_version_seq == seq and job.s1_version_name == name
    assert job.created_by == 5


def test_enqueue_is_idempotent_per_requirement_and_version(seeded):
    ref = {"key": f"REQ-{uuid4().hex[:8]}", "text": "x"}
    a = enqueue_s3_generation(tenant_id=TEST_TENANT_ID, requirement_ref=ref,
                              environment_id=TEST_ENV_ID, created_by=1)
    b = enqueue_s3_generation(tenant_id=TEST_TENANT_ID, requirement_ref=ref,
                              environment_id=TEST_ENV_ID, created_by=2)
    assert a.id == b.id                        # same (key, current s1_version) -> same job


# ---------------------------------------------------------------------------
# First full vertical — enqueue -> consumer tick -> complete + ledger persisted
# ---------------------------------------------------------------------------

def test_first_full_vertical_enqueue_to_complete(seeded):
    # Seed an object + VR at a fresh (now-current) version so enqueue pins a
    # version that grounds the scripted intent — order-independent.
    obj = f"EnqObj{uuid4().hex[:6]}"
    _seed_object_with_vr_at_new_version(obj)

    ref = {"key": f"REQ-{uuid4().hex[:8]}",
           "text": "Users must not save without a reason."}
    job = enqueue_s3_generation(
        tenant_id=TEST_TENANT_ID, requirement_ref=ref,
        environment_id=TEST_ENV_ID, created_by=1)
    assert job.status == "queued"

    # the object HAS a VR (APPLIES_TO) -> a caveated prohibition-negative draft.
    seam = FakeToolTurn([
        propose_turn(intent(claim_kind="prohibition-claim", polarity="negative",
                            sf_api_name=obj)),
        _emit_draft_turn(),
    ])
    processed = process_job_for_tenant(
        TEST_TENANT_ID, api_key_resolver=_STUB_KEY, tool_turn_fn=seam)
    assert processed == job.id

    final = GenerationJobStore(TEST_TENANT_ID).get_job(job.id)
    assert final.status == "completed" and final.current_request_id is not None
    # the governed outcome is durable in the ledger — the first full vertical run
    assert len(query_outcome_rows()["outcomes"]) == 1
