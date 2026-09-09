"""Step 4 on SCRATCH (LLD_STEP_4_RUN_PLANNER §g items 1, 3, 5, 7): the parts
that need the public tables the local harness lacks.

  - the release scope: DECLARED targets name the environments; none declared
    → the stated fallback (the active environments holding evidence); a
    declared-target change re-plans; env 78-shaped exclusions are named
    (one rolled-back transaction, the console's ``session=`` seam);
  - the manifest surface filter through the REAL builder over an approved
    scratch claim set (the manifest row it commits is removed after);
  - the schedule tick: a schedule without authority is REFUSED (recorded on
    the row, audited, no plan written); after "claim this schedule" it PLANS,
    executes THAT plan, the jobs carry the plan id. Committed rows are removed
    after (run_plans never deletes by design — scratch-only trigger bypass for
    FIXTURE removal, as Step 3's world removal).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
TENANT, ENV = 1, 5901
pytestmark = [pytest.mark.skipif(not DB, reason="set S3A3_TEST_DATABASE_URL (scratch, tenant_1 at 20260911_0010)")]


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


def _claim_with_data_recipe(s, coord, key):
    import sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
    from test_representation._builders import (
        build_minimal_data_mutation_trigger_body, build_minimal_execution_environment_body,
        empty_conditions, make_value_claim,
    )
    from test_representation.test_step_4_planner import _data_recipe_body
    cr = coord.write_claim(s, actor="s3", test_id=None, archetype="data_behavior", claim_kind="value-claim",
                           asserted_truth=make_value_claim(value=f"v-{uuid4().hex[:6]}"), semantic_conditions=empty_conditions())
    coord.link_requirement(s, actor="s3", test_id=cr.test_id, external_system="jira", external_key=key, link_kind="generated_from")
    coord.promote_claim_to_approved(s, actor="human", test_id=cr.test_id, version_seq=cr.version_seq)
    r = coord.write_recipe(s, actor="human", recipe_id=None, claim_test_id=cr.test_id, trigger_kind="data-mutation-trigger",
                           recipe_kind="data-recipe", causal_initiation=build_minimal_data_mutation_trigger_body(),
                           observation_realization=_data_recipe_body(),
                           execution_environment=build_minimal_execution_environment_body())
    coord.promote_recipe_to_approved(s, actor="human", recipe_id=r.recipe_id, version_seq=r.version_seq)
    return str(cr.test_id)


def test_release_scope_declared_targets_and_the_stated_fallback(tx):
    from primeqa.execution_engine import planner as P
    from primeqa.intelligence.run_plan_console import create_plan, declare_target, remove_target
    from primeqa.test_representation import SemanticTransactionCoordinator, identity
    s = tx; coord = SemanticTransactionCoordinator()
    key = f"S4S-{uuid4().hex[:5]}"
    identity.establish(s, key, origin="jira", evidence={"t": 1}, established_by="test")
    sec = s.execute(text("INSERT INTO public.sections (tenant_id, name, created_by) VALUES (1, 'step4 scratch', 1) RETURNING id")).scalar()
    req = s.execute(text("INSERT INTO public.requirements (tenant_id, section_id, source, jira_key, jira_summary, created_by, external_key) "
                         "VALUES (1, :sec, 'jira', :k, 'step 4 scratch', 1, :k) RETURNING id"), {"sec": sec, "k": key}).scalar()
    rel = s.execute(text("INSERT INTO public.releases (tenant_id, name, version_tag, status, created_by) "
                         "VALUES (1, 'Step 4 scratch release', 'v-s4', 'in_progress', 1) RETURNING id")).scalar()
    s.execute(text("INSERT INTO public.release_requirements (release_id, requirement_id, added_by) VALUES (:r, :q, 1)"), {"r": rel, "q": req})
    a = _claim_with_data_recipe(s, coord, key)
    # evidence on env 5901 (active) and on a phantom env 999999 (no environments row)
    for env in (ENV, 999999):
        s.execute(text("INSERT INTO s4_execution_runs (run_id, recipe_id, recipe_version_seq, claim_test_id, claim_version_seq, environment_id, "
                       "outcome, started_at, finished_at, duration_ms, evidence) VALUES (CAST(:r AS uuid), CAST(:p AS uuid), 1, CAST(:t AS uuid), 1, :e, 'passed', now(), now(), 1, '{}'::jsonb)"),
                  {"r": str(uuid4()), "p": str(uuid4()), "t": a, "e": env})
    # 1. no target declared → the stated fallback: the ACTIVE environments holding evidence
    r1 = create_plan(TENANT, scope={"scope_kind": "release", "release_id": rel}, user_id=1, user_role="admin", session=s)
    assert r1["ok"], r1
    p1 = r1["plan"]
    assert p1["inputs"]["targets_source"] == "fallback:evidence-active"
    assert [e["environment_id"] for e in p1["resolution"]["environments"]] == [ENV]
    assert {(x["kind"], str(x["ref"]), x["reason"]) for x in p1["exclusions"]["items"]} >= {("environment", "999999", P.X_ENV_NOT_TARGET)}
    assert {(pp["test_id"], pp["environment_id"]) for pp in p1["resolution"]["manifests"]["functional"]["pairs"]} == {(a, ENV)}
    # 2. declare a target → declared; env 5901 named; a re-plan reflects the change
    assert declare_target(TENANT, release_id=rel, environment_id=ENV, user_id=1, session=s)["created"]
    r2 = create_plan(TENANT, scope={"scope_kind": "release", "release_id": rel}, user_id=1, user_role="admin", session=s)
    p2 = r2["plan"]
    assert p2["inputs"]["targets_source"] == "declared" and [e["environment_id"] for e in p2["resolution"]["environments"]] == [ENV]
    assert p2["id"] != p1["id"]                                   # a re-plan is a new row
    # 3. remove the target → the next plan falls back again and says so
    assert remove_target(TENANT, release_id=rel, environment_id=ENV, user_id=1, reason="scratch", session=s)["removed"]
    p3 = create_plan(TENANT, scope={"scope_kind": "release", "release_id": rel}, user_id=1, user_role="admin", session=s)["plan"]
    assert p3["inputs"]["targets_source"] == "fallback:evidence-active"
    # the audit trail names the acts (public.activity_log exists on scratch)
    acts = [r[0] for r in s.execute(text("SELECT action FROM public.activity_log WHERE tenant_id=1 AND details->>'release_id' = :r ORDER BY id"), {"r": str(rel)}).fetchall()]
    assert acts == ["s4.release_target.declare", "s4.release_target.remove"]
    plans = [r[0] for r in s.execute(text("SELECT action FROM public.activity_log WHERE tenant_id=1 AND action='s4.plan.create' AND details->>'scope_ref' = :r"), {"r": str(rel)}).fetchall()]
    assert len(plans) == 3


def test_manifest_builder_filters_to_the_declared_surfaces():
    """Item 5 through the REAL builder over an approved scratch set with two
    surfaces: the manifest names ONE and records the other as excluded;
    membership stays by reference (claim_set_id), no new set, no approval."""
    from primeqa.execution_engine.ui_manifest import build_manifest_for_claim_set
    engine = create_engine(DB)
    with engine.connect() as c:
        c.execute(text("SET search_path TO tenant_1, public"))
        cands = c.execute(text("""
            SELECT CAST(cs.id AS text), cs.inventory_version FROM claim_sets cs
            WHERE cs.status='approved' AND (SELECT count(DISTINCT m.surface_key) FROM ui_surface_inventory_members m
                                           WHERE m.inventory_version=cs.inventory_version) >= 2
            ORDER BY cs.created_at LIMIT 6""")).all()
        c.commit()
    assert cands, "scratch holds no approved set with two surfaces"
    manifest_id = None
    for set_id, v in cands:
        with engine.connect() as c:
            c.execute(text("SET search_path TO tenant_1, public"))
            keys = [r[0] for r in c.execute(text("SELECT surface_key FROM ui_surface_inventory_members WHERE inventory_version=:v ORDER BY surface_key"), {"v": v}).all()]
            c.commit()
            try:
                with c.begin():                       # the connection's own transaction commits the manifest row
                    s = Session(bind=c)
                    manifest_id = build_manifest_for_claim_set(s, claim_set_id=__import__("uuid").UUID(set_id), surface_keys=[keys[0]])
                break
            except Exception as exc:  # noqa: BLE001 — a set whose caps are not on scratch: try the next
                manifest_id = None; last = exc
    assert manifest_id, f"no approved set built: {last}"
    with engine.connect() as c:
        c.execute(text("SET search_path TO tenant_1, public"))
        payload = c.execute(text("SELECT payload FROM s4_ui_run_manifests WHERE id = CAST(:m AS uuid)"), {"m": manifest_id}).scalar()
        try:
            assert payload["claim_set_id"] == set_id
            assert [x["key"] for x in payload["surfaces"]] == [keys[0]]
            assert payload["scope"] == {"surface_keys": [keys[0]], "excluded_surfaces": keys[1:]}
        finally:
            c.execute(text("DELETE FROM s4_ui_run_manifests WHERE id = CAST(:m AS uuid)"), {"m": manifest_id}); c.commit()
    engine.dispose()


def test_schedule_refuses_without_authority_then_claims_plans_and_executes():
    from primeqa.execution_engine.schedules import RunScheduleStore, fire_due_schedules
    store = RunScheduleStore(TENANT)
    engine = create_engine(DB)
    with engine.connect() as c:
        c.execute(text("SET search_path TO tenant_1, public"))
        sid = c.execute(text("INSERT INTO s4_run_schedules (environment_id, cron_expr, created_by) VALUES (:e, '0 6 * * *', NULL) RETURNING id"), {"e": ENV}).scalar()
        c.commit()
    plan_id = None
    try:
        now = datetime(2026, 9, 10, 6, 5, tzinfo=timezone.utc)      # past the 06:00 occurrence → due
        out = store.get(sid)
        assert out.authority is None
        r1 = fire_due_schedules(TENANT, production_env_ids=set(), now=now)
        assert sid not in r1["fired"] and any(x["id"] == sid for x in r1["refused"])
        row = store.get(sid)
        assert row.last_plan_id is None and "claim this schedule" in (row.last_refusal or "")
        with engine.connect() as c:
            c.execute(text("SET search_path TO tenant_1, public"))
            assert c.execute(text("SELECT count(*) FROM run_plans WHERE scope_kind='schedule' AND scope_ref=:s"), {"s": str(sid)}).scalar() == 0
            assert c.execute(text("SELECT count(*) FROM public.activity_log WHERE action='s4.schedule.refused' AND entity_id=:s"), {"s": sid}).scalar() >= 1
        # claim → the tick plans and executes THAT plan
        assert store.claim(sid, user_id=1)
        r2 = fire_due_schedules(TENANT, production_env_ids=set(), now=now)
        assert sid in r2["fired"] and len(r2["plans"]) == 1
        plan_id = r2["plans"][0]
        row = store.get(sid)
        assert row.last_plan_id == plan_id and row.last_refusal is None and row.authorised_by == 1
        with engine.connect() as c:
            c.execute(text("SET search_path TO tenant_1, public"))
            pr = c.execute(text("SELECT planned_by, authorised_by, executed_at IS NOT NULL, execution FROM run_plans WHERE id = CAST(:p AS uuid)"), {"p": plan_id}).one()
            assert pr[0] is None and pr[1] == 1 and pr[2] is True
            jobs = c.execute(text("SELECT count(*) FROM s4_execution_jobs WHERE plan_id = CAST(:p AS uuid)"), {"p": plan_id}).scalar()
            assert jobs == len(pr[3]["s4_jobs"]) and jobs == sum(1 for j in pr[3]["s4_jobs"] if not j["attached"])
    finally:
        with engine.connect() as c:
            with c.begin():
                c.execute(text("SET search_path TO tenant_1, public"))
                c.execute(text("SET LOCAL session_replication_role = replica"))   # scratch-only fixture removal
                if plan_id:
                    c.execute(text("DELETE FROM s4_execution_jobs WHERE plan_id = CAST(:p AS uuid)"), {"p": plan_id})
                    c.execute(text("DELETE FROM run_plans WHERE id = CAST(:p AS uuid)"), {"p": plan_id})
                c.execute(text("DELETE FROM s4_run_schedules WHERE id = :s"), {"s": sid})
                c.execute(text("DELETE FROM public.activity_log WHERE (action LIKE 's4.schedule.%' AND entity_id = :s) OR (action LIKE 's4.plan.%' AND details->>'plan_id' = :p)"), {"s": sid, "p": plan_id or ""})
        engine.dispose()
