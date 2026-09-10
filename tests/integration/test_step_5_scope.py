"""Step 5 on SCRATCH (LLD_STEP_5_QUALITY_POLICY §g items 1, 6): the parts that
need the public tables the local harness lacks — the RELEASE scope through the
real release, its declared target, its executed plan and the composer's
record: a decision row carries policy + version + plan; the policy freezes
(first_used_at) in the same act; the human's final decision lands beside the
recommendation through the repository the web route calls; the preview reads
the ACTIVE map set's levels from the public tables. One rolled-back
transaction; the console's ``session=`` seam; the composer's grading step is
pointed at that session (its own transaction handling is a wrapper).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
TENANT, ENV = 1, 5901
pytestmark = [pytest.mark.skipif(not DB, reason="set S3A3_TEST_DATABASE_URL (scratch, tenant_1 at 20260912_0010, public at 073)")]


@pytest.fixture
def tx():
    engine = create_engine(DB); conn = engine.connect(); trans = conn.begin()
    conn.execute(text(f'SET search_path TO "tenant_{TENANT}", public'))
    conn.execute(text("SELECT set_config('app.tenant_id', '1', false)"))
    s = Session(bind=conn)
    try:
        yield s
    finally:
        s.close(); trans.rollback(); conn.close(); engine.dispose()


def _world(s):
    import sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
    from test_representation._fixtures import empty_conditions, make_value_claim
    from test_representation.test_substrate_decision_evidence import _gv
    from primeqa.evolution import persist_grounding_validity
    from primeqa.test_representation import SemanticTransactionCoordinator, identity
    coord = SemanticTransactionCoordinator()
    key = f"S5S-{uuid4().hex[:5]}"
    identity.establish(s, key, origin="jira", evidence={"t": 1}, established_by="test")
    sec = s.execute(text("INSERT INTO public.sections (tenant_id, name, created_by) VALUES (1, 'step5 scratch', 1) RETURNING id")).scalar()
    req = s.execute(text("INSERT INTO public.requirements (tenant_id, section_id, source, jira_key, jira_summary, created_by, external_key) "
                         "VALUES (1, :sec, 'jira', :k, 'step 5 scratch', 1, :k) RETURNING id"), {"sec": sec, "k": key}).scalar()
    rel = s.execute(text("INSERT INTO public.releases (tenant_id, name, version_tag, status, created_by) "
                         "VALUES (1, 'Step 5 scratch release', 'v-s5', 'in_progress', 1) RETURNING id")).scalar()
    s.execute(text("INSERT INTO public.release_requirements (release_id, requirement_id, added_by) VALUES (:r, :q, 1)"), {"r": rel, "q": req})
    org = s.execute(text("INSERT INTO connected_orgs (org_type, sf_instance_url, label, environment_id) "
                         "VALUES ('sandbox', 'https://s5s.example', 's5s-org', :e) RETURNING CAST(id AS text)"), {"e": ENV}).scalar()
    seq = s.execute(text("INSERT INTO logical_versions (version_name, version_type, connected_org_id) "
                         "VALUES (:n, 'sync_run', CAST(:o AS uuid)) RETURNING version_seq"), {"n": f"s5s-{uuid4().hex[:5]}", "o": org}).scalar()
    claims = []
    for outcome in ("passed", "passed", "failed"):
        cr = coord.write_claim(s, actor="s3", test_id=None, archetype="data_behavior", claim_kind="value-claim",
                               asserted_truth=make_value_claim(value=f"v-{uuid4().hex[:6]}"), semantic_conditions=empty_conditions())
        coord.link_requirement(s, actor="s3", test_id=cr.test_id, external_system="jira", external_key=key, link_kind="generated_from")
        coord.promote_claim_to_approved(s, actor="human", test_id=cr.test_id, version_seq=cr.version_seq)
        persist_grounding_validity(s, connected_org_id=org, test_id=cr.test_id, version_seq=cr.version_seq,
                                   evaluated_at_version_seq=seq, validity=_gv(overall="intact"))
        s.execute(text("INSERT INTO s4_execution_runs (run_id, recipe_id, recipe_version_seq, claim_test_id, claim_version_seq, environment_id, "
                       "outcome, started_at, finished_at, evidence, connected_org_id, org_version_seq) VALUES (CAST(:r AS uuid), CAST(:p AS uuid), 1, "
                       "CAST(:t AS uuid), :v, :e, CAST(:o AS run_outcome), now(), now(), '{}'::jsonb, CAST(:org AS uuid), :s)"),
                  {"r": str(uuid4()), "p": str(uuid4()), "t": str(cr.test_id), "v": cr.version_seq, "e": ENV, "o": outcome, "org": org, "s": seq})
        claims.append(str(cr.test_id))
    return {"key": key, "release": rel, "org": org, "seq": seq, "claims": claims}


def test_release_scope_records_policy_version_plan_and_the_final_decision_beside_it(tx, monkeypatch):
    from primeqa.execution_engine import planner as P
    from primeqa.intelligence import quality_policy as qp
    from primeqa.intelligence.quality_decision_console import (
        grade_release, preview_release_decision, record_waiver, waivers_for_release,
    )
    import primeqa.core.models  # noqa: F401 — the decision row FKs users; the ORM needs the table registered
    from primeqa.release import decision_composer as dc
    from primeqa.release.repository import ReleaseRepository
    s = tx; w = _world(s)
    seed = next(p for p in qp.list_policies(s) if p.name == "Plimsol default" and p.version == 1)
    assert seed.status == "draft" and seed.first_used_at is None            # the migration's seed, a DRAFT
    # 1. no active policy → the preview refuses by sentence; nothing grades
    pv0 = preview_release_decision(TENANT, w["release"], [w["key"]], session=s)
    assert pv0["available"] and not pv0["ok"] and pv0["reason"] == "no_active_policy"
    qp.activate(s, policy_id=seed.id, user_id=1, tenant_id=TENANT)
    acts = [r[0] for r in s.execute(text("SELECT action FROM public.activity_log WHERE tenant_id=1 AND action LIKE 'quality_policy.%' ORDER BY id DESC LIMIT 1")).fetchall()]
    assert acts == ["quality_policy.activate"]
    # 2. a declared target + an executed plan for the release (ruling 6)
    P.declare_target(s, tenant_id=TENANT, release_id=w["release"], environment_id=ENV, actor_user_id=1)
    plan = P.plan(s, tenant_id=TENANT, scope={"scope_kind": "release", "release_id": w["release"]}, planned_by=1)
    assert plan["inputs"]["targets_source"] == "declared"
    P.execute_plan(s, tenant_id=TENANT, plan_id=plan["id"], executed_by=1,
                   enqueue=lambda **kw: type("J", (), {"id": 1, "plan_id": kw.get("plan_id")})())
    # 3. the preview over the real public tables: the failure blocks; levels read from the ACTIVE map set
    pv = preview_release_decision(TENANT, w["release"], [w["key"]], session=s)
    assert pv["ok"], pv
    d = pv["decision"]
    assert d["recommendation"] == "no_go" and d["plan_id"] == plan["id"]
    assert pv["evidence"]["conformance"]["levels_available"] is True
    assert pv["evidence"]["environments"]["targets_source"] == "declared"
    f = next(ln for ln in d["evidence_lines"] if ln["axis"] == "functional")
    assert f["effect"] == "BLOCK" and {r["position"] for r in f["rules"]} == {1, 10} and "pass rate 66.7%" in f["observed"]
    # 4. the composer records ONE row with policy + version + plan; the policy freezes in the same act
    monkeypatch.setattr(dc, "_grade_under_policy",
                        lambda tenant_id, release_id, keys: (lambda out: (qp.mark_used(s, policy_id=out["decision"]["policy"]["id"]), out)[1])(
                            grade_release(s, tenant_id=tenant_id, release_id=release_id, keys=keys)))
    monkeypatch.setattr("primeqa.intelligence.substrate_decision.get_release_substrate_decision",
                        lambda tenant_id, keys, criteria=None: {"available": False, "applicable": False, "claim_count": 0})
    repo = ReleaseRepository(s)
    release = repo.get_release(w["release"], TENANT)
    env = dc.evaluate_and_record(s, release, TENANT, release_repo=repo)
    assert env["recommendation"] == "no_go" and env["recommendation_source"] == "policy"
    row = s.execute(text("SELECT recommendation, CAST(policy_id AS text), policy_version, CAST(plan_id AS text) FROM public.release_decisions "
                         "WHERE release_id = :r ORDER BY id DESC LIMIT 1"), {"r": w["release"]}).first()
    assert row == ("no_go", seed.id, 1, plan["id"])
    assert qp.get_policy(s, seed.id).first_used_at is not None
    with pytest.raises(Exception):                                  # a used version refuses edit
        with s.begin_nested():
            s.execute(text("UPDATE quality_policy_rules SET effect = 'ALLOW' WHERE policy_id = CAST(:p AS uuid) AND position = 1"), {"p": seed.id})
    # 5. a waiver on the failed claim flips it → GO on the next preview; it names the reviewer
    bad = w["claims"][2]
    wv = record_waiver(TENANT, release_id=w["release"], item_kind="claim", item_ref=bad, axis="functional", reviewer_user_id=1,
                       reason="accepted for this release", expires_at=datetime.now(timezone.utc) + timedelta(days=2), user_id=1, session=s)
    assert wv["ok"]
    pv2 = preview_release_decision(TENANT, w["release"], [w["key"]], session=s)
    assert pv2["decision"]["recommendation"] == "go"
    assert waivers_for_release(TENANT, w["release"], session=s)["waivers"][0]["reviewer_name"]
    # 6. the human's final decision lands BESIDE the recommendation (the repository the web route calls)
    dec = repo.get_latest_decision(w["release"])
    fin = repo.finalize_decision(dec.id, w["release"], "go", 1, "AK: the failing check is waived; shipping")
    assert fin.final_decision == "go" and fin.recommendation == "no_go" and fin.decided_by == 1
    latest = s.execute(text("SELECT recommendation, final_decision, override_reason FROM public.release_decisions WHERE id = :i"), {"i": dec.id}).first()
    assert latest == ("no_go", "go", "AK: the failing check is waived; shipping")
