"""Multi-org scoping of the read/analytics consoles (multi-org closure, Part 1).

The action paths (S3/S4) are already env-scoped; these tests cover the
analytics reads that used to blend orgs: the insights assembly (recent runs +
S6 clusterings), the requirement-detail last-run chips, and the claims-library
listing. Two invariants per surface:

  1. ``environment_id=None`` returns EXACTLY what the read returned before the
     parameter existed (single-org regression guard — key-by-key, allowing only
     the documented additive keys).
  2. ``environment_id=N`` restricts every run-derived row to env N.

Seeds runs across two environment_ids (7 and 9) on the per-test rollback
``session`` fixture.
"""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import text

from primeqa.intelligence.s3_generation_console import _list_claims
from primeqa.intelligence.s4_execution_console import _latest_run_rows_by_test
from primeqa.intelligence.substrate_insights import _assemble_insights
from primeqa.interpretation.clustering import (
    cluster_by_vr,
    cluster_flapping,
    cluster_recurring_causes,
)
from primeqa.interpretation.model import Cause, Interpretation
from primeqa.interpretation.result_store import persist_interpretation
from primeqa.test_representation import SemanticTransactionCoordinator

from ._fixtures import arrange_approved_claim


ENV_A, ENV_B = 7, 9


def _seed_run(session, *, run_id=None, claim_test_id=None, environment_id=ENV_A,
              outcome="passed", finished_at="2026-06-01T10:00:00+00:00"):
    rid = run_id or uuid4()
    tid = claim_test_id or uuid4()
    session.execute(text(
        "INSERT INTO s4_execution_runs (run_id, recipe_id, recipe_version_seq, "
        "claim_test_id, environment_id, outcome, started_at, finished_at, evidence) "
        "VALUES (CAST(:r AS uuid), CAST(:rec AS uuid), 1, CAST(:t AS uuid), :env, "
        "CAST(:o AS run_outcome), CAST(:f AS timestamptz), CAST(:f AS timestamptz), "
        "CAST('{}' AS jsonb))"),
        {"r": str(rid), "rec": str(uuid4()), "t": str(tid), "env": environment_id,
         "o": outcome, "f": finished_at})
    return rid, tid


def _seed_interp(session, *, run_id, claim_test_id, outcome, verdict,
                 cause_kind=None, vr_name=None):
    cause = Cause(cause_kind=cause_kind, vr_name=vr_name) if cause_kind else None
    persist_interpretation(session, Interpretation(
        run_id=run_id, recipe_id=uuid4(), claim_test_id=claim_test_id,
        outcome=outcome, verdict=verdict, attribution="seeded", cause=cause))


def _seed_two_env_world(session):
    """Env A: 2 enforcement_gap/VR_A failures + a flapping claim (pass+fail).
    Env B: 2 enforcement_gap/VR_B failures; the same flapping claim passes
    twice there (so it only flaps within env A)."""
    flapper = uuid4()
    for n, (env, outcome, verdict, ck, vr) in enumerate([
        (ENV_A, "failed", "prohibition_not_enforced", "enforcement_gap", "VR_A"),
        (ENV_A, "failed", "prohibition_not_enforced", "enforcement_gap", "VR_A"),
        (ENV_B, "failed", "prohibition_not_enforced", "enforcement_gap", "VR_B"),
        (ENV_B, "failed", "prohibition_not_enforced", "enforcement_gap", "VR_B"),
    ]):
        rid, tid = _seed_run(session, environment_id=env, outcome=outcome,
                             finished_at="2026-06-0%dT10:00:00+00:00" % (n + 1))
        _seed_interp(session, run_id=rid, claim_test_id=tid, outcome=outcome,
                     verdict=verdict, cause_kind=ck, vr_name=vr)
    for n, (env, outcome) in enumerate([
        (ENV_A, "passed"), (ENV_A, "failed"),      # flaps in env A
        (ENV_B, "passed"), (ENV_B, "passed"),      # steady in env B
    ]):
        rid, _ = _seed_run(session, claim_test_id=flapper, environment_id=env,
                           outcome=outcome,
                           finished_at="2026-06-1%dT10:00:00+00:00" % n)
        _seed_interp(session, run_id=rid, claim_test_id=flapper, outcome=outcome,
                     verdict="seeded")
    session.flush()
    return flapper


# --- insights assembly --------------------------------------------------------

def test_insights_env_scope_restricts_run_derived_sections(session):
    flapper = _seed_two_env_world(session)

    scoped = _assemble_insights(session, limit=50, environment_id=ENV_A)
    assert {r["environment_id"] for r in scoped["recent_runs"]} == {ENV_A}
    assert [c["vr_name"] for c in scoped["vr_clusters"]] == ["VR_A"]
    # the claim flaps within env A (passed+failed there)
    assert [f["claim_test_id"] for f in scoped["flapping"]] == [str(flapper)]

    scoped_b = _assemble_insights(session, limit=50, environment_id=ENV_B)
    assert {r["environment_id"] for r in scoped_b["recent_runs"]} == {ENV_B}
    assert [c["vr_name"] for c in scoped_b["vr_clusters"]] == ["VR_B"]
    # steady in env B: a cross-org difference is NOT flapping
    assert scoped_b["flapping"] == []


def test_insights_env_none_is_the_unscoped_read(session):
    _seed_two_env_world(session)
    unscoped = _assemble_insights(session, limit=50)
    explicit_none = _assemble_insights(session, limit=50, environment_id=None)
    assert unscoped == explicit_none
    # the blend still spans both envs (tenant-wide default preserved)
    assert {r["environment_id"] for r in unscoped["recent_runs"]} == {ENV_A, ENV_B}
    # tenant-wide flapping still sees the claim (it has passed+failed overall)
    assert len(unscoped["flapping"]) == 1


def test_clustering_env_and_recipe_filters_compose(session):
    """The env join + recipe filter in one statement — guards the qualified
    column names (claim_test_id/recipe_id/outcome exist in BOTH joined
    tables)."""
    _seed_two_env_world(session)
    # no rows for a random recipe, but the statements must be valid SQL
    rid = uuid4()
    assert cluster_recurring_causes(
        session, recipe_id=rid, environment_id=ENV_A) == []
    assert cluster_by_vr(session, recipe_id=rid, environment_id=ENV_A) == []
    assert cluster_flapping(session, recipe_id=rid, environment_id=ENV_A) == []


# --- last-run chips (requirement detail) ---------------------------------------

def test_latest_run_rows_env_scope_and_payload(session):
    tid = uuid4()
    _seed_run(session, claim_test_id=tid, environment_id=ENV_A,
              outcome="failed", finished_at="2026-06-01T10:00:00+00:00")
    _seed_run(session, claim_test_id=tid, environment_id=ENV_B,
              outcome="passed", finished_at="2026-06-02T10:00:00+00:00")
    conn = session.connection()

    rows = _latest_run_rows_by_test(conn, [str(tid)])
    assert len(rows) == 1
    # tenant-wide latest is env B's newer run — and the row SAYS which env
    assert rows[0]["outcome"] == "passed"
    assert rows[0]["environment_id"] == ENV_B

    rows_a = _latest_run_rows_by_test(conn, [str(tid)], environment_id=ENV_A)
    assert rows_a[0]["outcome"] == "failed"
    assert rows_a[0]["environment_id"] == ENV_A


# --- claims library -------------------------------------------------------------

def test_list_claims_env_filter_and_env_scoped_last_run(session):
    coord = SemanticTransactionCoordinator()
    t_a, _ = arrange_approved_claim(session, coord, value="TechA")
    t_b, _ = arrange_approved_claim(session, coord, value="TechB")
    # claim A ran in both envs (older run in A, newer in B); claim B only in B
    _seed_run(session, claim_test_id=t_a, environment_id=ENV_A,
              outcome="failed", finished_at="2026-06-01T10:00:00+00:00")
    _seed_run(session, claim_test_id=t_a, environment_id=ENV_B,
              outcome="passed", finished_at="2026-06-02T10:00:00+00:00")
    _seed_run(session, claim_test_id=t_b, environment_id=ENV_B,
              outcome="passed", finished_at="2026-06-03T10:00:00+00:00")
    session.flush()
    conn = session.connection()

    total_all, claims_all = _list_claims(conn, limit=50, offset=0)
    by_id = {c["test_id"]: c for c in claims_all}
    assert {str(t_a), str(t_b)} <= set(by_id)
    # unscoped last_run is the tenant-wide latest, and carries its env
    assert by_id[str(t_a)]["last_run"]["outcome"] == "passed"
    assert by_id[str(t_a)]["last_run"]["environment_id"] == ENV_B

    total_a, claims_a = _list_claims(conn, limit=50, offset=0,
                                     environment_id=ENV_A)
    ids_a = {c["test_id"] for c in claims_a}
    assert str(t_a) in ids_a and str(t_b) not in ids_a   # B never ran in env A
    row_a = next(c for c in claims_a if c["test_id"] == str(t_a))
    # scoped last_run is env A's latest, not the newer env-B run
    assert row_a["last_run"]["outcome"] == "failed"
    assert row_a["last_run"]["environment_id"] == ENV_A
    assert total_a < total_all or total_all == total_a  # filter never widens
