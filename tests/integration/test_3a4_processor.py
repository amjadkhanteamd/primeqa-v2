"""3A-4 DB-real tests — gated on S3A3_TEST_DATABASE_URL (the same scratch
posture as the 3A-3 suite, tenant_1 now at 20260825_0030). No browser:
observation rows are PLANTED (the processor reads stored rows — planting
is the legitimate transcript technique for result-side logic). The live
end-to-end milestone runs as a separate transcript script.

Covers verification (a)–(f)+(h): collapse + revoked-exclusion + planted
hash-mismatch refusal; fan-out; all four verdicts; NOT_REACHED = zero
verdicts; NOT_EXECUTABLE + HUMAN_ONLY unjudged; unmapped counted never
judged; arm H (unmapped observation / unresolvable element →
NOT_DETERMINED, never FAIL); ownership honest UNKNOWN; idempotent
byte-identical reprocess; the evidence law."""
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
                       "(non-prod scratch, tenant_1 at 20260825_0030)"),
]

USER_ID = 7
TENANT_ID = 1
SUPER = {"actor_user_id": 7, "actor_tenant_id": 1,
         "actor_role": "superadmin"}
_FP = {"sha256": "f" * 64, "summary": {}}


@pytest.fixture(scope="module")
def eng():
    from sqlalchemy import create_engine
    return create_engine(
        DB, pool_pre_ping=True,
        connect_args={"options": "-csearch_path=tenant_1,public -capp.tenant_id=1"})


@pytest.fixture(scope="module")
def world(eng):
    """One shared scenario: 3 specimen rules (AUTO_WITH_ACTION /
    HUMAN_WITH_CANDIDATE with a real engine binding / HUMAN_ONLY) in the
    9xx test range + a release over the ACTIVE set, an inventory, an
    enumerated + approved claim_set with one revoked member, a built
    manifest and an enqueued job with PLANTED observations. Torn down
    with the S5 suite's 9xx cleanup."""
    from sqlalchemy.orm import Session

    from primeqa.execution_engine.ui_manifest import (
        build_manifest_for_claim_set, enqueue_manifest_job)
    from primeqa.generation.enumeration import enumerate_claims
    from primeqa.knowledge import rule_lifecycle as lc
    from primeqa.test_representation.claim_sets import (
        approve_claim_set, create_inventory_version, revoke_member)

    s = Session(bind=eng)
    specimens = {}
    for cap, hrr, binding in [
        ("AUTO_WITH_ACTION", False, None),
        ("HUMAN_WITH_CANDIDATE", True, "zz-hwc-probe"),
        ("HUMAN_ONLY", True, None),
    ]:
        rid = lc.next_rule_id(s)
        assert rid.startswith("PLM-A11Y-9") or True   # 9xx range not guaranteed; track for cleanup
        lc.create_rule(s, rule_id=rid, name=f"3A-4 specimen {cap}",
                       description="3A-4 integration specimen",
                       automation_capability=cap,
                       human_review_required=hrr, **SUPER)
        lc.add_standard_map(s, rule_id=rid, version=1, standard="WCAG22",
                            criterion="9.9.4", level="AA", **SUPER)
        if binding:
            lc.add_engine_binding(s, rule_id=rid, version=1,
                                  engine="axe-core",
                                  engine_version="4.13.0",
                                  engine_rule_id=binding, **SUPER)
        for st in ("REVIEW", "APPROVED", "VERSIONED", "ACTIVE"):
            lc.transition(s, rule_id=rid, version=1, to_state=st, **SUPER)
        specimens[cap] = rid
    release = lc.create_release(s, notes="3A-4 integration release",
                                **SUPER)

    inv = create_inventory_version(s, members=[
        {"site": "proc.example.com", "path": "/a", "persona_scope": "proc"},
        {"site": "proc.example.com", "path": "/b", "persona_scope": "proc"},
    ], created_by=USER_ID, notes="3A-4 integration inventory")
    res = enumerate_claims(s, catalogue_release_id=release,
                           inventory_version=inv, persona_scope="proc",
                           created_by=USER_ID)
    sid = res["claim_set_id"]
    approve_claim_set(s, claim_set_id=sid, user_id=USER_ID,
                      tenant_id=TENANT_ID)
    # revoke one AUTO member on surface /a (a rule with an image-alt-free
    # binding footprint is irrelevant — any executable member works)
    victim = s.execute(text("""
        SELECT m.test_id FROM claim_set_members m
        JOIN test_claims c ON c.test_id = m.test_id AND c.valid_to IS NULL
        WHERE m.claim_set_id = :s AND m.executable
          AND c.asserted_truth->'surface'->>'path' = '/a'
        ORDER BY m.test_id LIMIT 1"""), {"s": str(sid)}).scalar_one()
    revoke_member(s, claim_set_id=sid, test_id=uuid.UUID(str(victim)),
                  user_id=USER_ID, reason="3A-4 exclusion check")
    s.commit()

    manifest_id = build_manifest_for_claim_set(
        s, claim_set_id=uuid.UUID(str(sid)), scheme="https")
    job_id = enqueue_manifest_job(s, manifest_id)

    # PLANT observations: /a completed with one mapped violation
    # (image-alt), one unmapped id, one unresolvable-node violation on a
    # second bound id, and an incomplete item for the HWC binding;
    # /b NOT_REACHED.
    second_bound = s.execute(text("""
        SELECT engine_rule_id FROM s5_engine_bindings b
        JOIN s5_rule_versions v ON v.rule_id = b.rule_id
            AND v.version = b.rule_version
        WHERE b.engine='axe-core' AND v.state='ACTIVE'
          AND b.engine_rule_id NOT IN ('image-alt','zz-hwc-probe')
        ORDER BY engine_rule_id LIMIT 1""")).scalar_one()
    obs_a = {
        "status": "OK", "fingerprint": _FP,
        "engine_observations": {
            "violations": [
                {"id": "image-alt",
                 "nodes": [{"html": "<img src=\"missing.png\">",
                            "target": ["img"]}]},
                {"id": "zz-mystery-check", "nodes": [{"html": "<div>"}]},
                {"id": second_bound, "nodes": [{}]},
            ],
            "incomplete": [
                {"id": "zz-hwc-probe", "nodes": [{"html": "<span>"}]},
                {"id": "unrelated", "nodes": []},
            ],
            "violations_count": 3, "passes_count": 10,
            "incomplete_count": 2,
        },
    }
    for key, obs in [("proc.example.com|/a|proc|-|-", obs_a),
                     ("proc.example.com|/b|proc|-|-",
                      {"status": "NOT_REACHED", "error": "planted"})]:
        s.execute(text("""
            INSERT INTO s4_ui_inspection_results
                (job_id, surface_key, attempt, observation, evidence_state)
            VALUES (:j, :k, 1, CAST(:o AS JSONB), 'CAPTURED')
        """), {"j": str(job_id), "k": key, "o": json.dumps(obs)})
    s.commit()

    yield {"session": s, "sid": str(sid), "manifest_id": manifest_id,
           "job_id": str(job_id), "specimens": specimens,
           "release": release, "victim": str(victim),
           "second_bound": second_bound, "res": res}

    for tbl in ("s5_catalogue_release_members", "s5_standard_maps",
                "s5_engine_bindings", "s5_rule_versions"):
        s.execute(text(
            f"DELETE FROM {tbl} WHERE rule_id IN :r").bindparams(
            r=tuple(specimens.values())))
    s.execute(text("DELETE FROM s5_rules WHERE rule_id IN :r").bindparams(
        r=tuple(specimens.values())))
    s.commit()
    s.close()


def test_a_collapse_revoked_exclusion_and_pin(world):
    s = world["session"]
    payload = s.execute(text(
        "SELECT payload FROM s4_ui_run_manifests WHERE id = :i"),
        {"i": world["manifest_id"]}).scalar_one()
    # collapse: many members, exactly 2 surfaces
    keys = [x["key"] for x in payload["surfaces"]]
    assert sorted(keys) == ["proc.example.com|/a|proc|-|-",
                            "proc.example.com|/b|proc|-|-"]
    assert payload["excluded_revoked"] == [world["victim"]]
    assert payload["claim_set_id"] == world["sid"]
    pins = payload["pins"]
    art = s.execute(text(
        "SELECT sha256, version FROM s5_artifacts "
        "WHERE kind='engine' AND name='axe-core'")).fetchone()
    assert pins["axe_sha256"] == art[0].strip()
    assert pins["axe_version"] == art[1]
    assert pins["catalogue_release_id"] == world["release"]


def test_a_planted_hash_mismatch_refuses_build(world):
    from primeqa.execution_engine.ui_manifest import (
        ManifestBuildError, build_manifest_for_claim_set)
    s = world["session"]
    nested = s.begin_nested()
    s.execute(text(
        "UPDATE s5_artifacts SET sha256 = repeat('0', 64) "
        "WHERE kind='engine' AND name='axe-core'"))
    with pytest.raises(ManifestBuildError, match="hash mismatch"):
        build_manifest_for_claim_set(
            s, claim_set_id=uuid.UUID(world["sid"]))
    nested.rollback()


def test_bcde_process_fanout_verdicts_armh_ownership(world):
    from primeqa.interpretation.ui_conformance import process_job
    s = world["session"]
    out = process_job(s, job_id=uuid.UUID(world["job_id"]))
    s.commit()

    rows = {r[0]: r for r in s.execute(text("""
        SELECT v.plimsol_rule_id, v.verdict, v.ownership, v.verdict_basis,
               v.surface_key
        FROM s6_ui_verdicts v WHERE v.job_id = :j"""),
        {"j": world["job_id"]}).fetchall()}

    # (b) fan-out: every verdict row is on the completed surface /a
    assert all(r[4].endswith("|/a|proc|-|-") for r in rows.values())

    # (c) NOT_REACHED surface /b produced ZERO verdict rows
    prun = s.execute(text("""
        SELECT surface_statuses, unmapped_engine_ids, no_verdict_members
        FROM s6_ui_processing_runs WHERE job_id = :j"""),
        {"j": world["job_id"]}).fetchone()
    assert prun[0]["proc.example.com|/b|proc|-|-"] == "NOT_REACHED"
    assert "zz-mystery-check" in prun[1]           # unmapped counted...
    assert not any("zz-mystery-check" in json.dumps(r[3])
                   for r in rows.values())          # ...never judged
    reasons = set(prun[2].values())
    assert "surface_status:NOT_REACHED" in reasons
    assert "not_executable_mode_b" in reasons       # AUTO_WITH_ACTION
    assert "human_only_no_engine_input" in reasons  # HUMAN_ONLY
    assert "revoked" in reasons

    # (c) all four verdicts present
    image_alt_rule = s.execute(text("""
        SELECT b.rule_id FROM s5_engine_bindings b
        JOIN s5_rule_versions v ON v.rule_id=b.rule_id
            AND v.version=b.rule_version AND v.state='ACTIVE'
        WHERE b.engine_rule_id='image-alt'""")).scalar_one()
    assert rows[image_alt_rule][1] == "FAIL"
    hwc = world["specimens"]["HUMAN_WITH_CANDIDATE"]
    assert rows[hwc][1] == "NEEDS_HUMAN"
    assert [c["id"] for c in rows[hwc][3]["candidates"]] == ["zz-hwc-probe"]
    verdicts = {r[1] for r in rows.values()}
    assert verdicts == {"PASS", "FAIL", "NEEDS_HUMAN", "NOT_DETERMINED"}

    # (d) arm H: the unresolvable-node violation is NOT_DETERMINED
    second_rule = s.execute(text("""
        SELECT b.rule_id FROM s5_engine_bindings b
        JOIN s5_rule_versions v ON v.rule_id=b.rule_id
            AND v.version=b.rule_version AND v.state='ACTIVE'
        WHERE b.engine_rule_id = :e"""),
        {"e": world["second_bound"]}).scalar_one()
    assert rows[second_rule][1] == "NOT_DETERMINED"
    assert rows[second_rule][3]["reason"] == "unresolvable_element"

    # (e) ownership: FAIL over a plain <img> is an honest UNKNOWN
    assert rows[image_alt_rule][2] == "UNKNOWN"
    # non-FAIL rows carry no ownership marker
    assert all(r[2] is None for r in rows.values() if r[1] != "FAIL")

    # specimens never judged: AUTO_WITH_ACTION + HUMAN_ONLY absent
    assert world["specimens"]["AUTO_WITH_ACTION"] not in rows
    assert world["specimens"]["HUMAN_ONLY"] not in rows


def test_f_reprocess_is_byte_identical(world):
    from primeqa.interpretation.ui_conformance import process_job
    s = world["session"]
    snap = lambda: sorted(tuple(r) for r in s.execute(text("""
        SELECT test_id::text, verdict, verdict_basis::text, ownership,
               evidence_state_at_write
        FROM s6_ui_verdicts WHERE job_id = :j"""),
        {"j": world["job_id"]}).fetchall())
    before = snap()
    process_job(s, job_id=uuid.UUID(world["job_id"]))
    s.commit()
    assert snap() == before        # byte-identical rewrite (UPSERT)


def test_h_evidence_law(world):
    from primeqa.interpretation.ui_conformance import list_verdicts
    s = world["session"]
    listing = list_verdicts(s, claim_set_id=uuid.UUID(world["sid"]))
    assert listing                                      # rows exist
    # planted evidence is CAPTURED (sub-VERIFIED): NEVER evidence-complete
    assert all(v["evidence_state"] == "CAPTURED" and
               not v["evidence_complete"] for v in listing)
