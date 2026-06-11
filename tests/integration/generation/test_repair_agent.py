"""Integration: the D-215.1 repair spine round-trip — triage over seeded S6
rows -> proposals -> decide (reject + approve-rerun) on local PG."""
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


def _seed_run(conn, *, outcome, verdict, cause_kind=None, env=59):
    run_id, recipe_id, claim_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    conn.execute(text(
        "INSERT INTO s4_execution_runs (run_id, recipe_id, recipe_version_seq, "
        "claim_test_id, environment_id, outcome, started_at, finished_at, evidence) "
        "VALUES (:r, :rc, 1, :c, :e, :o, :t, :t, '{}'::jsonb)"),
        {"r": str(run_id), "rc": str(recipe_id), "c": str(claim_id),
         "e": env, "o": outcome, "t": now})
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
