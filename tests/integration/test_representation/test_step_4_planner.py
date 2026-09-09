"""Step 4 — the Run Planner (LLD_STEP_4_RUN_PLANNER §g), DB-real on the local
harness (per-test rollback). The tenant-only harness has no public tables,
so the environment reader is injected; the release scope (public.releases)
and the conformance manifest build (catalogue caps) run on scratch in
test_step_4_scope.py.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from primeqa.execution_engine import planner as P
from primeqa.generation.emission import _inspection_recipe
from primeqa.test_representation import (
    SemanticTransactionCoordinator, claim_sets, identity,
)
from primeqa.test_representation import surface_links as sl
from primeqa.test_representation.models.claims.ui.conformance_claim import ConformanceClaimBody
from primeqa.test_representation.models.conditions import SemanticConditionsBody
from primeqa.test_representation.models.surface import SurfaceNaturalKey, canonical_surface_key

from ._builders import (
    build_minimal_data_mutation_trigger_body,
    build_minimal_data_recipe_body, build_minimal_execution_environment_body,
    build_minimal_metadata_recipe_body, build_minimal_ui_recipe_body,
    build_minimal_ui_trigger_body, empty_conditions, make_value_claim,
)
from .test_substrate_decision_evidence import _seed_run

ENV_SANDBOX, ENV_PROD, ENV_RO, ENV_OFF = 59, 78, 60, 61
ENVS = {
    ENV_SANDBOX: P.EnvInfo(59, "Sandbox", True, False, "full"),
    ENV_PROD: P.EnvInfo(78, "Prod1", False, True, "read_only"),
    ENV_RO: P.EnvInfo(60, "Inspect-only", True, False, "read_only"),
    ENV_OFF: P.EnvInfo(61, "Disabled", True, False, "disabled"),
}


def _env(e):
    return ENVS.get(e)


def _claim(session, coord, key, *, approve=True, link_kind="generated_from"):
    cr = coord.write_claim(session, actor="s3", test_id=None, archetype="data_behavior",
                           claim_kind="value-claim", asserted_truth=make_value_claim(value=f"v-{uuid4().hex[:6]}"),
                           semantic_conditions=empty_conditions())
    coord.link_requirement(session, actor="s3", test_id=cr.test_id, external_system="jira",
                           external_key=key, link_kind=link_kind)
    if approve:
        coord.promote_claim_to_approved(session, actor="human", test_id=cr.test_id, version_seq=cr.version_seq)
    return str(cr.test_id)


def _data_recipe_body():
    """A data recipe that PASSES S4's shape check: Create → Read → Assert."""
    from primeqa.test_representation.models.primitives import AssertionPredicate
    from primeqa.test_representation.models.recipes.data_recipe import (
        AssertStep, CreateStep, DataRecipeBody, ReadStep,
    )
    from primeqa.test_representation.models.references import LogicalRef
    target = LogicalRef(entity_type="Object", external_id="Opportunity")
    return DataRecipeBody(api_choice="rest", identity_context="system", execution_mechanism="direct_api",
                          steps=[CreateStep(step_id="create-setup", target_object=target,
                                            field_values={"Opportunity.Loan_Amount__c": 6000000}),
                                 ReadStep(step_id="r", target=target, soql="SELECT Id FROM Opportunity",
                                          fields_to_capture=["Id"]),
                                 AssertStep(step_id="s", predicate=AssertionPredicate(
                                     subject_ref="r.Id", predicate="exists"))])


def _recipe(session, coord, tid, kind, *, surface=None):
    """A recipe of ``kind`` that PASSES S4's shape check (the bridge gates the
    trigger kind per recipe kind): data-recipe on a data-mutation trigger with a
    CreateStep, metadata-recipe as the inspection bundle, ui-inspection as
    enumeration writes it (inspection trigger + UiInspectionBody)."""
    if kind == "data-recipe":
        args = dict(trigger_kind="data-mutation-trigger",
                    causal_initiation=build_minimal_data_mutation_trigger_body(),
                    observation_realization=_data_recipe_body(),
                    execution_environment=build_minimal_execution_environment_body())
    elif kind == "metadata-recipe":
        trig, rec, env = _inspection_recipe(
            read_entity_type="Object", read_external_id="Lead", capture_field="APPLIES_TO",
            env_detail="read Lead metadata (Step 4 planner test)")
        args = dict(trigger_kind="inspection-trigger", causal_initiation=trig,
                    observation_realization=rec, execution_environment=env)
    else:
        from primeqa.test_representation.models.recipes.ui_inspection import UiInspectionBody
        from primeqa.test_representation.models.triggers import InspectionTriggerBody
        from primeqa.test_representation.models.environment import ExecutionEnvironmentBody
        args = dict(trigger_kind="inspection-trigger", causal_initiation=InspectionTriggerBody(),
                    observation_realization=UiInspectionBody(surface=surface),
                    execution_environment=ExecutionEnvironmentBody())
    r = coord.write_recipe(session, actor="human", recipe_id=None, claim_test_id=UUID(tid),
                           recipe_kind=kind, **args)
    coord.promote_recipe_to_approved(session, actor="human", recipe_id=r.recipe_id, version_seq=r.version_seq)
    return str(r.recipe_id)


@pytest.fixture
def world(session):
    coord = SemanticTransactionCoordinator()
    session.execute(text("SELECT set_config('app.tenant_id', '1', false)"))
    key_a, key_f = f"S4-A-{uuid4().hex[:5]}", f"fixture-s4-{uuid4().hex[:5]}"
    identity.establish(session, key_a, origin="manual", evidence={"t": 1}, established_by="test")
    identity.establish(session, key_f, origin="fixture", evidence={"t": 1}, established_by="test")
    a1 = _claim(session, coord, key_a)                       # approved, data + metadata recipes
    ra1d, ra1m = _recipe(session, coord, a1, "data-recipe"), _recipe(session, coord, a1, "metadata-recipe")
    a2 = _claim(session, coord, key_a)                       # approved, NO recipe
    a3 = _claim(session, coord, key_a, approve=False)        # draft
    d1 = _claim(session, coord, key_a)                       # approved then deprecated
    _recipe(session, coord, d1, "data-recipe")
    session.execute(text("UPDATE test_claims SET status = 'deprecated' WHERE test_id = CAST(:t AS uuid)"), {"t": d1})
    f1 = _claim(session, coord, key_f)                       # fixture-origin only
    _recipe(session, coord, f1, "data-recipe")
    # evidence on the production env for a1 (holds evidence, is not a target)
    _seed_run(session, claim_test_id=UUID(a1), claim_version_seq=1, outcome="passed",
              finished_at=datetime.now(timezone.utc), environment_id=ENV_PROD)
    return {"coord": coord, "key_a": key_a, "key_f": key_f, "a1": a1, "a2": a2, "a3": a3, "d1": d1,
            "f1": f1, "ra1d": ra1d, "ra1m": ra1m}


def _reasons(plan):
    return {(x["kind"], str(x["ref"]), x["reason"]) for x in plan["exclusions"]["items"]}


def _pairs(plan):
    return {(p["test_id"], p["environment_id"]) for p in plan["resolution"]["manifests"]["functional"]["pairs"]}


# 1 — the requirement scope: claims by kind with reasons; exclusions named ------

def test_requirement_scope_resolves_with_every_exclusion_named(session, world):
    w = world
    plan = P.plan(session, tenant_id=1, scope={"scope_kind": "requirement", "key": w["key_a"],
                                               "environment_id": ENV_SANDBOX},
                  planned_by=1, env_reader=_env, evidence_envs=lambda tids: [ENV_SANDBOX])
    assert plan["scope_kind"] == "requirement" and plan["scope_ref"] == w["key_a"]
    assert plan["planned_by"] == 1 and plan["executed_at"] is None
    res = plan["resolution"]
    assert res["claim_count"] == 1 and _pairs(plan) == {(w["a1"], ENV_SANDBOX)}, [(x["reason"], x.get("reasons"), x.get("detail")) for x in plan["exclusions"]["items"] if x["ref"] == w["a1"]]
    kinds = list(res["claims_by_kind"].values())
    assert kinds[0]["archetype"] == "data_behavior" and kinds[0]["claim_kind"] == "value-claim"
    c = kinds[0]["claims"][0]
    assert c["test_id"] == w["a1"] and c["keys"] == [w["key_a"]] and "generated_from" in c["link_kinds"]
    assert {r["recipe_kind"] for r in c["recipes"]} == {"data-recipe", "metadata-recipe"}
    rs = _reasons(plan)
    assert ("claim", w["a2"], P.X_NO_ELIGIBLE_RECIPE) in rs
    assert ("claim", w["a3"], P.X_CLAIM_NOT_APPROVED) in rs
    assert ("claim", w["d1"], P.X_CLAIM_DEPRECATED) in rs
    # env 78 holds evidence for a1 and is not the target — named with what it is
    prod = [x for x in plan["exclusions"]["items"] if x["kind"] == "environment" and x["ref"] == ENV_PROD]
    assert prod and prod[0]["reason"] == P.X_ENV_NOT_TARGET and prod[0]["is_production"] and \
        prod[0]["execution_policy"] == "read_only" and prod[0]["is_active"] is False
    assert plan["inputs"]["targets_source"] == "named"
    env = res["environments"][0]
    assert env["environment_id"] == ENV_SANDBOX and env["admitted"] and "pin" in env
    assert res["selection"]["impact"].startswith("not yet")
    assert plan["exclusions"]["counts"][P.X_ENV_NOT_TARGET] == 1


# 2 — hidden origins ---------------------------------------------------------

def test_fixture_origin_is_excluded_unless_asked(session, world):
    w = world
    p1 = P.plan(session, tenant_id=1, scope={"scope_kind": "requirement", "key": w["key_f"],
                                             "environment_id": ENV_SANDBOX}, planned_by=1, env_reader=_env)
    assert _pairs(p1) == set() and ("claim", w["f1"], P.X_ORIGIN_FIXTURE) in _reasons(p1)
    p2 = P.plan(session, tenant_id=1, scope={"scope_kind": "requirement", "key": w["key_f"],
                                             "environment_id": ENV_SANDBOX, "include_hidden": True},
                planned_by=1, env_reader=_env)
    assert _pairs(p2) == {(w["f1"], ENV_SANDBOX)}


# 3 — the target's policy as exclusions ---------------------------------------

def test_read_only_target_admits_inspection_recipes_only(session, world):
    w = world
    plan = P.plan(session, tenant_id=1, scope={"scope_kind": "requirement", "key": w["key_a"],
                                               "environment_id": ENV_RO}, planned_by=1, env_reader=_env)
    pairs = plan["resolution"]["manifests"]["functional"]["pairs"]
    assert [(p["test_id"], p["environment_id"]) for p in pairs] == [(w["a1"], ENV_RO)]
    assert pairs[0]["recipes"] == [w["ra1m"]]                    # the metadata recipe only
    assert ("recipe", w["ra1d"], P.X_READ_ONLY_TARGET) in _reasons(plan)
    off = P.plan(session, tenant_id=1, scope={"scope_kind": "requirement", "key": w["key_a"],
                                              "environment_id": ENV_OFF}, planned_by=1, env_reader=_env)
    assert _pairs(off) == set() and ("environment", str(ENV_OFF), P.X_ENV_DISABLED) in _reasons(off)
    inactive = P.plan(session, tenant_id=1, scope={"scope_kind": "requirement", "key": w["key_a"],
                                                   "environment_id": ENV_PROD}, planned_by=1, env_reader=_env)
    assert _pairs(inactive) == set() and ("environment", str(ENV_PROD), P.X_ENV_INACTIVE) in _reasons(inactive)


# 4 — "Run this plan" executes exactly the recorded row, once ------------------

class _Job:
    def __init__(self, jid, plan_id):
        self.id, self.plan_id = jid, plan_id


def test_execute_plan_enqueues_exactly_the_recorded_set_once(session, world):
    w = world
    plan = P.plan(session, tenant_id=1, scope={"scope_kind": "requirement", "key": w["key_a"],
                                               "environment_id": ENV_SANDBOX}, planned_by=1, env_reader=_env)
    calls = []

    def fake_enqueue(*, tenant_id, test_id, environment_id, created_by, plan_id):
        calls.append((tenant_id, test_id, environment_id, created_by, plan_id))
        return _Job(len(calls), plan_id)
    receipt = P.execute_plan(session, tenant_id=1, plan_id=plan["id"], executed_by=7, enqueue=fake_enqueue)
    assert {(t, e) for (_, t, e, _, _) in calls} == _pairs(plan)             # the enqueued set == the recorded set
    assert all(cb == 7 and pid == plan["id"] for (_, _, _, cb, pid) in calls)
    assert len(receipt["s4_jobs"]) == 1 and receipt["s4_jobs"][0]["attached"] is False
    row = P.get_plan(session, plan["id"])
    assert row["executed_by"] == 7 and row["executed_at"] and row["execution"]["s4_jobs"][0]["job_id"] == 1
    with pytest.raises(P.PlanRefused) as e:
        P.execute_plan(session, tenant_id=1, plan_id=plan["id"], executed_by=7, enqueue=fake_enqueue)
    assert e.value.reason == P.REFUSE_ALREADY_EXECUTED and len(calls) == 1
    # the row is immutable, never deleted, executed once
    for stmt in ("UPDATE run_plans SET resolution = '{}'::jsonb WHERE id = CAST(:i AS uuid)",
                 "UPDATE run_plans SET executed_by = 9 WHERE id = CAST(:i AS uuid)",
                 "DELETE FROM run_plans WHERE id = CAST(:i AS uuid)"):
        with pytest.raises(DBAPIError):
            with session.begin_nested():
                session.execute(text(stmt), {"i": plan["id"]})


# 5 — the tenant-wide plan (the successor of "Run all approved") ---------------

def test_tenant_scope_plans_every_approved_executable_claim_with_reasons(session, world):
    w = world
    plan = P.plan(session, tenant_id=1, scope={"scope_kind": "tenant", "environment_id": ENV_SANDBOX},
                  planned_by=1, env_reader=_env)
    pairs = _pairs(plan)
    assert (w["a1"], ENV_SANDBOX) in pairs and (w["f1"], ENV_SANDBOX) not in pairs
    rs = _reasons(plan)
    assert ("claim", w["f1"], P.X_ORIGIN_FIXTURE) in rs and ("claim", w["a2"], P.X_NO_ELIGIBLE_RECIPE) in rs
    assert plan["scope_kind"] == "tenant" and plan["scope_ref"] == str(ENV_SANDBOX)


# 6 — the schedule scope: no authority refuses; claim, then it plans ------------

def test_schedule_without_authority_refuses_then_claims_and_plans(session, world):
    sid = session.execute(text(
        "INSERT INTO s4_run_schedules (environment_id, cron_expr, created_by) VALUES (:e, '0 6 * * *', NULL) RETURNING id"),
        {"e": ENV_SANDBOX}).scalar()
    with pytest.raises(P.PlanRefused) as e:
        P.plan(session, tenant_id=1, scope={"scope_kind": "schedule", "schedule_id": sid}, env_reader=_env)
    assert e.value.reason == P.REFUSE_NO_AUTHORITY
    assert session.execute(text("SELECT count(*) FROM run_plans WHERE scope_kind='schedule' AND scope_ref=:s"),
                           {"s": str(sid)}).scalar() == 0                   # no row written on a refusal
    session.execute(text("UPDATE s4_run_schedules SET authorised_by = 5, authorised_at = now() WHERE id = :s"), {"s": sid})
    plan = P.plan(session, tenant_id=1, scope={"scope_kind": "schedule", "schedule_id": sid}, env_reader=_env)
    assert plan["planned_by"] is None and plan["authorised_by"] == 5
    assert plan["inputs"]["resolved_scope_kind"] == "tenant" and plan["inputs"]["schedule"]["template"]["legacy"] is True
    assert (world["a1"], ENV_SANDBOX) in _pairs(plan)
    row = {"plan_template": None, "environment_id": 59, "created_by": None, "authorised_by": None}
    assert P.schedule_authority(row) is None and P.schedule_template(row)["scope_kind"] == "tenant"
    assert P.schedule_authority({**row, "created_by": 3}) == 3 and P.schedule_authority({**row, "authorised_by": 4, "created_by": 3}) == 4


# 7 — release targets: declared with actor; removal a state change --------------

def test_release_targets_declare_and_remove_with_provenance(session):
    d = P.declare_target(session, tenant_id=None, release_id=16, environment_id=ENV_SANDBOX, actor_user_id=1)
    assert d["created"] and P.declare_target(session, tenant_id=None, release_id=16, environment_id=ENV_SANDBOX,
                                             actor_user_id=1)["created"] is False
    assert [t["environment_id"] for t in P.list_targets(session, 16)] == [ENV_SANDBOX]
    P.remove_target(session, tenant_id=None, release_id=16, environment_id=ENV_SANDBOX, actor_user_id=2, reason="moved")
    assert P.list_targets(session, 16) == []
    hist = P.list_targets(session, 16, include_inactive=True)
    assert hist[0]["active"] is False and hist[0]["deactivated_by"] == 2 and hist[0]["deactivation_reason"] == "moved"
    with pytest.raises(DBAPIError):
        with session.begin_nested():
            session.execute(text("DELETE FROM release_targets WHERE release_id = 16"))


# 8 — the conformance lane: the Step 3 declaration, consumed --------------------

def test_conformance_lane_is_filtered_to_the_declared_surfaces(session):
    coord = SemanticTransactionCoordinator()
    session.execute(text("SELECT set_config('app.tenant_id', '1', false)"))
    org = session.execute(text("INSERT INTO connected_orgs (org_type, sf_instance_url, label, environment_id) "
                               "VALUES ('sandbox', 'https://s4.example', 's4-org', 59) RETURNING CAST(id AS text)")).scalar()
    site = "s4.example.my.site.com"
    A, B = (SurfaceNaturalKey(site=site, path="/s", persona_scope="customer"),
            SurfaceNaturalKey(site=site, path="/s/topiccatalog", persona_scope="customer"))
    v = claim_sets.create_inventory_version(session, members=[
        {"site": site, "path": "/s", "persona_scope": "customer", "record_context_ref": None, "viewport": None, "display_name": "Home"},
        {"site": site, "path": "/s/topiccatalog", "persona_scope": "customer", "record_context_ref": None, "viewport": None, "display_name": "Topics"}],
        created_by=1, connected_org_id=org)
    claims = []
    for surf in (A, B):
        for rule in ("PLM-A11Y-001", "PLM-A11Y-002"):
            cr = coord.write_claim(session, actor="s3", test_id=None, archetype="ui", claim_kind="conformance-claim",
                                   asserted_truth=ConformanceClaimBody(plimsol_rule_id=rule, surface=surf),
                                   semantic_conditions=SemanticConditionsBody())
            coord.promote_claim_to_approved(session, actor="human", test_id=cr.test_id, version_seq=cr.version_seq)
            _recipe(session, coord, str(cr.test_id), "ui-inspection", surface=surf)
            claims.append(str(cr.test_id))
    set_id = claim_sets.create_claim_set(session, persona_scope="customer", inventory_version=v, catalogue_release_id=1,
                                         created_by=1, members=[{"test_id": t, "applicability": "APPLICABLE", "executable": True} for t in claims])
    session.execute(text("UPDATE claim_sets SET status='approved', approved_by=1, approved_at=clock_timestamp() WHERE id=CAST(:i AS uuid)"), {"i": str(set_id)})
    key = f"S4-C-{uuid4().hex[:5]}"
    identity.establish(session, key, origin="jira", evidence={"t": 1}, established_by="test")
    d = sl.declare(session, requirement_key=key, surface_key=canonical_surface_key(A), actor_user_id=1)
    assert d.materialised == 2, d
    assert [x.surface_key for x in sl.declared_surfaces(session, requirement_key=key)] == [canonical_surface_key(A)]
    plan = P.plan(session, tenant_id=1, scope={"scope_kind": "requirement", "key": key, "environment_id": ENV_SANDBOX},
                  planned_by=1, env_reader=_env)
    res = plan["resolution"]
    assert res["personas"] == ["customer"] and len(res["declared_surfaces"]) == 1, (res["requirement_keys"], res["personas"], res["declared_surfaces"], plan["inputs"], res["claim_count"], plan["exclusions"]["counts"])
    assert res["manifests"]["functional"]["pairs"] == []                     # no functional lane here
    m = res["manifests"]["conformance"]
    assert len(m) == 1 and m[0]["claim_set_id"] == str(set_id) and m[0]["surface_keys"] == [canonical_surface_key(A)]
    assert m[0]["excluded_surfaces"] == [canonical_surface_key(B)] and len(m[0]["claims"]) == 2
    assert ("surface", canonical_surface_key(B), P.X_SURFACE_NOT_DECLARED) in _reasons(plan)
    # execute: the conformance enqueue receives the filter; no S4 job
    ui_calls = []

    def fake_ui(session_, *, subject, claim_set_id, surface_keys, sf_client, trigger):
        ui_calls.append((str(claim_set_id), list(surface_keys), trigger["plan_id"]))
        return {"manifest_id": "m-1", "job_id": "j-1"}
    receipt = P.execute_plan(session, tenant_id=1, plan_id=plan["id"], executed_by=1,
                             enqueue=lambda **kw: (_ for _ in ()).throw(AssertionError("no S4 job expected")),
                             enqueue_ui=fake_ui)
    assert ui_calls == [(str(set_id), [canonical_surface_key(A)], plan["id"])] and receipt["ui_jobs"][0]["manifest_id"] == "m-1"
