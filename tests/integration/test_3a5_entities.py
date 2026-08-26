"""3A-5 DB-real tests — gated on S3A3_TEST_DATABASE_URL (scratch,
tenant_1 at 20260825_0040). Covers verification (b)/(c)/(e): Surface
materialization + entity reuse + the canonicalizer pin; bundle-sync
version semantics through the REAL phase function with a fake client
(same-source no-op, one-line edit → one new version, removed bundle →
SCD-2 close); the DE-11 CONFIRMED join through process_job."""
from __future__ import annotations

import json
import os
import uuid

import pytest
from sqlalchemy import text

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DB, reason="set S3A3_TEST_DATABASE_URL "
                       "(non-prod scratch, tenant_1 at 20260825_0040)"),
]

USER_ID = 7


@pytest.fixture()
def session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    eng = create_engine(DB, pool_pre_ping=True, connect_args={
        "options": "-csearch_path=tenant_1,public -capp.tenant_id=1"})
    s = Session(bind=eng)
    yield s
    s.rollback()
    s.close()


def test_b_surface_materialization_reuse_and_ref_fill(session):
    from primeqa.test_representation.claim_sets import (
        create_inventory_version)

    suffix = uuid.uuid4().hex[:8]
    member = {"site": f"m-{suffix}.example.com", "path": "/one",
              "persona_scope": "matz", "display_name": "One"}
    v1 = create_inventory_version(session, members=[member],
                                  created_by=USER_ID)
    row1 = session.execute(text("""
        SELECT m.surface_entity_ref, e.entity_type, e.entity_origin,
               e.sf_api_name
        FROM ui_surface_inventory_members m
        JOIN entities e ON e.id = m.surface_entity_ref
        WHERE m.inventory_version = :v"""), {"v": v1}).fetchone()
    assert row1 is not None                       # ref filled at declaration
    assert row1[1] == "Surface"
    assert row1[2] == "manual_curation"           # declared, never 'sync'

    # re-declaration in a LATER version REUSES the entity
    v2 = create_inventory_version(session, members=[member],
                                  created_by=USER_ID)
    ref2 = session.execute(text("""
        SELECT surface_entity_ref FROM ui_surface_inventory_members
        WHERE inventory_version = :v"""), {"v": v2}).scalar_one()
    assert str(ref2) == str(row1[0])
    n = session.execute(text("""
        SELECT COUNT(*) FROM entities
        WHERE entity_type='Surface' AND sf_api_name = :k
          AND valid_to_seq IS NULL"""), {"k": row1[3]}).scalar_one()
    assert n == 1


class _FakeClient:
    def __init__(self, bundles):
        self._bundles = bundles

    def fetch_lwc_bundles(self):
        return json.loads(json.dumps(self._bundles))   # deep copy


def _bundle(source, dev="probeWidget", sfid="0Rb0000000000AA"):
    return {"Id": sfid, "DeveloperName": dev, "NamespacePrefix": None,
            "ApiVersion": 63.0, "Description": "3a5 probe",
            "_resources": [{"FilePath": f"lwc/{dev}/{dev}.js",
                            "Format": "js", "Source": source}]}


@pytest.fixture()
def sync_world(session):
    """A minimal REAL sync scaffold on scratch: connected_org +
    logical_versions rows and a SyncContext factory."""
    from primeqa.sync.context import SyncContext

    org_id = str(uuid.uuid4())
    session.execute(text("""
        INSERT INTO connected_orgs (id, org_type, sf_instance_url, label)
        VALUES (:i, 'sandbox', 'https://scratch.example.com', '3a5-probe')
    """), {"i": org_id})

    def ctx(client):
        seq = session.execute(text("""
            INSERT INTO logical_versions (version_name, version_type,
                                          description, connected_org_id)
            VALUES (:n, 'manual_checkpoint', '3a5 test', :o)
            RETURNING version_seq
        """), {"n": f"3a5-{uuid.uuid4().hex[:8]}", "o": org_id}).scalar_one()
        return SyncContext(
            sf_client=client, engine=None,
            sync_run_id=str(uuid.uuid4()), connected_org_id=org_id,
            tenant_schema="tenant_1", logical_version_seq=seq)

    yield {"ctx": ctx, "org_id": org_id, "session": session}


def _versions(session, dev):
    return session.execute(text("""
        SELECT valid_from_seq, valid_to_seq FROM entities
        WHERE entity_type='LightningComponentBundle' AND sf_api_name=:d
        ORDER BY valid_from_seq"""), {"d": dev}).fetchall()


def test_c_bundle_sync_version_semantics(sync_world):
    from primeqa.sync.phases import phase_lightning_component_bundle

    session = sync_world["session"]
    conn = session.connection()
    dev = f"probe{uuid.uuid4().hex[:6]}"

    # initial sync → one current version
    r1 = phase_lightning_component_bundle(
        sync_world["ctx"](_FakeClient([_bundle("let a = 1;\n", dev)])), conn)
    assert r1.entity_type == "LightningComponentBundle"
    assert len(_versions(session, dev)) == 1

    # same-source resync (CRLF noise) → ZERO new versions
    phase_lightning_component_bundle(
        sync_world["ctx"](_FakeClient([_bundle("let a = 1;\r\n", dev)])),
        conn)
    assert len(_versions(session, dev)) == 1

    # one-line edit → exactly ONE new version (prior SCD-2-closed)
    phase_lightning_component_bundle(
        sync_world["ctx"](_FakeClient([_bundle("let a = 2;\n", dev)])), conn)
    vs = _versions(session, dev)
    assert len(vs) == 2
    assert vs[0][1] is not None and vs[1][1] is None

    # removed bundle (another bundle still present so the reconcile's
    # empty-set fail-safe does not trigger) → SCD-2 close
    other = _bundle("x\n", dev=f"other{uuid.uuid4().hex[:6]}",
                    sfid="0Rb0000000000BB")
    phase_lightning_component_bundle(
        sync_world["ctx"](_FakeClient([other])), conn)
    vs = _versions(session, dev)
    assert all(v[1] is not None for v in vs)      # every version closed


def test_e_confirmed_join_through_the_processor(sync_world):
    """A planted c-* FAIL + a synced bundle → CONFIRMED with
    owner_bundle_ref; an unresolvable c-* tag → PROBABLE (the corrected
    rule); non-c markers unchanged (UNKNOWN)."""
    from primeqa.generation.enumeration import enumerate_claims
    from primeqa.interpretation.ui_conformance import (
        list_verdicts, process_job)
    from primeqa.sync.phases import phase_lightning_component_bundle
    from primeqa.test_representation.claim_sets import (
        approve_claim_set, create_inventory_version)
    from primeqa.execution_engine.ui_manifest import (
        build_manifest_for_claim_set, enqueue_manifest_job)

    session = sync_world["session"]
    # sync the bundle the c-tag will resolve to. Unique per run: the
    # manifest helpers COMMIT (spike design), so scratch accumulates.
    suffix2 = uuid.uuid4().hex[:6]
    dev = "own" + suffix2.capitalize()          # tag c-own-<suffix2>
    phase_lightning_component_bundle(
        sync_world["ctx"](_FakeClient([_bundle("let a = 1;\n", dev)])),
        session.connection())
    bundle_id = session.execute(text("""
        SELECT id FROM entities
        WHERE entity_type='LightningComponentBundle'
          AND sf_api_name=:d AND valid_to_seq IS NULL
    """), {"d": dev}).scalar_one()

    suffix = uuid.uuid4().hex[:8]
    inv = create_inventory_version(session, members=[
        {"site": f"own-{suffix}.example.com", "path": "/p",
         "persona_scope": "own"}], created_by=USER_ID)
    res = enumerate_claims(session, catalogue_release_id=2,
                           inventory_version=inv, persona_scope="own",
                           created_by=USER_ID)
    approve_claim_set(session, claim_set_id=res["claim_set_id"],
                      user_id=USER_ID, tenant_id=1)
    mid = build_manifest_for_claim_set(
        session, claim_set_id=uuid.UUID(str(res["claim_set_id"])))
    job_id = enqueue_manifest_job(session, mid)

    # three FAILing rules on one surface: resolved c-*, unresolved c-*,
    # plain markup
    bound = session.execute(text("""
        SELECT b.engine_rule_id, b.rule_id FROM s5_engine_bindings b
        JOIN s5_rule_versions v ON v.rule_id=b.rule_id
            AND v.version=b.rule_version AND v.state='ACTIVE'
        WHERE b.engine='axe-core' ORDER BY b.engine_rule_id LIMIT 3
    """)).fetchall()
    key = f"own-{suffix}.example.com|/p|own|-|-"
    obs = {"status": "OK", "fingerprint": {"sha256": "f" * 64},
           "engine_observations": {"violations": [
               {"id": bound[0][0],
                "nodes": [{"html": f'<c-own-{suffix2} class="x">',
                           "target": [f"c-own-{suffix2}"]}]},
               {"id": bound[1][0],
                "nodes": [{"html": '<c-ghost-widget>',
                           "target": ["c-ghost-widget"]}]},
               {"id": bound[2][0],
                "nodes": [{"html": "<img src=x>", "target": ["img"]}]},
           ], "incomplete": [], "violations_count": 3,
              "passes_count": 1, "incomplete_count": 0}}
    session.execute(text("""
        INSERT INTO s4_ui_inspection_results
            (job_id, surface_key, attempt, observation, evidence_state)
        VALUES (:j, :k, 1, CAST(:o AS JSONB), 'CAPTURED')
    """), {"j": str(job_id), "k": key, "o": json.dumps(obs)})

    process_job(session, job_id=uuid.UUID(job_id))
    by_rule = {v["plimsol_rule_id"]: v for v in list_verdicts(
        session, claim_set_id=uuid.UUID(str(res["claim_set_id"])),
        verdict="FAIL")}
    resolved = by_rule[bound[0][1]]
    assert resolved["ownership"] == "CONFIRMED"
    assert resolved["owner_bundle_ref"] == str(bundle_id)
    ghost = by_rule[bound[1][1]]
    assert ghost["ownership"] == "PROBABLE"        # the corrected rule
    assert ghost["owner_bundle_ref"] is None
    plain = by_rule[bound[2][1]]
    assert plain["ownership"] == "UNKNOWN"
    assert plain["owner_bundle_ref"] is None
