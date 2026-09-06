"""Step A.1 DB-real acceptance — gated on S3A3_TEST_DATABASE_URL (scratch,
tenant_1 at 20260907_0010). Reuses the Step A suite's world helpers.

  a. an applied DERIVED edit becomes an EXECUTABLE version through the
     approval act: the written version is `approved` with a
     `recipe_approved` event carrying `gate_apply_approval` + the
     proposal id + the human; the S2 selector returns THAT version; the
     proposal enters `reverify_state='queued'` with the job id;
  b. the real consumer runs the queued job (run_fn injected: persists a
     run row) and `settle_reverifies` records `ran` + run id/outcome/verdict;
  c. a job that completes with NO run settles as `no_run /
     no_eligible_recipe` — the silence made loud;
  d. SPECULATIVE apply still refused: no version, no promotion, no job;
  e. a deprecated claim refuses `claim_deprecated` before any write —
     recipe_edit AND rerun; the panel read carries `claim_status`;
  f. a moved recipe refuses `recipe_moved`;
  g. the autonomous pass PRE-APPROVES a live DERIVED row and writes no
     version (audited);
  h. `reexamine` over July-shaped applied rows: deprecated → recorded
     refusal; DERIVED on a live claim → promoted + queued; not DERIVED →
     refused; a second run changes nothing.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_repair_gate as T  # noqa: E402  (the Step A world helpers)

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DB, reason="set S3A3_TEST_DATABASE_URL "
                       "(non-prod scratch, tenant_1 at 20260907_0010)"),
]
TENANT, ENV, SOBJECT = T.TENANT, T.ENV, T.SOBJECT


@pytest.fixture(scope="module")
def world():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    if os.environ.get("DATABASE_URL", "") != DB:
        pytest.skip("DATABASE_URL must point at the scratch DB for this suite")
    from primeqa.db import init_db
    init_db(DB)
    pub = create_engine(DB)
    with pub.begin() as c:
        c.execute(text(
            "INSERT INTO public.environments (id, tenant_id, name, env_type, "
            "sf_instance_url, sf_api_version, is_production, is_active) "
            "VALUES (:i, :t, 'gate sandbox', 'sandbox', 'https://x.test', "
            "'v60.0', false, true) ON CONFLICT (id) DO UPDATE SET "
            "is_production = false"), {"i": ENV, "t": TENANT})
    T._settings(agent_enabled=True, repair_auto_apply=False,
                repair_gate_apply_enabled=True, max_fix_attempts_per_run=3)
    w = {"claims": [], "recipes": []}
    with T._conn() as conn:
        s = Session(bind=conn)
        try:
            for key in ("A", "B", "C", "D", "E", "F", "G"):
                w[key] = T._write_claim_and_recipe(
                    s, asserted_field="Amount",
                    staged={f"{SOBJECT}.Name": "n", f"{SOBJECT}.Amount": "5",
                            f"{SOBJECT}.Line_Total__c": "1"})
                w["claims"].append(w[key][0]); w["recipes"].append(w[key][1])
            s.commit()
        finally:
            s.close()
    yield w
    ids = [str(c) for c in w["claims"]]
    with T._conn() as conn:
        for tbl, col in (("repair_proposals", "claim_test_id"),
                         ("s6_interpretations", "claim_test_id"),
                         ("s4_execution_runs", "claim_test_id"),
                         ("s4_execution_job_attempts", None),
                         ("s4_execution_jobs", "test_id"),
                         ("test_requirement_links", "test_id")):
            if col is None:
                conn.execute(text("DELETE FROM s4_execution_job_attempts WHERE job_id IN "
                                  "(SELECT id FROM s4_execution_jobs WHERE test_id = ANY(CAST(:ids AS uuid[])))"),
                             {"ids": ids})
                continue
            conn.execute(text(f"DELETE FROM {tbl} WHERE {col} = ANY(CAST(:ids AS uuid[]))"),  # noqa: S608
                         {"ids": ids})
        conn.execute(text("DELETE FROM test_provenance WHERE recipe_id = ANY(CAST(:r AS uuid[]))"),
                     {"r": [str(r) for r in w["recipes"]]})
        conn.execute(text("DELETE FROM test_provenance WHERE claim_test_id = ANY(CAST(:ids AS uuid[]))"),
                     {"ids": ids})
        conn.execute(text("DELETE FROM test_recipes WHERE claim_test_id = ANY(CAST(:ids AS uuid[]))"),
                     {"ids": ids})
        conn.execute(text("DELETE FROM test_claims WHERE test_id = ANY(CAST(:ids AS uuid[]))"),
                     {"ids": ids})
    T._settings(agent_enabled=True, repair_auto_apply=False,
                repair_gate_apply_enabled=False, max_fix_attempts_per_run=3)


def _approve_claim(claim_id):
    """The claim must be APPROVED for the selector to consider it."""
    from sqlalchemy.orm import Session
    from primeqa.test_representation.coordinator import SemanticTransactionCoordinator
    with T._conn() as conn:
        s = Session(bind=conn)
        try:
            coord = SemanticTransactionCoordinator()
            c = coord.get_latest_claim(s, claim_id)
            coord.promote_claim_to_approved(s, actor="human", test_id=claim_id,
                                            version_seq=c.version_seq)
            s.commit()
        finally:
            s.close()


def _deprecate_claim(claim_id):
    from sqlalchemy.orm import Session
    from primeqa.test_representation.coordinator import SemanticTransactionCoordinator
    with T._conn() as conn:
        s = Session(bind=conn)
        try:
            coord = SemanticTransactionCoordinator()
            c = coord.get_latest_claim(s, claim_id)
            coord.deprecate_claim(s, actor="human", test_id=claim_id,
                                  version_seq=c.version_seq,
                                  reason="A.1 suite: withdrawn test")
            s.commit()
        finally:
            s.close()


def _proposal(pid):
    with T._conn() as conn:
        return conn.execute(text(
            "SELECT status, gate_verdict, applied_recipe_version_seq, reverify_job_id, "
            "reverify_state, reverify_run_id, reverify_outcome, reverify_verdict, "
            "reverify_refusal, payload FROM repair_proposals WHERE id = :p"),
            {"p": pid}).mappings().first()


def _versions(recipe_id):
    with T._conn() as conn:
        return conn.execute(text(
            "SELECT version_seq, status FROM test_recipes WHERE recipe_id = CAST(:r AS uuid) "
            "ORDER BY version_seq"), {"r": str(recipe_id)}).all()


def _derived_proposal(claim, run, field="Line_Total__c"):
    return T._plant_proposal(claim, run, verdict="DERIVED",
                             grounding={"rule": "R1", "s1_fact": "is_createable=false"},
                             field_changes={field: "__REMOVE__"})


def _run_fn_persisting(outcome="failed", verdict="creation_rejected"):
    """An injected consumer run_fn: persists a run (as the executor would)
    and returns ran=True — no Salesforce client needed on scratch."""
    from types import SimpleNamespace

    from primeqa.execution_engine.run import RunPathResult

    def _fn(tenant_id, test_id, *, environment_id, client=None):
        with T._conn() as conn:
            rec = conn.execute(text(
                "SELECT recipe_id, version_seq FROM test_recipes WHERE claim_test_id = "
                "CAST(:c AS uuid) AND valid_to IS NULL LIMIT 1"),
                {"c": str(test_id)}).first()
            run_id = T._plant_run(conn, claim_id=test_id, recipe_id=rec[0],
                                  recipe_seq=rec[1], outcome=outcome, verdict=verdict,
                                  cause_kind="platform_constraint")
        # the consumer logs result.evidence.outcome; the settle pass reads the
        # persisted row, so the in-memory outcome only needs to be readable
        return RunPathResult(ran=True, evidence=SimpleNamespace(
            outcome="passed", run_id=run_id))
    return _fn


def _run_fn_nothing(tenant_id, test_id, *, environment_id, client=None):
    from primeqa.execution_engine.run import RunPathResult
    return RunPathResult(ran=False, reason="no_eligible_recipe")


# ---------------------------------------------------------------------------

def test_a_applied_derived_edit_becomes_an_executable_version(world):
    from sqlalchemy.orm import Session
    from primeqa.execution_engine.run import _MIN_AVAILABLE_ENV
    from primeqa.intelligence.repair_agent import decide_proposal
    from primeqa.test_representation.coordinator import SemanticTransactionCoordinator
    claim, recipe, seq = world["A"]
    _approve_claim(claim)
    with T._conn() as conn:
        run = T._plant_run(conn, claim_id=claim, recipe_id=recipe, recipe_seq=seq,
                           outcome="failed", verdict="creation_rejected",
                           cause_kind="platform_constraint")
    pid = _derived_proposal(claim, run)
    res = decide_proposal(TENANT, pid, approve=True, decided_by=1)
    assert res["ok"] is True, res
    assert res["applied_recipe_version_seq"] == 2 and res["reverify_job_id"]
    assert _versions(recipe) == [(1, "generated_unapproved"), (2, "approved")]
    with T._conn() as conn:
        ev = conn.execute(text(
            "SELECT event_kind, event_data FROM test_provenance WHERE recipe_id = "
            "CAST(:r AS uuid) ORDER BY event_at"), {"r": str(recipe)}).mappings().all()
        s = Session(bind=conn)
        try:
            sel = SemanticTransactionCoordinator().select_recipe_for_execution(
                s, claim, available_environment=_MIN_AVAILABLE_ENV, replay_mode="live")
        finally:
            s.close()
    kinds = [e["event_kind"] for e in ev]
    assert kinds[-2:] == ["recipe_s8_rewrite", "recipe_approved"]
    assert ev[-2]["event_data"]["provenance"] == "gate_apply"
    assert ev[-1]["event_data"]["provenance"] == "gate_apply_approval"
    assert ev[-1]["event_data"]["proposal_id"] == pid
    assert ev[-1]["event_data"]["decided_by"] == 1
    assert ev[-1]["event_data"]["gate_verdict"] == "DERIVED"
    assert sel is not None and sel.version_seq == 2          # the selector finds it
    row = _proposal(pid)
    assert row["status"] == "applied" and row["reverify_state"] == "queued"
    assert row["applied_recipe_version_seq"] == 2
    assert row["reverify_job_id"] == res["reverify_job_id"]
    world["A_pid"], world["A_job"] = pid, res["reverify_job_id"]


def test_b_the_consumer_runs_the_job_and_settle_records_the_run(world):
    from primeqa.execution_engine.consumer import process_execution_job_for_tenant
    from primeqa.intelligence.repair_agent import list_proposals, settle_reverifies
    pid, job_id = world["A_pid"], world["A_job"]
    # pending rows are OPEN work on the panel
    lp = list_proposals(TENANT)
    row = next(r for r in lp["proposals"] if r["id"] == pid)
    assert row["status"] == "applied" and row["reverify"]["state"] == "queued"
    assert lp["verdict_counts"]["REVERIFY_PENDING"] >= 1
    assert settle_reverifies(TENANT)["settled"] == 0           # job still queued
    done = process_execution_job_for_tenant(TENANT, run_fn=_run_fn_persisting("failed"))
    assert done == job_id
    out = settle_reverifies(TENANT)
    assert out["settled"] >= 1
    r = _proposal(pid)
    assert r["reverify_state"] == "ran" and r["reverify_outcome"] == "failed"
    assert r["reverify_verdict"] == "creation_rejected" and r["reverify_run_id"]
    assert r["reverify_refusal"] is None
    # settled rows leave the panel; a second settle changes nothing
    assert all(x["id"] != pid for x in list_proposals(TENANT)["proposals"])
    assert settle_reverifies(TENANT)["settled"] == 0


def test_c_a_job_that_runs_nothing_settles_as_the_loud_silence(world):
    from primeqa.execution_engine.consumer import process_execution_job_for_tenant
    from primeqa.intelligence.repair_agent import decide_proposal, settle_reverifies
    claim, recipe, seq = world["B"]
    _approve_claim(claim)
    with T._conn() as conn:
        run = T._plant_run(conn, claim_id=claim, recipe_id=recipe, recipe_seq=seq,
                           outcome="errored", verdict="not_evaluated", cause_kind=None)
    pid = T._plant_proposal(claim, run, kind="rerun", verdict="DERIVED",
                            grounding={"rule": "K-rerun", "no_recipe_mutation": True})
    res = decide_proposal(TENANT, pid, approve=True, decided_by=1)
    assert res["ok"] and _proposal(pid)["reverify_state"] == "queued"
    assert process_execution_job_for_tenant(TENANT, run_fn=_run_fn_nothing) == res["s4_job_id"]
    settle_reverifies(TENANT)
    r = _proposal(pid)
    assert r["reverify_state"] == "no_run" and r["reverify_refusal"] == "no_eligible_recipe"
    assert r["reverify_run_id"] is None


def test_d_speculative_apply_is_still_refused_with_no_write(world):
    from primeqa.intelligence.repair_agent import decide_proposal
    claim, recipe, seq = world["C"]
    _approve_claim(claim)
    with T._conn() as conn:
        run = T._plant_run(conn, claim_id=claim, recipe_id=recipe, recipe_seq=seq,
                           outcome="failed", verdict="creation_rejected",
                           cause_kind="platform_constraint")
    pid = T._plant_proposal(claim, run, verdict="SPECULATIVE",
                            grounding={"reason": "no_platform_error"},
                            field_changes={"Name": "x"})
    res = decide_proposal(TENANT, pid, approve=True, decided_by=1)
    assert res["ok"] is False and res["refused"] is True
    assert _versions(recipe) == [(1, "generated_unapproved")]
    assert _proposal(pid)["reverify_state"] is None


def test_e_deprecated_claim_refuses_loudly_for_every_kind(world):
    from primeqa.intelligence.repair_agent import decide_proposal, list_proposals
    claim, recipe, seq = world["D"]
    _approve_claim(claim)
    _deprecate_claim(claim)
    with T._conn() as conn:
        run = T._plant_run(conn, claim_id=claim, recipe_id=recipe, recipe_seq=seq,
                           outcome="failed", verdict="creation_rejected",
                           cause_kind="platform_constraint")
    pid = _derived_proposal(claim, run)
    res = decide_proposal(TENANT, pid, approve=True, decided_by=1)
    assert res["ok"] is False and res["error"].startswith("claim_deprecated")
    assert res["claim_status"] == "deprecated"
    assert _versions(recipe)[-1][0] == 1                       # nothing written
    with T._conn() as conn:
        n_jobs = conn.execute(text("SELECT COUNT(*) FROM s4_execution_jobs WHERE "
                                   "test_id = CAST(:t AS uuid)"), {"t": str(claim)}).scalar()
    assert n_jobs == 0                                         # nothing enqueued
    row = next(r for r in list_proposals(TENANT)["proposals"] if r["id"] == pid)
    assert row["claim_status"] == "deprecated"
    with T._conn() as conn:
        conn.execute(text("UPDATE repair_proposals SET status='rejected' WHERE id=:p"), {"p": pid})
        run2 = T._plant_run(conn, claim_id=claim, recipe_id=recipe, recipe_seq=seq,
                            outcome="errored", verdict="not_evaluated", cause_kind=None)
    rerun = T._plant_proposal(claim, run2, kind="rerun", verdict="DERIVED",
                              grounding={"rule": "K-rerun"})
    res = decide_proposal(TENANT, rerun, approve=True, decided_by=1)
    assert res["ok"] is False and res["error"].startswith("claim_deprecated")


def test_f_a_moved_recipe_refuses(world):
    from sqlalchemy.orm import Session
    from primeqa.intelligence.repair_agent import decide_proposal
    from primeqa.test_representation.coordinator import SemanticTransactionCoordinator
    claim, recipe, seq = world["E"]
    _approve_claim(claim)
    with T._conn() as conn:
        run = T._plant_run(conn, claim_id=claim, recipe_id=recipe, recipe_seq=seq,
                           outcome="failed", verdict="creation_rejected",
                           cause_kind="platform_constraint")
        s = Session(bind=conn)
        try:
            coord = SemanticTransactionCoordinator()
            cur = coord.get_recipe_latest(s, recipe)
            coord.write_recipe(
                s, actor="human", recipe_id=recipe, claim_test_id=cur.claim_test_id,
                trigger_kind=cur.trigger_kind, recipe_kind=cur.recipe_kind,
                causal_initiation=cur.causal_initiation,
                observation_realization=cur.observation_realization,
                execution_environment=cur.execution_environment,
                claim_version_seq=cur.claim_version_seq, priority=cur.priority)
            s.commit()
        finally:
            s.close()
    pid = _derived_proposal(claim, run)
    res = decide_proposal(TENANT, pid, approve=True, decided_by=1)
    assert res["ok"] is False and res["error"].startswith("recipe_moved")
    assert len(_versions(recipe)) == 2                         # no third version


def test_g_the_autonomous_pass_pre_approves_and_writes_nothing(world):
    from primeqa.intelligence.repair_agent import auto_apply_proposals
    claim, recipe, seq = world["F"]
    _approve_claim(claim)
    with T._conn() as conn:
        run = T._plant_run(conn, claim_id=claim, recipe_id=recipe, recipe_seq=seq,
                           outcome="failed", verdict="creation_rejected",
                           cause_kind="platform_constraint")
    pid = _derived_proposal(claim, run)
    T._settings(repair_auto_apply=True)
    try:
        out = auto_apply_proposals(TENANT)
    finally:
        T._settings(repair_auto_apply=False)
    assert out["applied"] >= 1
    r = _proposal(pid)
    assert r["status"] == "approved" and r["payload"].get("auto_approved") is True
    assert r["reverify_state"] is None and _versions(recipe) == [(1, "generated_unapproved")]
    from sqlalchemy import create_engine
    with create_engine(DB).connect() as c:
        n = c.execute(text("SELECT COUNT(*) FROM public.activity_log WHERE action = "
                           "'ui.repair_auto_approved' AND details->>'proposal_id' = :p"),
                      {"p": str(pid)}).scalar()
    assert n == 1


def test_h_reexamine_the_july_shaped_rows(world):
    from primeqa.intelligence import repair_gate as G
    # (1) a DERIVED edit on a LIVE claim whose applied version is current
    claim, recipe, seq = world["G"]
    _approve_claim(claim)
    from sqlalchemy.orm import Session
    from primeqa.test_representation.coordinator import SemanticTransactionCoordinator
    with T._conn() as conn:
        s = Session(bind=conn)
        try:
            coord = SemanticTransactionCoordinator()
            cur = coord.get_recipe_latest(s, recipe)
            res = coord.write_recipe(
                s, actor="s8", recipe_id=recipe, claim_test_id=cur.claim_test_id,
                trigger_kind=cur.trigger_kind, recipe_kind=cur.recipe_kind,
                causal_initiation=cur.causal_initiation,
                observation_realization=cur.observation_realization,
                execution_environment=cur.execution_environment,
                claim_version_seq=cur.claim_version_seq, priority=cur.priority)
            s.commit(); applied_seq = res.version_seq
        finally:
            s.close()
        run = T._plant_run(conn, claim_id=claim, recipe_id=recipe, recipe_seq=seq,
                           outcome="failed", verdict="creation_rejected",
                           cause_kind="platform_constraint")
    live = T._plant_proposal(claim, run, verdict="DERIVED", grounding={"rule": "R1"},
                             status="applied", auto_applied=True,
                             field_changes={"Line_Total__c": "__REMOVE__"},
                             payload={"action": "recipe_edit", "recipe_id": str(recipe),
                                      "new_version_seq": applied_seq})
    # (2) a DERIVED edit on a DEPRECATED claim (the July shape)
    dclaim, drecipe, dseq = world["D"]
    with T._conn() as conn:
        drun = T._plant_run(conn, claim_id=dclaim, recipe_id=drecipe, recipe_seq=dseq,
                            outcome="failed", verdict="creation_rejected",
                            cause_kind="platform_constraint")
        conn.execute(text("UPDATE repair_proposals SET status='rejected' WHERE "
                          "claim_test_id = CAST(:c AS uuid) AND status IN ('proposed','approved')"),
                     {"c": str(dclaim)})
    dead = T._plant_proposal(dclaim, drun, verdict="DERIVED", grounding={"rule": "R1"},
                             status="applied", auto_applied=True,
                             field_changes={"Line_Total__c": "__REMOVE__"},
                             payload={"action": "recipe_edit", "recipe_id": str(drecipe),
                                      "new_version_seq": 1})
    # (3) a SPECULATIVE applied edit on a live claim
    cclaim, crecipe, cseq = world["C"]
    with T._conn() as conn:
        crun = T._plant_run(conn, claim_id=cclaim, recipe_id=crecipe, recipe_seq=cseq,
                            outcome="failed", verdict="creation_rejected",
                            cause_kind="platform_constraint")
        conn.execute(text("UPDATE repair_proposals SET status='rejected' WHERE "
                          "claim_test_id = CAST(:c AS uuid) AND status IN ('proposed','approved')"),
                     {"c": str(cclaim)})
    spec = T._plant_proposal(cclaim, crun, verdict="SPECULATIVE",
                             grounding={"reason": "no_platform_error"},
                             status="applied", auto_applied=False,
                             field_changes={"Name": "x"},
                             payload={"action": "recipe_edit", "recipe_id": str(crecipe),
                                      "new_version_seq": 1})
    out = {o["proposal_id"]: o for o in G.reexamine_applied(TENANT, actor_user_id=1)}
    assert out[live]["action"] == "promoted_and_queued" and out[live]["s4_job_id"]
    assert out[dead]["action"] == "refused" and out[dead]["refusal"] == "claim_deprecated"
    assert out[spec]["action"] == "refused" and out[spec]["refusal"] == "not_derived"
    assert _versions(recipe)[-1] == (applied_seq, "approved")
    with T._conn() as conn:
        ev = conn.execute(text(
            "SELECT event_data FROM test_provenance WHERE recipe_id = CAST(:r AS uuid) "
            "AND event_kind = 'recipe_approved' ORDER BY event_at DESC LIMIT 1"),
            {"r": str(recipe)}).scalar()
    assert ev["provenance"] == "gate_retro_approval" and ev["proposal_id"] == live
    r = _proposal(live)
    assert r["reverify_state"] == "queued" and r["applied_recipe_version_seq"] == applied_seq
    assert _proposal(dead)["reverify_state"] == "refused"
    assert _proposal(spec)["reverify_refusal"] == "not_derived"
    # idempotent: the second pass sees nothing to examine
    again = G.reexamine_applied(TENANT, actor_user_id=1)
    assert all(o["proposal_id"] not in (live, dead, spec) for o in again)
