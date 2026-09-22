"""Integration: the D-215.1 repair spine round-trip — triage over seeded S6
rows -> proposals -> decide (reject + approve-rerun) on local PG.

Round 4 (AUD-045): two product rules had grown around this test since it was
written, and its world showed neither. (1) The repair gate SWITCH (D-236 /
Step A): apply actions are dormant unless ``tenant_agent_settings.
repair_gate_apply_enabled`` — a public table the governance harness never
carried (it migrates the tenant schema only), so ``_repair_settings`` fell to
its dormant defaults and every approve was refused "the repair gate switch is
off". (2) AUD-013 / D-494: a re-run executes under ITS run's recorded plan and
is refused without one. The test now shims the three public tables the switch
needs (from the ORM, so the shape is the product's), turns the switch on for
the harness tenant, and seeds its runs with a plan id. The product is right on
both counts; the world was stale."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import text

from primeqa.intelligence.repair_agent import (
    decide_proposal,
    list_proposals,
    triage_new_failures,
)

from .conftest import TEST_TENANT_ID


PLAN_ID = "00000000-0000-4000-8000-00000000d494"   # AUD-013: a re-run runs under its run's plan


def _switch_on_the_repair_gate():
    """Shim the public tables the repair gate switch reads (the harness
    carries the tenant schema only) and turn the switch on for the tenant."""
    import os
    import primeqa.db as db
    from primeqa.core.models import TenantAgentSettings
    if db.engine is None:                     # the harness never starts the app
        db.init_db(os.environ["DATABASE_URL"])   # (the conftest points this at the harness)
    engine = db.engine
    # the settings table's COLUMNS from the ORM (so the reader's SELECT finds
    # every mapped column) without its foreign keys (users / tenants are not
    # in the harness either); tenants gets the two columns the row needs
    def _default(c):
        arg = getattr(c.server_default, "arg", None)
        if c.server_default is None:
            return ""
        if isinstance(arg, str):
            lit = arg if arg in ("true", "false") or arg.isdigit() else f"'{arg}'"
            return f" DEFAULT {lit}"
        return " DEFAULT now()"                 # func.now() on the timestamps
    cols = ", ".join(
        f"{c.name} {c.type.compile(dialect=engine.dialect)}"
        + ("" if c.nullable else " NOT NULL") + _default(c)
        for c in TenantAgentSettings.__table__.columns)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS tenants (id INTEGER PRIMARY KEY, name TEXT, slug TEXT)"))
        conn.execute(text(f"CREATE TABLE IF NOT EXISTS tenant_agent_settings ({cols}, PRIMARY KEY (tenant_id))"))

        conn.execute(text(
            "INSERT INTO tenants (id, name, slug) VALUES (:t, 'harness', 'harness') "
            "ON CONFLICT (id) DO NOTHING"), {"t": TEST_TENANT_ID})
        conn.execute(text(
            "INSERT INTO tenant_agent_settings (tenant_id, agent_enabled, repair_gate_apply_enabled) "
            "VALUES (:t, true, true) "
            "ON CONFLICT (tenant_id) DO UPDATE SET repair_gate_apply_enabled = true, agent_enabled = true"),
            {"t": TEST_TENANT_ID})


def _seed_run(conn, *, outcome, verdict, cause_kind=None, env=59):
    run_id, recipe_id, claim_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    conn.execute(text(
        "INSERT INTO s4_execution_runs (run_id, recipe_id, recipe_version_seq, "
        "claim_test_id, environment_id, outcome, started_at, finished_at, evidence, plan_id) "
        "VALUES (:r, :rc, 1, :c, :e, :o, :t, :t, '{}'::jsonb, CAST(:p AS uuid))"),
        {"r": str(run_id), "rc": str(recipe_id), "c": str(claim_id),
         "e": env, "o": outcome, "t": now, "p": PLAN_ID})
    conn.execute(text(
        "INSERT INTO s6_interpretations (run_id, recipe_id, claim_test_id, "
        "outcome, verdict, detail, cause_kind) "
        "VALUES (:r, :rc, :c, :o, :v, '{}'::jsonb, :k)"),
        {"r": str(run_id), "rc": str(recipe_id), "c": str(claim_id),
         "o": outcome, "v": verdict, "k": cause_kind})
    return claim_id


def _clean(conn):
    conn.execute(text("DELETE FROM repair_proposals"))
    conn.execute(text("DELETE FROM s6_interpretations"))
    conn.execute(text("DELETE FROM s4_execution_runs"))
    conn.execute(text("DELETE FROM s4_execution_jobs"))


def test_triage_decide_round_trip(db_setup):
    from primeqa.semantic.connection import get_tenant_connection
    _switch_on_the_repair_gate()
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        _clean(conn)
        rerun_claim = _seed_run(conn, outcome="errored", verdict="not_evaluated")
        _seed_run(conn, outcome="failed", verdict="rejected_unasserted_reason",
                  cause_kind="other_vr_fired")
        _seed_run(conn, outcome="failed", verdict="prohibition_not_enforced")  # finding

    out = triage_new_failures(TEST_TENANT_ID)
    assert out["scanned"] == 3
    assert out["proposed"] == 2                       # the finding got nothing

    props = list_proposals(TEST_TENANT_ID)["proposals"]
    kinds = {p["proposal_kind"] for p in props}
    assert kinds == {"rerun", "regenerate_from_current_org"}

    # re-triage is idempotent (run-level NOT EXISTS guard)
    assert triage_new_failures(TEST_TENANT_ID)["proposed"] == 0

    # reject the regenerate; approve the rerun -> an S4 job appears
    regen = next(p for p in props if p["proposal_kind"] != "rerun")
    rerun = next(p for p in props if p["proposal_kind"] == "rerun")
    assert decide_proposal(TEST_TENANT_ID, regen["id"], approve=False)["status"] == "rejected"
    applied = decide_proposal(TEST_TENANT_ID, rerun["id"], approve=True, decided_by=1)
    assert applied["ok"] and applied["status"] == "applied"
    assert applied.get("s4_job_id")

    from primeqa.semantic.connection import get_tenant_connection as gtc
    with gtc(TEST_TENANT_ID) as conn:
        job = conn.execute(text(
            "SELECT test_id, environment_id FROM s4_execution_jobs "
            "WHERE id = :jid"), {"jid": applied["s4_job_id"]}).first()
        assert str(job.test_id) == str(rerun_claim)
        assert job.environment_id == 59
        # the ledger is stamped
        st = conn.execute(text(
            "SELECT status, decided_by FROM repair_proposals WHERE id = :pid"),
            {"pid": rerun["id"]}).first()
        assert st.status == "applied" and st.decided_by == 1
        # decided rows can't be re-decided
        assert not decide_proposal(TEST_TENANT_ID, rerun["id"], approve=True)["ok"]
        _clean(conn)
