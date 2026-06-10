"""Substrate decision evidence — theme #3 slice 1 (D-198).

Seeds claims + generated_from links + grounding + S4 runs (with and without
claim_version_seq) and asserts _assemble_claim_evidence applies the three
decision-grade correctness rules: version-correct run counting, NULL-seq
tolerance (version_unknown flag), and grounding staleness vs the current S1
version. Mirrors the test_release_substrate_console harness.
"""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import text

from primeqa.evolution import GroundingValidity, persist_grounding_validity
from primeqa.evolution.claim_grounding import ClaimGroundingResult
from primeqa.intelligence.substrate_decision import _assemble_claim_evidence
from primeqa.test_representation import SemanticTransactionCoordinator

from ._fixtures import empty_conditions, make_value_claim


def _gv(*, overall, claim_verdict="intact"):
    return GroundingValidity(
        claim_grounding=ClaimGroundingResult(
            claim_verdict, reason="subject_not_resolved", unresolved=()),
        recipe_verdicts=(), overall=overall)


def _seed_run(session, *, claim_test_id, outcome, finished_at,
              claim_version_seq=None):
    session.execute(text(
        "INSERT INTO s4_execution_runs (run_id, recipe_id, recipe_version_seq, "
        "claim_test_id, claim_version_seq, environment_id, outcome, started_at, "
        "finished_at, evidence) "
        "VALUES (CAST(:r AS uuid), CAST(:rec AS uuid), 1, CAST(:t AS uuid), :cvs, 7, "
        "CAST(:o AS run_outcome), CAST(:f AS timestamptz), CAST(:f AS timestamptz), "
        "CAST('{}' AS jsonb))"),
        {"r": str(uuid4()), "rec": str(uuid4()), "t": str(claim_test_id),
         "cvs": claim_version_seq, "o": outcome, "f": finished_at})


def _seed_s1_version(session, version_seq):
    session.execute(text(
        "INSERT INTO logical_versions (version_seq, version_name, version_type) "
        "VALUES (:s, :n, 'genesis') ON CONFLICT DO NOTHING"),
        {"s": version_seq, "n": f"v{version_seq}-{uuid4().hex[:6]}"})


def _approved_claim(session, coord, *, key):
    cr = coord.write_claim(
        session, actor="s3", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=make_value_claim(value="Tech"),
        semantic_conditions=empty_conditions())
    coord.link_requirement(
        session, actor="s3", test_id=cr.test_id,
        external_system="jira", external_key=key, link_kind="generated_from")
    coord.promote_claim_to_approved(
        session, actor="human", test_id=cr.test_id, version_seq=cr.version_seq)
    return cr


def test_superseded_version_run_is_excluded_and_flagged(session):
    # approved at seq N; an OLDER run at seq N and a NEWER run at seq N+1 —
    # the N run counts (version-correct, not blind recency); the newer
    # mismatching run flags superseded_newer_run.
    coord = SemanticTransactionCoordinator()
    cr = _approved_claim(session, coord, key="DEC-1")
    _seed_run(session, claim_test_id=cr.test_id, outcome="passed",
              finished_at="2026-06-01T10:00:00+00:00",
              claim_version_seq=cr.version_seq)
    _seed_run(session, claim_test_id=cr.test_id, outcome="failed",
              finished_at="2026-06-02T10:00:00+00:00",
              claim_version_seq=cr.version_seq + 1)        # superseded evidence
    session.flush()

    [row] = _assemble_claim_evidence(session, ["DEC-1"])
    assert row["approved_seq"] == cr.version_seq
    assert row["latest_run"]["outcome"] == "passed"        # NOT the newer failed
    assert row["latest_run"]["version_unknown"] is False
    assert row["superseded_newer_run"] is True
    assert row["never_run"] is False


def test_null_seq_run_counts_with_version_unknown(session):
    coord = SemanticTransactionCoordinator()
    cr = _approved_claim(session, coord, key="DEC-2")
    _seed_run(session, claim_test_id=cr.test_id, outcome="passed",
              finished_at="2026-06-01T10:00:00+00:00")     # NULL claim_version_seq
    session.flush()

    [row] = _assemble_claim_evidence(session, ["DEC-2"])
    assert row["latest_run"]["outcome"] == "passed"
    assert row["latest_run"]["version_unknown"] is True
    assert row["superseded_newer_run"] is False
    # no S1 version seeded in this test → staleness unknowable, grounding absent.
    assert row["grounding"] is None


def test_only_superseded_runs_means_never_run(session):
    coord = SemanticTransactionCoordinator()
    cr = _approved_claim(session, coord, key="DEC-3")
    _seed_run(session, claim_test_id=cr.test_id, outcome="passed",
              finished_at="2026-06-01T10:00:00+00:00",
              claim_version_seq=cr.version_seq + 5)        # only superseded evidence
    session.flush()

    [row] = _assemble_claim_evidence(session, ["DEC-3"])
    assert row["latest_run"] is None and row["never_run"] is True
    assert row["superseded_newer_run"] is True             # the warning still fires


def test_grounding_staleness_vs_current_s1_version(session):
    coord = SemanticTransactionCoordinator()
    cr = _approved_claim(session, coord, key="DEC-4")
    persist_grounding_validity(
        session, test_id=cr.test_id, version_seq=cr.version_seq,
        evaluated_at_version_seq=5, validity=_gv(overall="intact"))
    _seed_s1_version(session, 10)                          # current S1 = 10 > 5
    session.flush()

    [row] = _assemble_claim_evidence(session, ["DEC-4"])
    assert row["grounding"]["overall"] == "intact"
    assert row["grounding"]["stale"] is True
    assert row["grounding"]["evaluated_at_version_seq"] == 5


def test_grounding_fresh_when_evaluated_at_current(session):
    coord = SemanticTransactionCoordinator()
    cr = _approved_claim(session, coord, key="DEC-5")
    _seed_s1_version(session, 7)
    persist_grounding_validity(
        session, test_id=cr.test_id, version_seq=cr.version_seq,
        evaluated_at_version_seq=7, validity=_gv(overall="broken", claim_verdict="broken"))
    session.flush()

    [row] = _assemble_claim_evidence(session, ["DEC-5"])
    assert row["grounding"]["overall"] == "broken"
    assert row["grounding"]["stale"] is False


def test_unapproved_claim_counts_any_version_run(session):
    # no approved version → no reference seq: the latest run counts unfiltered
    # and grounding falls back to the latest verdict (the D-172 idiom).
    coord = SemanticTransactionCoordinator()
    cr = coord.write_claim(
        session, actor="s3", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=make_value_claim(value="Tech"),
        semantic_conditions=empty_conditions())
    coord.link_requirement(
        session, actor="s3", test_id=cr.test_id,
        external_system="jira", external_key="DEC-6", link_kind="generated_from")
    persist_grounding_validity(
        session, test_id=cr.test_id, version_seq=cr.version_seq,
        evaluated_at_version_seq=3, validity=_gv(overall="drifted"))
    _seed_run(session, claim_test_id=cr.test_id, outcome="errored",
              finished_at="2026-06-01T10:00:00+00:00",
              claim_version_seq=cr.version_seq)
    session.flush()

    [row] = _assemble_claim_evidence(session, ["DEC-6"])
    assert row["approved_seq"] is None
    assert row["latest_run"]["outcome"] == "errored"
    assert row["grounding"]["overall"] == "drifted"
    assert row["superseded_newer_run"] is False            # no reference seq


def test_unknown_key_yields_empty(session):
    assert _assemble_claim_evidence(session, ["NO-SUCH-KEY"]) == []


def test_wrapper_empty_keys_not_applicable():
    from primeqa.intelligence.substrate_decision import (
        get_release_substrate_decision,
    )
    out = get_release_substrate_decision(1, [])
    assert out["available"] is True and out["applicable"] is False


def test_wrapper_best_effort_bad_tenant():
    from primeqa.intelligence.substrate_decision import (
        get_release_substrate_decision,
    )
    assert get_release_substrate_decision(-1, ["X-1"])["available"] is False


# ---------------------------------------------------------------------------
# Seeded end-to-end (slices 1+2 over REAL rows): a release's claims with runs →
# the recommendation comes out right, both directions (D-198 slice 4).
# ---------------------------------------------------------------------------

def test_e2e_clean_evidence_yields_go(session):
    from primeqa.intelligence.substrate_decision import compute_substrate_decision
    coord = SemanticTransactionCoordinator()
    cr = _approved_claim(session, coord, key="E2E-GO")
    _seed_s1_version(session, 4)
    persist_grounding_validity(
        session, test_id=cr.test_id, version_seq=cr.version_seq,
        evaluated_at_version_seq=4, validity=_gv(overall="intact"))
    _seed_run(session, claim_test_id=cr.test_id, outcome="passed",
              finished_at="2026-06-10T09:00:00+00:00",
              claim_version_seq=cr.version_seq)
    session.flush()

    from datetime import datetime, timezone
    out = compute_substrate_decision(
        _assemble_claim_evidence(session, ["E2E-GO"]),
        now=datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc))
    assert out["recommendation"] == "go"
    assert out["metrics"] == {
        "claim_count": 1, "counted_runs": 1, "passed": 1, "failed": 0,
        "errored": 0, "never_run": 0, "quarantined": 0, "pass_rate": 100.0,
        "grounding": {"broken": 0, "drifted": 0, "stale": 0},
        "blockers": 0, "warnings": 0}
    assert out["risk"]["level"] == "low"


def test_e2e_broken_grounding_and_failed_run_yields_no_go(session):
    from primeqa.intelligence.substrate_decision import compute_substrate_decision
    coord = SemanticTransactionCoordinator()
    cr = _approved_claim(session, coord, key="E2E-NOGO")
    _seed_s1_version(session, 4)
    persist_grounding_validity(
        session, test_id=cr.test_id, version_seq=cr.version_seq,
        evaluated_at_version_seq=4,
        validity=_gv(overall="broken", claim_verdict="broken"))
    _seed_run(session, claim_test_id=cr.test_id, outcome="failed",
              finished_at="2026-06-10T09:00:00+00:00",
              claim_version_seq=cr.version_seq)
    session.flush()

    from datetime import datetime, timezone
    out = compute_substrate_decision(
        _assemble_claim_evidence(session, ["E2E-NOGO"]),
        now=datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc))
    assert out["recommendation"] == "no_go"
    checks = {r["check"]: r["status"] for r in out["reasoning"]}
    assert checks["pass_rate"] == "fail"
    assert checks["grounding_integrity"] == "fail"
    assert out["risk"]["level"] == "critical"
