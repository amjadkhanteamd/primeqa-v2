"""Phase 7 DB-real acceptance — gated on S3A3_TEST_DATABASE_URL
(scratch, tenant_1 at 20260826_0010). Arms D/E/F + C at verdict level +
the cross-inventory refusal + idempotent re-compare, on planted
observation rows (the 3A-4/3A-5 transcript posture: the comparator
reads stored rows; planting is the controlled-input technique).

The world: one claim_set over release 2 (72 AUTO rules, 1 surface),
five jobs A..E with exactly ONE planted delta per arm:
  A baseline: all PASS, fingerprint FP1, snapshot S1, pins P.
  B (arm D):  STRUCTURAL page change (FP2) + the owning bundle gains a
              new S1 version in (A,B) + an image-alt FAIL on a c-* node
              resolving to it. Same snapshot, same pins.
  C (arm C):  page changes again (FP3), NOTHING else moves.
  D (arm E):  package version delta in the snapshot (S2), a new 'label'
              FAIL on a plain node. Same fingerprint as C, same pins.
  E (arm F):  axe pin delta (hand-crafted manifest pins), a new FAIL.
              Same snapshot, same fingerprint as D.
"""
from __future__ import annotations

import json
import uuid

import os

import pytest
from sqlalchemy import text

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DB, reason="set S3A3_TEST_DATABASE_URL "
                       "(non-prod scratch, tenant_1 at 20260826_0010)"),
]

USER_ID = 7
SUPER = {"actor_user_id": 7, "actor_tenant_id": 1,
         "actor_role": "superadmin"}


def _fp(sha, named):
    return {"sha256": sha * 64, "summary": {"element_count": 5,
                                            "named": named}}


@pytest.fixture(scope="module")
def world():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from primeqa.browser_worker import manifest as bw_manifest
    from primeqa.execution_engine.ui_manifest import (
        build_manifest_for_claim_set, enqueue_manifest_job)
    from primeqa.generation.enumeration import enumerate_claims
    from primeqa.interpretation.ui_conformance import process_job
    from primeqa.sync.context import SyncContext
    from primeqa.sync.phases import phase_lightning_component_bundle
    from primeqa.test_representation.claim_sets import (
        approve_claim_set, create_inventory_version)

    eng = create_engine(DB, pool_pre_ping=True, connect_args={
        "options": "-csearch_path=tenant_1,public -capp.tenant_id=1"})
    s = Session(bind=eng)
    sfx = uuid.uuid4().hex[:6]
    dev = "p" + sfx.capitalize()           # tag c-p-<sfx>
    tag = f"c-p-{sfx}"
    site = f"p7-{sfx}.example.com"
    key = f"{site}|/x|p7|-|-"

    # ---- S1 scaffold: org + bundle v1 ------------------------------
    org_id = str(uuid.uuid4())
    s.execute(text("""
        INSERT INTO connected_orgs (id, org_type, sf_instance_url, label)
        VALUES (:i, 'sandbox', 'https://p7.example.com', 'p7-probe')
    """), {"i": org_id})

    class _FakeClient:
        def __init__(self, source):
            self._source = source

        def fetch_lwc_bundles(self):
            return [{"Id": "0Rb00000000p7AA", "DeveloperName": dev,
                     "NamespacePrefix": None, "ApiVersion": 63.0,
                     "Description": "p7", "_resources": [
                         {"FilePath": f"lwc/{dev}/{dev}.js",
                          "Format": "js", "Source": self._source}]}]

    def ctx(client):
        seq = s.execute(text("""
            INSERT INTO logical_versions (version_name, version_type,
                                          description, connected_org_id)
            VALUES (:n, 'manual_checkpoint', 'p7', :o)
            RETURNING version_seq
        """), {"n": f"p7-{uuid.uuid4().hex[:8]}", "o": org_id}).scalar_one()
        return SyncContext(sf_client=client, engine=None,
                           sync_run_id=str(uuid.uuid4()),
                           connected_org_id=org_id,
                           tenant_schema="tenant_1",
                           logical_version_seq=seq)

    phase_lightning_component_bundle(
        ctx(_FakeClient("let a = 1;\n")), s.connection())
    s.commit()

    # ---- snapshots (planted) ---------------------------------------
    def snap(pkg_version):
        sid = str(uuid.uuid4())
        s.execute(text("""
            INSERT INTO org_environment_snapshots
                (id, platform_api_version, organization, packages,
                 content_hash)
            VALUES (:i, '63.0', '{}', CAST(:p AS JSONB), :h)
        """), {"i": sid, "p": json.dumps(
            [{"package_id": "0330p7", "version_id": pkg_version}]),
            "h": uuid.uuid4().hex + uuid.uuid4().hex})
        return sid

    snap1, snap2 = snap("04t-V1"), snap("04t-V2")

    # ---- claim_set --------------------------------------------------
    inv = create_inventory_version(s, members=[
        {"site": site, "path": "/x", "persona_scope": "p7"}],
        created_by=USER_ID)
    res = enumerate_claims(s, catalogue_release_id=2,
                           inventory_version=inv, persona_scope="p7",
                           created_by=USER_ID)
    approve_claim_set(s, claim_set_id=res["claim_set_id"],
                      user_id=USER_ID, tenant_id=1)
    s.commit()
    cs_id = uuid.UUID(str(res["claim_set_id"]))

    # engine ids for the planted FAILs
    label_rule = s.execute(text("""
        SELECT b.rule_id FROM s5_engine_bindings b
        JOIN s5_rule_versions v ON v.rule_id=b.rule_id
            AND v.version=b.rule_version AND v.state='ACTIVE'
        WHERE b.engine_rule_id='label'""")).scalar_one()
    imgalt_rule = s.execute(text("""
        SELECT b.rule_id FROM s5_engine_bindings b
        JOIN s5_rule_versions v ON v.rule_id=b.rule_id
            AND v.version=b.rule_version AND v.state='ACTIVE'
        WHERE b.engine_rule_id='image-alt'""")).scalar_one()
    third = s.execute(text("""
        SELECT b.engine_rule_id, b.rule_id FROM s5_engine_bindings b
        JOIN s5_rule_versions v ON v.rule_id=b.rule_id
            AND v.version=b.rule_version AND v.state='ACTIVE'
        WHERE b.engine_rule_id NOT IN ('label','image-alt')
        ORDER BY b.engine_rule_id LIMIT 1""")).fetchone()

    def make_job(snapshot_id, pin_override=None):
        mid = build_manifest_for_claim_set(
            s, claim_set_id=cs_id, org_env_snapshot_id=snapshot_id)
        if pin_override:
            # arm F: a hand-crafted manifest carrying the planted tool
            # pin (manifests are immutable; the comparator reads pins
            # from the payload — provenance is irrelevant to the test)
            payload = s.execute(text(
                "SELECT payload FROM s4_ui_run_manifests WHERE id=:i"),
                {"i": mid}).scalar_one()
            payload["pins"] = {**payload["pins"], **pin_override}
            mid = bw_manifest.create_manifest(s, payload)
        return mid, enqueue_manifest_job(s, mid)

    # Every bound engine id in the run, so a planted observation ATTESTS
    # the rules it did not fail. Without this the D-465 fix (correctly)
    # decides every non-violation as legacy_unattested -> NOT_DETERMINED,
    # and the comparator then reads every pair as NOT_COMPARABLE
    # (indeterminate_side) — which would test the verdict semantics
    # rather than the comparator this suite exists to test.
    all_engine_ids = sorted({r[0] for r in s.execute(text("""
        SELECT b.engine_rule_id FROM s5_engine_bindings b
        JOIN s5_rule_versions v ON v.rule_id=b.rule_id
          AND v.version=b.rule_version AND v.state='ACTIVE'
        WHERE b.engine='axe-core'""")).fetchall()})

    def plant(job_id, fingerprint, violations):
        failing = {v["id"] for v in violations}
        s.execute(text("""
            INSERT INTO s4_ui_inspection_results
                (job_id, surface_key, attempt, observation,
                 evidence_state)
            VALUES (:j, :k, 1, CAST(:o AS JSONB), 'CAPTURED')
        """), {"j": str(job_id), "k": key, "o": json.dumps({
            "status": "OK", "fingerprint": fingerprint,
            "engine_observations": {
                "violations": violations, "incomplete": [],
                "violations_count": len(violations), "passes_count": 9,
                "incomplete_count": 0,
                "run_set": all_engine_ids,
                "passes_ids": [e for e in all_engine_ids
                               if e not in failing],
                "inapplicable_ids": []}})})
        s.commit()

    jobs = {}
    fps = {"A": _fp("a", [["link", "Home"]]),
           "B": _fp("b", [["link", "Home"], ["button", "Buy"]]),
           "C": _fp("c", [["link", "Away"]]),
           "D": _fp("c", [["link", "Away"]]),
           "E": _fp("c", [["link", "Away"]])}

    # A — baseline, all PASS
    _, jobs["A"] = make_job(snap1)
    plant(jobs["A"], fps["A"], [])
    process_job(s, job_id=uuid.UUID(jobs["A"])); s.commit()

    # bundle v2 lands BETWEEN A and B (the arm-D window)
    phase_lightning_component_bundle(
        ctx(_FakeClient("let a = 2;\n")), s.connection())
    s.commit()

    # B — arm D: structural change + c-* FAIL resolving to the bundle
    _, jobs["B"] = make_job(snap1)
    plant(jobs["B"], fps["B"], [
        {"id": "image-alt",
         "nodes": [{"html": f'<{tag}><img src=x></{tag}>',
                    "target": [tag, "img"]}]}])
    process_job(s, job_id=uuid.UUID(jobs["B"])); s.commit()

    # C — arm C: page changes again, NOTHING else moves
    _, jobs["C"] = make_job(snap1)
    plant(jobs["C"], fps["C"], [
        {"id": "image-alt",
         "nodes": [{"html": f'<{tag}><img src=x></{tag}>',
                    "target": [tag, "img"]}]}])
    process_job(s, job_id=uuid.UUID(jobs["C"])); s.commit()

    # D — arm E: package delta; a new plain-node label FAIL
    _, jobs["D"] = make_job(snap2)
    plant(jobs["D"], fps["D"], [
        {"id": "image-alt",
         "nodes": [{"html": f'<{tag}><img src=x></{tag}>',
                    "target": [tag, "img"]}]},
        {"id": "label",
         "nodes": [{"html": "<input type=text>", "target": ["input"]}]}])
    process_job(s, job_id=uuid.UUID(jobs["D"])); s.commit()

    # E — arm F: tool pin delta; a third FAIL
    # sha-only override: axe_version stays 4.13.0 so the processor's
    # binding resolution (keyed on the pinned version) still resolves —
    # the moved TOOL dimension is the artifact hash.
    _, jobs["E"] = make_job(snap2,
                            pin_override={"axe_sha256": "f" * 64})
    plant(jobs["E"], fps["E"], [
        {"id": "image-alt",
         "nodes": [{"html": f'<{tag}><img src=x></{tag}>',
                    "target": [tag, "img"]}]},
        {"id": "label",
         "nodes": [{"html": "<input type=text>", "target": ["input"]}]},
        {"id": third[0], "nodes": [{"html": "<div x>", "target": ["div"]}]}])
    process_job(s, job_id=uuid.UUID(jobs["E"])); s.commit()

    yield {"s": s, "jobs": jobs, "cs_id": cs_id, "inv": inv,
           "dev": dev, "site": site, "imgalt_rule": imgalt_rule,
           "label_rule": label_rule, "third_rule": third[1],
           "snap1": snap1}
    s.close()


def _tr(s, cid, rule=None, transition=None):
    q = "SELECT transition, drift, causal, fingerprint_delta FROM s6_ui_verdict_transitions WHERE comparison_id=:c"
    p = {"c": str(cid)}
    if rule:
        q += " AND plimsol_rule_id=:r"; p["r"] = rule
    if transition:
        q += " AND transition=:t"; p["t"] = transition
    return s.execute(text(q), p).fetchall()


def test_arm_d_structural_client_change(world):
    from primeqa.interpretation.ui_comparison import compare_processing_runs
    s = world["s"]
    out = compare_processing_runs(
        s, baseline_job_id=uuid.UUID(world["jobs"]["A"]),
        candidate_job_id=uuid.UUID(world["jobs"]["B"]))
    s.commit()
    assert out["outcome"] == "completed"
    rows = _tr(s, out["comparison_id"], rule=world["imgalt_rule"])
    assert len(rows) == 1
    tr, drift, causal, fp = rows[0]
    assert tr == "NEW_FAIL" and drift is False
    # CLIENT primary, the bundle NAMED, fingerprint delta IN EVIDENCE
    assert causal["primary"] == "CLIENT_BUNDLE"
    assert causal["evidence"]["bundle"] == world["dev"]
    assert causal["evidence"]["source_hash_from"] is not None
    assert causal["fingerprint_delta"]["named_added"] == [["button", "Buy"]]
    assert causal["confidence"] == "HIGH"       # only the bundle moved
    # the amendment: the structural delta did NOT make it NOT_COMPARABLE
    assert out["transition_counts"].get("NOT_COMPARABLE", 0) == 0
    assert out["transition_counts"]["STILL_PASSING"] == 71


def test_arm_c_unexplained_change_is_not_comparable(world):
    from primeqa.interpretation.ui_comparison import compare_processing_runs
    s = world["s"]
    out = compare_processing_runs(
        s, baseline_job_id=uuid.UUID(world["jobs"]["B"]),
        candidate_job_id=uuid.UUID(world["jobs"]["C"]))
    s.commit()
    counts = out["transition_counts"]
    assert counts.get("NOT_COMPARABLE") == 72   # never a transition
    assert not any(k.startswith(("NEW_FAIL", "FIXED", "STILL"))
                   for k in counts)
    row = _tr(s, out["comparison_id"], rule=world["imgalt_rule"])[0]
    assert row[2]["reason"] == "state_changed_unexplained"
    assert row[3]["named_removed"] == [["button", "Buy"], ["link", "Home"]]
    assert row[3]["named_added"] == [["link", "Away"]]


def test_arm_e_package_delta_is_environment(world):
    from primeqa.interpretation.ui_comparison import compare_processing_runs
    s = world["s"]
    out = compare_processing_runs(
        s, baseline_job_id=uuid.UUID(world["jobs"]["C"]),
        candidate_job_id=uuid.UUID(world["jobs"]["D"]))
    s.commit()
    assert out["env_delta"]["packages"]["version_changed"][0][
        "package_id"] == "0330p7"
    rows = _tr(s, out["comparison_id"], rule=world["label_rule"])
    tr, drift, causal, _fp = rows[0]
    assert tr == "NEW_FAIL" and drift is False
    assert causal["primary"] == "ENVIRONMENT_PACKAGE"
    assert causal["confidence"] == "HIGH"


def test_arm_f_tool_delta_is_drift_not_regression(world):
    from primeqa.interpretation.ui_comparison import compare_processing_runs
    s = world["s"]
    out = compare_processing_runs(
        s, baseline_job_id=uuid.UUID(world["jobs"]["D"]),
        candidate_job_id=uuid.UUID(world["jobs"]["E"]))
    s.commit()
    assert "axe_sha256" in out["tool_drift"]
    rows = _tr(s, out["comparison_id"], rule=world["third_rule"])
    tr, drift, causal, _fp = rows[0]
    assert tr == "NEW_FAIL" and drift is True     # subtracted as DRIFT
    assert causal["primary"] == "TOOL"
    counts = out["transition_counts"]
    assert counts.get("NEW_FAIL_drift") == 1      # separate ledger
    assert counts.get("NEW_FAIL", 0) == 0         # regression headline: 0


def test_cross_inventory_refused_and_idempotent_recompare(world):
    from primeqa.generation.enumeration import enumerate_claims
    from primeqa.interpretation.ui_comparison import compare_processing_runs
    from primeqa.execution_engine.ui_manifest import (
        build_manifest_for_claim_set, enqueue_manifest_job)
    from primeqa.interpretation.ui_conformance import process_job
    from primeqa.test_representation.claim_sets import (
        approve_claim_set, create_inventory_version)
    s = world["s"]

    inv2 = create_inventory_version(s, members=[
        {"site": world["site"], "path": "/y", "persona_scope": "p7"}],
        created_by=USER_ID)
    res2 = enumerate_claims(s, catalogue_release_id=2,
                            inventory_version=inv2, persona_scope="p7",
                            created_by=USER_ID)
    approve_claim_set(s, claim_set_id=res2["claim_set_id"],
                      user_id=USER_ID, tenant_id=1)
    s.commit()
    mid = build_manifest_for_claim_set(
        s, claim_set_id=uuid.UUID(str(res2["claim_set_id"])),
        org_env_snapshot_id=world["snap1"])
    job2 = enqueue_manifest_job(s, mid)
    s.execute(text("""
        INSERT INTO s4_ui_inspection_results
            (job_id, surface_key, attempt, observation, evidence_state)
        VALUES (:j, :k, 1, CAST(:o AS JSONB), 'CAPTURED')
    """), {"j": str(job2), "k": f"{world['site']}|/y|p7|-|-",
           "o": json.dumps({"status": "OK",
                            "fingerprint": _fp("a", []),
                            "engine_observations": {
                                "violations": [], "incomplete": [],
                                "violations_count": 0,
                                "passes_count": 9,
                                "incomplete_count": 0,
                                "run_set": [], "passes_ids": [],
                                "inapplicable_ids": []}})})
    process_job(s, job_id=uuid.UUID(job2)); s.commit()

    out = compare_processing_runs(
        s, baseline_job_id=uuid.UUID(world["jobs"]["A"]),
        candidate_job_id=uuid.UUID(job2))
    s.commit()
    assert out["outcome"] == "refused"
    assert "cross-inventory" in out["refusal_reason"]
    assert "DECLARED act" in out["refusal_reason"]

    # idempotent re-compare of (A, B): byte-identical rows, same run row
    snap = lambda cid: sorted(tuple(r) for r in s.execute(text("""
        SELECT test_id::text, transition, from_verdict, to_verdict,
               drift, fingerprint_delta::text, causal::text
        FROM s6_ui_verdict_transitions WHERE comparison_id=:c"""),
        {"c": str(cid)}).fetchall())
    first = compare_processing_runs(
        s, baseline_job_id=uuid.UUID(world["jobs"]["A"]),
        candidate_job_id=uuid.UUID(world["jobs"]["B"]))
    before = snap(first["comparison_id"])
    again = compare_processing_runs(
        s, baseline_job_id=uuid.UUID(world["jobs"]["A"]),
        candidate_job_id=uuid.UUID(world["jobs"]["B"]))
    s.commit()
    assert again["comparison_id"] == first["comparison_id"]
    assert snap(again["comparison_id"]) == before
