"""Integration: S6 cross-run clustering (D-116).

Deterministic aggregation over the ``s6_interpretations`` store — recurring
causes, the same VR across runs, flapping outcomes. Seeds interpretations
directly via ``persist_interpretation`` (the store has only logical FKs, so no
run-path arrangement is needed) and asserts the grouping. Also proves the persist
**promotion** — ``cause_kind`` / ``vr_name`` land in the typed columns the
clustering GROUP BYs read.

Reuses this package's per-test transactional ``session`` fixture; every test
scopes its queries to a test-local ``recipe_id`` so it is isolated from any other
seeded rows. The migration ``20260601_0010_s6_cause_promote`` (the promoted
columns + indexes) is applied by the package's ``alembic upgrade head`` setup.
"""
from __future__ import annotations

from uuid import uuid4

from primeqa.interpretation import clustering
from primeqa.interpretation.model import Cause, Interpretation
from primeqa.interpretation.result_store import S6Interpretation, persist_interpretation


def _interp(*, recipe_id, claim_test_id, outcome, verdict,
            cause_kind=None, vr_name=None) -> Interpretation:
    """A minimal Interpretation to seed; ``cause`` present iff cause_kind given."""
    cause = Cause(cause_kind=cause_kind, vr_name=vr_name) if cause_kind else None
    return Interpretation(
        run_id=uuid4(), recipe_id=recipe_id, claim_test_id=claim_test_id,
        outcome=outcome, verdict=verdict, attribution="x", cause=cause)


def _seed(session, interp) -> Interpretation:
    persist_interpretation(session, interp)
    return interp


# ---------------------------------------------------------------------------
# persist promotion — the typed cause columns the clustering reads
# ---------------------------------------------------------------------------

def test_persist_promotes_cause_columns(session):
    rid = uuid4()
    i_cause = _seed(session, _interp(
        recipe_id=rid, claim_test_id=uuid4(), outcome="failed",
        verdict="prohibition_not_enforced", cause_kind="enforcement_gap",
        vr_name="Lead.RequireReason"))
    i_none = _seed(session, _interp(
        recipe_id=rid, claim_test_id=uuid4(), outcome="passed",
        verdict="asserted_metadata_present"))
    session.flush()

    row_c = session.query(S6Interpretation).filter_by(run_id=i_cause.run_id).one()
    assert row_c.cause_kind == "enforcement_gap"
    assert row_c.vr_name == "Lead.RequireReason"
    # the JSONB cause stays the structured source of truth alongside the columns.
    assert row_c.detail["cause"]["cause_kind"] == "enforcement_gap"

    row_n = session.query(S6Interpretation).filter_by(run_id=i_none.run_id).one()
    assert row_n.cause_kind is None and row_n.vr_name is None   # no cause -> NULL


# ---------------------------------------------------------------------------
# recurring causes
# ---------------------------------------------------------------------------

def test_cluster_recurring_causes_groups_and_thresholds(session):
    rid = uuid4()
    for _ in range(3):                                          # enforcement_gap x3
        _seed(session, _interp(
            recipe_id=rid, claim_test_id=uuid4(), outcome="failed",
            verdict="prohibition_not_enforced", cause_kind="enforcement_gap",
            vr_name="VR_A"))
    _seed(session, _interp(                                     # vr_inactive x1
        recipe_id=rid, claim_test_id=uuid4(), outcome="failed",
        verdict="prohibition_not_enforced", cause_kind="vr_inactive", vr_name="VR_B"))
    session.flush()

    clusters = clustering.cluster_recurring_causes(session, recipe_id=rid, min_runs=2)
    # only enforcement_gap (3 >= 2) survives the threshold; vr_inactive (1) drops.
    assert [(c.cause_kind, c.count) for c in clusters] == [("enforcement_gap", 3)]
    assert len(clusters[0].run_ids) == 3

    # min_runs=1 surfaces both, most-frequent first.
    both = clustering.cluster_recurring_causes(session, recipe_id=rid, min_runs=1)
    assert [(c.cause_kind, c.count) for c in both] == [
        ("enforcement_gap", 3), ("vr_inactive", 1)]


def test_cluster_recurring_causes_recipe_filter_isolates(session):
    rid_a, rid_b = uuid4(), uuid4()
    for _ in range(2):
        _seed(session, _interp(
            recipe_id=rid_a, claim_test_id=uuid4(), outcome="failed",
            verdict="prohibition_not_enforced", cause_kind="enforcement_gap"))
    for _ in range(2):
        _seed(session, _interp(
            recipe_id=rid_b, claim_test_id=uuid4(), outcome="failed",
            verdict="prohibition_not_enforced", cause_kind="vr_formula_drift"))
    session.flush()

    a = clustering.cluster_recurring_causes(session, recipe_id=rid_a)
    assert [c.cause_kind for c in a] == ["enforcement_gap"]     # only rid_a's rows
    b = clustering.cluster_recurring_causes(session, recipe_id=rid_b)
    assert [c.cause_kind for c in b] == ["vr_formula_drift"]


# ---------------------------------------------------------------------------
# same VR across runs (with the distinct outcomes)
# ---------------------------------------------------------------------------

def test_cluster_by_vr_counts_and_outcomes(session):
    rid = uuid4()
    # VR_X implicated in 2 runs: one failed (enforcement_gap), one rejected for
    # an unasserted reason (other_vr_fired) — distinct outcomes carried.
    _seed(session, _interp(
        recipe_id=rid, claim_test_id=uuid4(), outcome="failed",
        verdict="prohibition_not_enforced", cause_kind="enforcement_gap", vr_name="VR_X"))
    _seed(session, _interp(
        recipe_id=rid, claim_test_id=uuid4(), outcome="failed",
        verdict="rejected_unasserted_reason", cause_kind="other_vr_fired", vr_name="VR_X"))
    # VR_Y only once -> dropped at min_runs=2.
    _seed(session, _interp(
        recipe_id=rid, claim_test_id=uuid4(), outcome="failed",
        verdict="prohibition_not_enforced", cause_kind="vr_inactive", vr_name="VR_Y"))
    session.flush()

    clusters = clustering.cluster_by_vr(session, recipe_id=rid, min_runs=2)
    assert len(clusters) == 1
    vr = clusters[0]
    assert vr.vr_name == "VR_X" and vr.count == 2
    assert vr.outcomes == ("failed",)                          # both runs failed
    assert len(vr.run_ids) == 2


# ---------------------------------------------------------------------------
# flapping — a claim_test whose runs disagree
# ---------------------------------------------------------------------------

def test_cluster_flapping_flags_disagreeing_runs(session):
    rid = uuid4()
    flapper, stable = uuid4(), uuid4()
    # the flapper: passed once, failed once (the same claim_test, two runs).
    _seed(session, _interp(recipe_id=rid, claim_test_id=flapper, outcome="passed",
                           verdict="prohibition_enforced"))
    _seed(session, _interp(recipe_id=rid, claim_test_id=flapper, outcome="failed",
                           verdict="prohibition_not_enforced", cause_kind="enforcement_gap"))
    # the stable one: passed twice -> not flapping.
    _seed(session, _interp(recipe_id=rid, claim_test_id=stable, outcome="passed",
                           verdict="prohibition_enforced"))
    _seed(session, _interp(recipe_id=rid, claim_test_id=stable, outcome="passed",
                           verdict="prohibition_enforced"))
    session.flush()

    clusters = clustering.cluster_flapping(session, recipe_id=rid)
    assert len(clusters) == 1                                   # only the flapper
    f = clusters[0]
    assert f.claim_test_id == flapper
    assert f.outcomes == ("failed", "passed")                  # both, sorted
    assert len(f.run_ids) == 2
