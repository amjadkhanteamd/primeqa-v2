"""Step A DB-real acceptance — gated on S3A3_TEST_DATABASE_URL (scratch,
tenant_1 at 20260906_0010; public.tenant_agent_settings at migration 069).

The pure classifier is tabled in tests/unit/test_repair_gate.py; this
suite proves the WIRING around it on real rows through the real paths:

  a. triage writes NO unclassified row (gate_verdict + grounding +
     classifier_version on every insert) — SEMANTIC / SPECULATIVE /
     DERIVED each land from a planted failure;
  b. agent_enabled=false → triage writes zero rows (loudly-once);
  c. the route/decide refuses SPECULATIVE, refuses DERIVED with no
     grounding, refuses everything while the switch is OFF;
  d. the auto pass ignores confidence (planted 0.99 SPECULATIVE → not
     applied) and applies a DERIVED row only with all three flags ON;
  e. retro-classification is idempotent (run twice, same counts, zero
     writes the second time);
  f. the D3 revert: a refused auto-applied edit is reverted to its
     pre-edit content as a NEW version with gate_retro_revert provenance
     + a re-verify job; a DERIVED one is kept; a second run reverts nothing;
  g. the S1 facts reader against a planted org world (createable=false,
     absent, picklist default / sole-active).

Claims and recipes are written through the coordinator (the real S2 write
path); runs / interpretations / evidence are planted rows (the 3A-4
transcript posture). Every row this suite plants is removed at teardown;
the tenant's settings row is restored to the dormant defaults.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DB, reason="set S3A3_TEST_DATABASE_URL "
                       "(non-prod scratch, tenant_1 at 20260906_0010)"),
]

TENANT = 1
ENV = 5901                      # a planted SANDBOX environment (public)
SOBJECT = "Opportunity"
REQ_KEY = "req-302"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _conn():
    from primeqa.semantic.connection import get_tenant_connection
    return get_tenant_connection(TENANT)


def _settings(**flags):
    from primeqa.core.agent_settings import AgentSettingsRepository
    from primeqa.db import get_db
    db = next(get_db())
    try:
        return AgentSettingsRepository(db).update(TENANT, updated_by=1, **flags)
    finally:
        db.close()


def _write_claim_and_recipe(session, *, asserted_field: str,
                            staged: dict, sobject: str = SOBJECT):
    """A value-claim asserting ``sobject.asserted_field`` + a data recipe
    whose subject create stages ``staged`` — via the coordinator."""
    from primeqa.test_representation import (
        IdentityBearingRef, LiteralValue, SemanticConditionsBody, ValueClaimBody)
    from primeqa.test_representation.coordinator import (
        SemanticTransactionCoordinator)
    from primeqa.test_representation.models.environment import (
        ExecutionEnvironmentBody)
    from primeqa.test_representation.models.recipes.data_recipe import (
        CreateStep, DataRecipeBody)
    from primeqa.test_representation.models.references import LogicalRef
    from primeqa.test_representation.models.triggers.data_mutation import (
        DataMutationTriggerBody)
    coord = SemanticTransactionCoordinator()
    body = ValueClaimBody(
        subject=IdentityBearingRef(entity_type="Field", entity_id=uuid4(),
                                   version_seq=1,
                                   external_id=f"{sobject}.{asserted_field}"),
        expected_value=LiteralValue(value="Tech"))
    cr = coord.write_claim(
        session, actor="s3", test_id=None, archetype="data_behavior",
        claim_kind="value-claim", asserted_truth=body,
        semantic_conditions=SemanticConditionsBody())
    recipe = DataRecipeBody(
        api_choice="rest", identity_context="system",
        execution_mechanism="direct_api",
        steps=[CreateStep(step_id="s1",
                          target_object=LogicalRef(entity_type="Object",
                                                   external_id=sobject),
                          field_values=dict(staged))])
    rr = coord.write_recipe(
        session, actor="s3", recipe_id=None, claim_test_id=cr.test_id,
        trigger_kind="data-mutation-trigger", recipe_kind="data-recipe",
        causal_initiation=DataMutationTriggerBody(
            operation="create",
            target=LogicalRef(entity_type="Object", external_id=sobject),
            identity_context="system", volume="single"),
        observation_realization=recipe,
        execution_environment=ExecutionEnvironmentBody(),
        claim_version_seq=cr.version_seq)
    session.execute(text(
        "INSERT INTO test_requirement_links (test_id, external_system, "
        "external_key, link_kind, linked_by) VALUES (:t, 'jira', :k, "
        "'generated_from', 's3')"), {"t": str(cr.test_id), "k": REQ_KEY})
    return cr.test_id, rr.recipe_id, rr.version_seq


def _plant_run(conn, *, claim_id, recipe_id, recipe_seq, outcome, verdict,
               cause_kind, error=None):
    run_id = uuid4()
    now = datetime.now(timezone.utc)
    steps = []
    if error:
        steps.append({"kind": "create", "success": False, **error})
    conn.execute(text(
        "INSERT INTO s4_execution_runs (run_id, recipe_id, recipe_version_seq, "
        "claim_test_id, environment_id, outcome, started_at, finished_at, "
        "evidence) VALUES (:r, :rc, :rs, :c, :e, :o, :t, :t, "
        "CAST(:ev AS jsonb))"),
        {"r": str(run_id), "rc": str(recipe_id), "rs": recipe_seq,
         "c": str(claim_id), "e": ENV, "o": outcome, "t": now,
         "ev": json.dumps({"steps": steps})})
    conn.execute(text(
        "INSERT INTO s6_interpretations (run_id, recipe_id, claim_test_id, "
        "outcome, verdict, detail, cause_kind) VALUES (:r, :rc, :c, :o, :v, "
        "'{}'::jsonb, :k)"),
        {"r": str(run_id), "rc": str(recipe_id), "c": str(claim_id),
         "o": outcome, "v": verdict, "k": cause_kind})
    return run_id


def _facts_reader(facts):
    """An injectable S1 reader returning planted facts (seq 1, org 'x')."""
    def _r(conn, environment_id, sobject, touched):
        return {b: f for b, f in facts.items() if b in touched}, 1, "x"
    return _r


# ---------------------------------------------------------------------------
# the world
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def world():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    if os.environ.get("DATABASE_URL", "") != DB:
        pytest.skip("DATABASE_URL must point at the scratch DB for this suite "
                    "(the settings + environment reads go through the app engine)")
    from primeqa.db import init_db
    init_db(DB)                                  # binds get_db() (idempotent)
    pub = create_engine(DB)
    with pub.begin() as c:
        c.execute(text(
            "INSERT INTO public.environments (id, tenant_id, name, env_type, "
            "sf_instance_url, sf_api_version, is_production, is_active) "
            "VALUES (:i, :t, 'gate sandbox', 'sandbox', 'https://x.test', "
            "'v60.0', false, true) ON CONFLICT (id) DO UPDATE SET "
            "is_production = false"), {"i": ENV, "t": TENANT})
    _settings(agent_enabled=True, repair_auto_apply=False,
              repair_gate_apply_enabled=False, max_fix_attempts_per_run=3)

    w: dict = {"claims": [], "recipes": []}
    with _conn() as conn:
        s = Session(bind=conn)
        try:
            # claim A asserts Amount; recipe stages qualified keys
            w["A"] = _write_claim_and_recipe(
                s, asserted_field="Amount",
                staged={f"{SOBJECT}.Name": "n", f"{SOBJECT}.Amount": "5",
                        f"{SOBJECT}.Loan_Type__c": "Home",
                        f"{SOBJECT}.Line_Total__c": "1"})
            # claim B: a BARE staged key
            w["B"] = _write_claim_and_recipe(
                s, asserted_field="Amount",
                staged={"StageName": "Prospecting", f"{SOBJECT}.Name": "n"})
            s.commit()
        finally:
            s.close()
    for key in ("A", "B"):
        w["claims"].append(w[key][0]); w["recipes"].append(w[key][1])
    yield w
    # ---- teardown: remove every planted row; restore dormant defaults ----
    ids = [str(c) for c in w["claims"]]
    with _conn() as conn:
        for tbl, col in (("repair_proposals", "claim_test_id"),
                         ("s6_interpretations", "claim_test_id"),
                         ("s4_execution_runs", "claim_test_id"),
                         ("s4_execution_jobs", "test_id"),
                         ("test_requirement_links", "test_id")):
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
    _settings(agent_enabled=True, repair_auto_apply=False,
              repair_gate_apply_enabled=False, max_fix_attempts_per_run=3)


@pytest.fixture(autouse=True)
def _clean_proposals(world):
    ids = [str(c) for c in world["claims"]]
    with _conn() as conn:
        conn.execute(text("DELETE FROM repair_proposals WHERE claim_test_id = ANY(CAST(:ids AS uuid[]))"), {"ids": ids})
        conn.execute(text("DELETE FROM s6_interpretations WHERE claim_test_id = ANY(CAST(:ids AS uuid[]))"), {"ids": ids})
        conn.execute(text("DELETE FROM s4_execution_runs WHERE claim_test_id = ANY(CAST(:ids AS uuid[]))"), {"ids": ids})
    yield


def _triage(monkeypatch, field_changes, facts=None):
    """Run the real triage with the LLM step stubbed to a chosen remedy and
    the S1 reader stubbed to planted facts."""
    from primeqa.intelligence import repair_agent as RA
    from primeqa.intelligence import repair_gate as G
    monkeypatch.setattr(RA, "_propose_recipe_edit",
                        lambda conn, tid, row, key: {
                            "confidence": 0.99, "field_changes": field_changes,
                            "rationale": "stubbed"})
    monkeypatch.setattr(G, "_s1_facts", _facts_reader(facts or {}))
    return RA.triage_new_failures(TENANT, api_key_resolver=lambda t, e: "k")


def _proposals(claim_id):
    with _conn() as conn:
        return conn.execute(text(
            "SELECT id, proposal_kind, gate_verdict, grounding_source, "
            "classifier_version, classified_at, status, confidence "
            "FROM repair_proposals WHERE claim_test_id = CAST(:c AS uuid) "
            "ORDER BY id"), {"c": str(claim_id)}).mappings().all()


# ---------------------------------------------------------------------------
# a. triage classifies at creation — no unclassified row, ever
# ---------------------------------------------------------------------------

def test_a1_semantic_touching_an_asserted_field_is_refused_with_destination(
        world, monkeypatch):
    from primeqa.intelligence import repair_gate as G
    claim, recipe, seq = world["A"]
    with _conn() as conn:
        _plant_run(conn, claim_id=claim, recipe_id=recipe, recipe_seq=seq,
                   outcome="failed", verdict="creation_rejected",
                   cause_kind="platform_constraint",
                   error={"error_code": "INVALID_FIELD_FOR_INSERT_UPDATE",
                          "message": "Amount", "error_fields": ["Amount"]})
    out = _triage(monkeypatch, {"Amount": "__REMOVE__"},
                  facts={"amount": G.FieldFact(exists=True, is_createable=False)})
    assert out["proposed"] == 1
    (p,) = _proposals(claim)
    assert p["gate_verdict"] == "SEMANTIC"
    assert p["grounding_source"]["reason"] == "touches_asserted_field"
    assert p["grounding_source"]["fields"] == ["amount"]
    assert p["grounding_source"]["destination"] == {
        "key": REQ_KEY, "url": "/requirements/302"}
    assert p["classifier_version"] == G.CLASSIFIER_VERSION
    assert p["classified_at"] is not None
    # the panel read carries the verdict + destination, never the confidence
    from primeqa.intelligence.repair_agent import list_proposals
    row = next(r for r in list_proposals(TENANT)["proposals"] if r["id"] == p["id"])
    assert row["gate_verdict"] == "SEMANTIC" and row["destination"]["key"] == REQ_KEY
    assert "confidence" not in row


def test_a2_speculative_when_no_platform_error_exists(world, monkeypatch):
    claim, recipe, seq = world["A"]
    with _conn() as conn:
        _plant_run(conn, claim_id=claim, recipe_id=recipe, recipe_seq=seq,
                   outcome="failed", verdict="automation_not_triggered",
                   cause_kind="automation_effect_absent")
    assert _triage(monkeypatch, {"Loan_Type__c": "Personal"})["proposed"] == 1
    (p,) = _proposals(claim)
    assert p["gate_verdict"] == "SPECULATIVE"
    assert p["grounding_source"]["reason"] == "no_platform_error"


def test_a3_derived_r1_carries_the_s1_grounding(world, monkeypatch):
    from primeqa.intelligence import repair_gate as G
    claim, recipe, seq = world["A"]
    with _conn() as conn:
        _plant_run(conn, claim_id=claim, recipe_id=recipe, recipe_seq=seq,
                   outcome="failed", verdict="creation_rejected",
                   cause_kind="platform_constraint",
                   error={"error_code": "INVALID_FIELD_FOR_INSERT_UPDATE",
                          "message": "Line_Total__c is not createable",
                          "error_fields": ["Line_Total__c"]})
    out = _triage(monkeypatch, {"Line_Total__c": "__REMOVE__"},
                  facts={"line_total__c": G.FieldFact(
                      exists=True, is_createable=False, entity_id="e-lt")})
    assert out["proposed"] == 1
    (p,) = _proposals(claim)
    assert p["gate_verdict"] == "DERIVED"
    g = p["grounding_source"]
    assert g["rule"] == "R1" and g["s1_fact"] == "is_createable=false"
    assert g["s1_entity_id"] == "e-lt" and g["attested_by"] == "error_fields"


def test_a4_bare_staged_key_fails_closed(world, monkeypatch):
    claim, recipe, seq = world["B"]
    with _conn() as conn:
        _plant_run(conn, claim_id=claim, recipe_id=recipe, recipe_seq=seq,
                   outcome="failed", verdict="automation_not_triggered",
                   cause_kind="automation_effect_absent")
    assert _triage(monkeypatch, {"StageName": "Qualification"})["proposed"] == 1
    (p,) = _proposals(claim)
    assert p["gate_verdict"] == "SEMANTIC"
    assert p["grounding_source"]["reason"] == "bare_staged_key"


def test_a5_deterministic_kinds_carry_no_recipe_mutation(world, monkeypatch):
    claim, recipe, seq = world["A"]
    with _conn() as conn:
        _plant_run(conn, claim_id=claim, recipe_id=recipe, recipe_seq=seq,
                   outcome="errored", verdict="not_evaluated", cause_kind=None)
    assert _triage(monkeypatch, {})["proposed"] == 1
    (p,) = _proposals(claim)
    assert p["proposal_kind"] == "rerun" and p["gate_verdict"] == "DERIVED"
    assert p["grounding_source"]["no_recipe_mutation"] is True
    assert p["grounding_source"]["rule"] == "K-rerun"


def test_a6_the_tick_never_writes_an_unclassified_row(world, monkeypatch):
    claim, recipe, seq = world["A"]
    with _conn() as conn:
        for v, k, o in (("creation_rejected", "platform_constraint", "failed"),
                        ("not_evaluated", None, "errored"),
                        ("rejected_unasserted_reason", "other_vr_fired", "failed")):
            _plant_run(conn, claim_id=claim, recipe_id=recipe, recipe_seq=seq,
                       outcome=o, verdict=v, cause_kind=k)
    _triage(monkeypatch, {"Loan_Type__c": "Business"})
    rows = _proposals(claim)
    assert rows and all(r["gate_verdict"] in ("DERIVED", "SPECULATIVE", "SEMANTIC")
                        for r in rows)
    assert all(r["grounding_source"] and r["classifier_version"] for r in rows)


# ---------------------------------------------------------------------------
# b. agent_enabled=false gates CREATION
# ---------------------------------------------------------------------------

def test_b_agent_disabled_writes_zero_rows_loudly_once(world, monkeypatch, caplog):
    from primeqa.intelligence import repair_agent as RA
    claim, recipe, seq = world["A"]
    with _conn() as conn:
        _plant_run(conn, claim_id=claim, recipe_id=recipe, recipe_seq=seq,
                   outcome="errored", verdict="not_evaluated", cause_kind=None)
    _settings(agent_enabled=False)
    try:
        RA._WARNED_DISABLED.discard(TENANT)
        with caplog.at_level("WARNING", logger="primeqa.repair_agent"):
            out1 = RA.triage_new_failures(TENANT)
            out2 = RA.triage_new_failures(TENANT)
        assert out1 == {"proposed": 0, "scanned": 0, "disabled": True} == out2
        assert _proposals(claim) == []
        assert sum("agent_enabled=false" in r.message for r in caplog.records) == 1
    finally:
        _settings(agent_enabled=True)
        RA._WARNED_DISABLED.discard(TENANT)


# ---------------------------------------------------------------------------
# c. the refusals (route + decide) and the switch
# ---------------------------------------------------------------------------

def _plant_proposal(claim, run_id, *, kind="recipe_edit", verdict, grounding,
                    status="proposed", confidence=None, field_changes=None,
                    auto_applied=False, payload=None):
    with _conn() as conn:
        pid = conn.execute(text(
            "INSERT INTO repair_proposals (run_id, claim_test_id, environment_id, "
            "verdict, cause_kind, proposal_kind, payload, confidence, "
            "proposed_payload, status, auto_applied, gate_verdict, "
            "grounding_source, classified_at, classifier_version) VALUES "
            "(:r, :c, :e, 'creation_rejected', 'platform_constraint', :k, "
            "CAST(:p AS jsonb), :conf, CAST(:pp AS jsonb), :st, :aa, :gv, "
            "CAST(:gs AS jsonb), NOW(), 'gate@v1') RETURNING id"),
            {"r": str(run_id), "c": str(claim), "e": ENV, "k": kind,
             "p": json.dumps(payload or {}), "conf": confidence,
             "pp": json.dumps({"field_changes": field_changes or {},
                               "rationale": "planted"}),
             "st": status, "aa": auto_applied, "gv": verdict,
             "gs": json.dumps(grounding) if grounding is not None else None}
        ).scalar()
    return pid


def test_c1_decide_refuses_speculative_and_ungrounded_and_dormant(world):
    from primeqa.intelligence.repair_agent import decide_proposal
    claim, recipe, seq = world["A"]
    with _conn() as conn:
        run = _plant_run(conn, claim_id=claim, recipe_id=recipe, recipe_seq=seq,
                         outcome="failed", verdict="creation_rejected",
                         cause_kind="platform_constraint")
    spec = _plant_proposal(claim, run, verdict="SPECULATIVE",
                           grounding={"reason": "no_platform_error"},
                           field_changes={"Loan_Type__c": "Personal"})
    _settings(repair_gate_apply_enabled=True)
    try:
        res = decide_proposal(TENANT, spec, approve=True, decided_by=1)
        assert res["ok"] is False and res["refused"] is True
        assert res["gate_verdict"] == "SPECULATIVE"
        # (one ACTIVE proposal per claim+kind — the partial unique index —
        # so retire each refused row before planting the next shape)
        with _conn() as conn:
            conn.execute(text("UPDATE repair_proposals SET status = 'rejected' WHERE id = :p"), {"p": spec})
        # a DERIVED row without grounding is refused too
        bare = _plant_proposal(claim, uuid4(), verdict="DERIVED", grounding={},
                               field_changes={"Line_Total__c": "__REMOVE__"})
        res = decide_proposal(TENANT, bare, approve=True, decided_by=1)
        assert res["ok"] is False and "grounding" in res["error"]
        with _conn() as conn:
            conn.execute(text("UPDATE repair_proposals SET status = 'rejected' WHERE id = :p"), {"p": bare})
        # nothing was applied: the recipe has no new version
        with _conn() as conn:
            n = conn.execute(text(
                "SELECT COUNT(*) FROM test_recipes WHERE recipe_id = CAST(:r AS uuid)"),
                {"r": str(recipe)}).scalar()
        assert n == 1
    finally:
        _settings(repair_gate_apply_enabled=False)
    # switch OFF: even a grounded DERIVED row is refused
    derived = _plant_proposal(claim, uuid4(), verdict="DERIVED",
                              grounding={"rule": "R1"},
                              field_changes={"Line_Total__c": "__REMOVE__"})
    res = decide_proposal(TENANT, derived, approve=True, decided_by=1)
    assert res["ok"] is False and "dormant" in res["error"]


def test_c2_the_route_refuses_an_apply_post_for_a_speculative_row(world):
    """The refusal is the control: the POST lands on the route and is
    refused server-side, not merely hidden."""
    from primeqa.app import create_app
    from primeqa.core.service import AuthService  # noqa: F401 — app import binds engines
    import jwt as _jwt
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()
    claim, recipe, seq = world["A"]
    with _conn() as conn:
        run = _plant_run(conn, claim_id=claim, recipe_id=recipe, recipe_seq=seq,
                         outcome="failed", verdict="creation_rejected",
                         cause_kind="platform_constraint")
    spec = _plant_proposal(claim, run, verdict="SPECULATIVE",
                           grounding={"reason": "no_platform_error"},
                           field_changes={"Loan_Type__c": "Personal"})
    secret = os.environ.get("JWT_SECRET") or app.config.get("JWT_SECRET")
    now = datetime.now(timezone.utc)
    tok = _jwt.encode({"sub": "1", "tenant_id": TENANT, "email": "a@x",
                       "role": "superadmin", "full_name": "gate test",
                       "iat": now, "exp": now.timestamp() + 600},
                      secret, algorithm="HS256")
    client.set_cookie("access_token", tok)
    client.set_cookie("csrf_token", "gate-csrf")
    _settings(repair_gate_apply_enabled=True)
    try:
        r = client.post(f"/runs/substrate/repairs/{spec}",
                        data={"action": "approve", "csrf_token": "gate-csrf"})
        assert r.status_code in (302, 303)
        with _conn() as conn:
            st = conn.execute(text(
                "SELECT status FROM repair_proposals WHERE id = :p"),
                {"p": spec}).scalar()
            n = conn.execute(text(
                "SELECT COUNT(*) FROM test_recipes WHERE recipe_id = CAST(:r AS uuid)"),
                {"r": str(recipe)}).scalar()
        assert st == "proposed" and n == 1
        # the panel renders the SPECULATIVE row with NO apply form
        html = client.get("/runs/substrate").get_data(as_text=True)
        assert 'data-gate-verdict="SPECULATIVE"' in html
        assert "Open recipe" in html
        assert "real defects never appear here" not in html
        assert "% conf" not in html
        assert 'data-testid="repair-verdict-counts"' in html
    finally:
        _settings(repair_gate_apply_enabled=False)


# ---------------------------------------------------------------------------
# d. the auto pass: confidence is never a gate; DERIVED applies under all flags
# ---------------------------------------------------------------------------

def test_d1_planted_099_speculative_never_auto_applies(world):
    from primeqa.intelligence.repair_agent import auto_apply_proposals
    claim, recipe, seq = world["A"]
    with _conn() as conn:
        run = _plant_run(conn, claim_id=claim, recipe_id=recipe, recipe_seq=seq,
                         outcome="failed", verdict="creation_rejected",
                         cause_kind="platform_constraint")
    pid = _plant_proposal(claim, run, verdict="SPECULATIVE",
                          grounding={"reason": "no_platform_error"},
                          confidence=0.99,
                          field_changes={"Loan_Type__c": "Personal"})
    _settings(repair_auto_apply=True, repair_gate_apply_enabled=True)
    try:
        out = auto_apply_proposals(TENANT)
        assert out["applied"] == 0 and out["skipped"] >= 1
        with _conn() as conn:
            st = conn.execute(text("SELECT status, auto_applied FROM repair_proposals "
                                   "WHERE id = :p"), {"p": pid}).first()
            n = conn.execute(text(
                "SELECT COUNT(*) FROM test_recipes WHERE recipe_id = CAST(:r AS uuid)"),
                {"r": str(recipe)}).scalar()
        assert tuple(st) == ("proposed", False) and n == 1
    finally:
        _settings(repair_auto_apply=False, repair_gate_apply_enabled=False)


def test_d2_derived_auto_applies_only_with_all_three_flags(world):
    from primeqa.intelligence.repair_agent import auto_apply_proposals
    claim, recipe, seq = world["A"]
    with _conn() as conn:
        run = _plant_run(conn, claim_id=claim, recipe_id=recipe, recipe_seq=seq,
                         outcome="failed", verdict="creation_rejected",
                         cause_kind="platform_constraint")
    pid = _plant_proposal(claim, run, verdict="DERIVED",
                          grounding={"rule": "R1", "s1_fact": "is_createable=false"},
                          field_changes={"Line_Total__c": "__REMOVE__"})
    # auto flag on, switch off → dormant
    _settings(repair_auto_apply=True, repair_gate_apply_enabled=False)
    try:
        assert auto_apply_proposals(TENANT) == {"applied": 0, "skipped": 0}
        _settings(repair_gate_apply_enabled=True)
        out = auto_apply_proposals(TENANT)
        assert out["applied"] == 1
        with _conn() as conn:
            row = conn.execute(text(
                "SELECT status, auto_applied, payload FROM repair_proposals WHERE id = :p"),
                {"p": pid}).mappings().first()
            versions = conn.execute(text(
                "SELECT version_seq, observation_realization FROM test_recipes "
                "WHERE recipe_id = CAST(:r AS uuid) ORDER BY version_seq"),
                {"r": str(recipe)}).all()
        assert row["status"] == "applied" and row["auto_applied"] is True
        assert row["payload"]["new_version_seq"] == 2 and len(versions) == 2
        staged = versions[1][1]["steps"][0]["field_values"]
        assert f"{SOBJECT}.Line_Total__c" not in staged      # the edit landed
        assert f"{SOBJECT}.Line_Total__c" in versions[0][1]["steps"][0]["field_values"]
        world["applied_pid"] = pid
    finally:
        _settings(repair_auto_apply=False, repair_gate_apply_enabled=False)


# ---------------------------------------------------------------------------
# e. retro-classification is idempotent
# ---------------------------------------------------------------------------

def test_e_retro_classification_is_idempotent(world, monkeypatch):
    from primeqa.intelligence import repair_gate as G
    claim, recipe, seq = world["A"]
    with _conn() as conn:
        run1 = _plant_run(conn, claim_id=claim, recipe_id=recipe, recipe_seq=seq,
                          outcome="failed", verdict="creation_rejected",
                          cause_kind="platform_constraint",
                          error={"error_code": "INVALID_OR_NULL_FOR_RESTRICTED_PICKLIST",
                                 "message": "bad value for restricted picklist "
                                            "field: Loan_Type__c",
                                 "error_fields": ["Loan_Type__c"]})
        run2 = _plant_run(conn, claim_id=claim, recipe_id=recipe, recipe_seq=seq,
                          outcome="errored", verdict="not_evaluated", cause_kind=None)
        # legacy rows: NO gate columns (the pre-Step-A shape)
        for rid, kind, fc in ((run1, "recipe_edit", {"Loan_Type__c": "Home"}),
                              (run2, "rerun", {})):
            conn.execute(text(
                "INSERT INTO repair_proposals (run_id, claim_test_id, environment_id, "
                "verdict, cause_kind, proposal_kind, payload, proposed_payload) "
                "VALUES (:r, :c, :e, 'x', 'platform_constraint', :k, '{}'::jsonb, "
                "CAST(:pp AS jsonb))"),
                {"r": str(rid), "c": str(claim), "e": ENV, "k": kind,
                 "pp": json.dumps({"field_changes": fc, "rationale": "legacy"})})
    facts = {"loan_type__c": G.FieldFact(
        exists=True, is_createable=True,
        picklist_active_values=("Home", "Personal"), picklist_default="Home",
        entity_id="e-lt")}
    first = G.retro_classify(TENANT, s1_reader=_facts_reader(facts))
    second = G.retro_classify(TENANT, s1_reader=_facts_reader(facts))
    assert first["written"] >= 2 and second["written"] == 0
    assert first["counts"] == second["counts"]
    rows = {r["proposal_kind"]: r for r in _proposals(claim)}
    assert rows["recipe_edit"]["gate_verdict"] == "DERIVED"          # R2 default
    assert rows["recipe_edit"]["grounding_source"]["matched"] == "default"
    assert rows["rerun"]["gate_verdict"] == "DERIVED"
    assert all(r["classifier_version"] == G.CLASSIFIER_VERSION for r in rows.values())
    # a bumped classifier version re-classifies, deliberately
    monkeypatch.setattr(G, "CLASSIFIER_VERSION", "gate@v1-test")
    third = G.retro_classify(TENANT, s1_reader=_facts_reader(facts))
    assert third["written"] >= 2


# ---------------------------------------------------------------------------
# f. the D3 revert
# ---------------------------------------------------------------------------

def test_f_refused_auto_applied_edit_is_reverted_with_provenance(world):
    from primeqa.intelligence import repair_gate as G
    from primeqa.test_representation.coordinator import (
        SemanticTransactionCoordinator)
    from sqlalchemy.orm import Session
    claim, recipe, seq = world["B"]
    # an auto-applied edit that the gate now calls SPECULATIVE: write the
    # "edited" version 2 for real, then plant the applied row
    with _conn() as conn:
        s = Session(bind=conn)
        try:
            coord = SemanticTransactionCoordinator()
            cur = coord.get_recipe_latest(s, recipe)
            body = cur.observation_realization
            steps = list(body.steps)
            steps[0] = steps[0].model_copy(update={"field_values": {
                **steps[0].field_values, f"{SOBJECT}.Amount": "1000"}})
            res = coord.write_recipe(
                s, actor="s8", recipe_id=recipe, claim_test_id=cur.claim_test_id,
                trigger_kind=cur.trigger_kind, recipe_kind=cur.recipe_kind,
                causal_initiation=cur.causal_initiation,
                observation_realization=body.model_copy(update={"steps": steps}),
                execution_environment=cur.execution_environment,
                claim_version_seq=cur.claim_version_seq, priority=cur.priority)
            s.commit()
            edited_seq = res.version_seq
        finally:
            s.close()
        run = _plant_run(conn, claim_id=claim, recipe_id=recipe, recipe_seq=seq,
                         outcome="failed", verdict="creation_rejected",
                         cause_kind="platform_constraint")
    refused = _plant_proposal(
        claim, run, verdict="SPECULATIVE", grounding={"reason": "inference_or_chosen_value"},
        status="applied", auto_applied=True, field_changes={"Amount": "1000"},
        payload={"action": "recipe_edit", "recipe_id": str(recipe),
                 "new_version_seq": edited_seq})
    kept = _plant_proposal(
        claim, uuid4(), verdict="DERIVED", grounding={"rule": "R1"},
        status="applied", auto_applied=True, field_changes={"X": "__REMOVE__"},
        payload={"action": "recipe_edit", "recipe_id": str(recipe),
                 "new_version_seq": edited_seq})
    out = G.revert_refused_auto_applies(TENANT, actor_user_id=1)
    by_id = {o["proposal_id"]: o for o in out}
    assert by_id[refused]["action"] == "reverted"
    assert by_id[kept]["action"] == "kept_derived"
    assert by_id[refused]["restores_version_seq"] == edited_seq - 1
    with _conn() as conn:
        versions = conn.execute(text(
            "SELECT version_seq, observation_realization FROM test_recipes "
            "WHERE recipe_id = CAST(:r AS uuid) ORDER BY version_seq"),
            {"r": str(recipe)}).all()
        prov = conn.execute(text(
            "SELECT event_kind, event_data FROM test_provenance "
            "WHERE recipe_id = CAST(:r AS uuid) ORDER BY event_at DESC LIMIT 1"),
            {"r": str(recipe)}).mappings().first()
        row = conn.execute(text(
            "SELECT revert_recipe_version_seq, reverted_at FROM repair_proposals "
            "WHERE id = :p"), {"p": refused}).first()
        job = conn.execute(text(
            "SELECT COUNT(*) FROM s4_execution_jobs WHERE test_id = CAST(:t AS uuid)"),
            {"t": str(claim)}).scalar()
        audit = conn.execute(text(
            "SELECT COUNT(*) FROM public.activity_log WHERE action = "
            "'repair.gate_retro_revert' AND entity_id = :p"), {"p": refused}).scalar()
    # the new version's content == the pre-edit content
    assert versions[-1][0] == edited_seq + 1
    assert versions[-1][1]["steps"] == versions[edited_seq - 2][1]["steps"]
    assert prov["event_kind"] == "recipe_s8_rewrite"
    assert prov["event_data"]["provenance"] == "gate_retro_revert"
    assert prov["event_data"]["proposal_id"] == refused
    assert prov["event_data"]["predicted_verdict"] == "SPECULATIVE"
    assert row[0] == edited_seq + 1 and row[1] is not None
    assert job >= 1 and audit == 1
    # idempotent: a second pass reverts nothing
    again = {o["proposal_id"]: o for o in G.revert_refused_auto_applies(TENANT)}
    assert refused not in again and again[kept]["action"] == "kept_derived"


# ---------------------------------------------------------------------------
# g. the S1 facts reader over a planted org world
# ---------------------------------------------------------------------------

def test_g_s1_facts_reader_reads_createable_absent_and_picklist(world):
    from primeqa.intelligence import repair_gate as G
    org = uuid4(); obj = uuid4(); f_ro = uuid4(); f_pl = uuid4(); pvs = uuid4()
    pv1, pv2 = uuid4(), uuid4()
    with _conn() as conn:
        seq = conn.execute(text(
            "SELECT COALESCE(MAX(version_seq), 0) + 1 FROM logical_versions")).scalar()
        conn.execute(text(
            "INSERT INTO connected_orgs (id, org_type, sf_instance_url, label, "
            "environment_id) VALUES (CAST(:o AS uuid), 'sandbox', 'https://x', "
            "'gate probe', :e)"), {"o": str(org), "e": ENV})
        conn.execute(text(
            "INSERT INTO logical_versions (version_seq, version_name, version_type, "
            "connected_org_id) VALUES (:s, 'gate probe', 'manual_checkpoint', CAST(:o AS uuid))"),
            {"s": seq, "o": str(org)})
        now = datetime.now(timezone.utc)
        for eid, et, name in ((obj, "Object", SOBJECT),
                              (f_ro, "Field", f"{SOBJECT}.Line_Total__c"),
                              (f_pl, "Field", f"{SOBJECT}.Loan_Type__c"),
                              (pvs, "PicklistValueSet", "Loan_Type__c.set"),
                              (pv1, "PicklistValue", "Home"),
                              (pv2, "PicklistValue", "Personal")):
            conn.execute(text(
                "INSERT INTO entities (id, entity_type, sf_api_name, display_name, "
                "attributes, valid_from_seq, tenant_id, last_synced_at, "
                "connected_org_id) VALUES (CAST(:i AS uuid), :t, :n, :n, "
                "'{}'::jsonb, :s, :ten, :now, CAST(:o AS uuid))"),
                {"i": str(eid), "t": et, "n": name, "s": seq, "ten": TENANT,
                 "now": now, "o": str(org)})
        conn.execute(text(
            "INSERT INTO field_details (entity_id, object_entity_id, field_type, "
            "is_createable) VALUES (CAST(:f AS uuid), CAST(:o AS uuid), 'currency', "
            "false)"), {"f": str(f_ro), "o": str(obj)})
        conn.execute(text(
            "INSERT INTO field_details (entity_id, object_entity_id, field_type, "
            "is_createable, picklist_value_set_entity_id) VALUES (CAST(:f AS uuid), "
            "CAST(:o AS uuid), 'picklist', true, CAST(:p AS uuid))"),
            {"f": str(f_pl), "o": str(obj), "p": str(pvs)})
        for pv, name, dflt, order in ((pv1, "Home", True, 1), (pv2, "Personal", False, 2)):
            conn.execute(text(
                "INSERT INTO picklist_value_details (entity_id, "
                "picklist_value_set_entity_id, value_label, value_api_name, "
                "is_active, is_default, sort_order) VALUES (CAST(:e AS uuid), "
                "CAST(:p AS uuid), :n, :n, true, :d, :o)"),
                {"e": str(pv), "p": str(pvs), "n": name, "d": dflt, "o": order})
    try:
        with _conn() as conn:
            facts, at_seq, at_org = G._s1_facts(
                conn, ENV, SOBJECT,
                {"line_total__c": "Line_Total__c", "loan_type__c": "Loan_Type__c",
                 "ghost__c": "Ghost__c"})
        assert at_seq == seq and at_org == str(org)
        assert facts["line_total__c"].exists is True
        assert facts["line_total__c"].is_createable is False
        assert facts["loan_type__c"].picklist_active_values == ("Home", "Personal")
        assert facts["loan_type__c"].picklist_default == "Home"
        assert facts["ghost__c"].exists is False
        # through the classifier: R2 default, sole-active negative (D4)
        inp = G.GateInputs(
            proposal_kind="recipe_edit", field_changes={"Loan_Type__c": "Home"},
            staged_keys=(f"{SOBJECT}.Loan_Type__c",), sobject=SOBJECT,
            recipe_readable=True, claim_readable=True, claim_kind="value-claim",
            asserted_fields=frozenset(), error_code=G._PICKLIST_ERROR_CODE,
            error_fields=("Loan_Type__c",), s1_facts=facts, s1_seq=at_seq)
        assert G.classify(inp).verdict == "DERIVED"
        inp.field_changes = {"Loan_Type__c": "Personal"}
        assert G.classify(inp).grounding["reason"] == "chosen_picklist_value"
    finally:
        with _conn() as conn:
            conn.execute(text("DELETE FROM picklist_value_details WHERE picklist_value_set_entity_id = CAST(:p AS uuid)"), {"p": str(pvs)})
            conn.execute(text("DELETE FROM field_details WHERE object_entity_id = CAST(:o AS uuid)"), {"o": str(obj)})
            conn.execute(text("DELETE FROM entities WHERE connected_org_id = CAST(:o AS uuid)"), {"o": str(org)})
            conn.execute(text("DELETE FROM logical_versions WHERE connected_org_id = CAST(:o AS uuid)"), {"o": str(org)})
            conn.execute(text("DELETE FROM connected_orgs WHERE id = CAST(:o AS uuid)"), {"o": str(org)})


# ---------------------------------------------------------------------------
# h. the consolidated settings page (one home; every flag change audited)
# ---------------------------------------------------------------------------

def _superadmin_client():
    import jwt as _jwt
    from primeqa.app import create_app
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()
    secret = os.environ.get("JWT_SECRET")
    now = datetime.now(timezone.utc)
    tok = _jwt.encode({"sub": "1", "tenant_id": TENANT, "email": "a@x",
                       "role": "superadmin", "full_name": "gate test",
                       "iat": now, "exp": now.timestamp() + 600},
                      secret, algorithm="HS256")
    client.set_cookie("access_token", tok)
    client.set_cookie("csrf_token", "gate-csrf")
    return client


def test_h_settings_page_is_the_one_home_and_audits_each_flag(world):
    client = _superadmin_client()
    html = client.get("/settings/agent").get_data(as_text=True)
    assert "repair_gate_apply_enabled" in html and "repair_auto_apply" in html
    assert "agent_enabled" in html and "trust_threshold" not in html
    # the llm-usage page no longer carries the checkbox — only the note.
    # (Asserted on the template source: that page also reads llm_usage_log /
    # llm_models, which the scratch DB does not carry.)
    from pathlib import Path
    usage = Path("primeqa/templates/settings/llm_usage.html").read_text()
    assert 'name="repair_auto_apply"' not in usage
    assert 'href="/settings/agent"' in usage
    from sqlalchemy import create_engine
    pub = create_engine(DB)
    with pub.connect() as c:
        before = c.execute(text(
            "SELECT COUNT(*) FROM public.activity_log WHERE tenant_id = :t AND "
            "entity_type IN ('tenant_repair_gate_apply_enabled', "
            "'tenant_repair_auto_apply', 'tenant_agent_enabled')"),
            {"t": TENANT}).scalar()
    try:
        r = client.post("/settings/agent", data={
            "agent_enabled": "1", "repair_gate_apply_enabled": "1",
            "max_fix_attempts_per_run": "2", "csrf_token": "gate-csrf"})
        assert r.status_code in (302, 303)
        with pub.connect() as c:
            row = c.execute(text(
                "SELECT agent_enabled, repair_auto_apply, repair_gate_apply_enabled, "
                "max_fix_attempts_per_run FROM public.tenant_agent_settings "
                "WHERE tenant_id = :t"), {"t": TENANT}).first()
            after = c.execute(text(
                "SELECT entity_type, details FROM public.activity_log WHERE "
                "tenant_id = :t AND entity_type = 'tenant_repair_gate_apply_enabled' "
                "ORDER BY id DESC LIMIT 1"), {"t": TENANT}).first()
            n_after = c.execute(text(
                "SELECT COUNT(*) FROM public.activity_log WHERE tenant_id = :t AND "
                "entity_type IN ('tenant_repair_gate_apply_enabled', "
                "'tenant_repair_auto_apply', 'tenant_agent_enabled')"),
                {"t": TENANT}).scalar()
        assert tuple(row) == (True, False, True, 2)
        assert n_after == before + 1                      # exactly the flag that moved
        assert after[1]["old"] is False and after[1]["new"] is True
        assert after[1]["surface"] == "/settings/agent"
    finally:
        _settings(agent_enabled=True, repair_auto_apply=False,
                  repair_gate_apply_enabled=False, max_fix_attempts_per_run=3)
