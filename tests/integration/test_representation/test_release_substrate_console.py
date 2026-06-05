"""Release substrate console bridge — governance (D-172, UI Area 5 slice 5a).

Seeds a claim + a generated_from requirement link (external_key 'REL-1') + an S8
grounding verdict + an S4 run, then asserts _assemble_release_substrate fans
requirement -> claim -> grounding + verdict and rolls up; plus the best-effort
wrapper (empty keys / bad tenant).
"""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import text

from primeqa.evolution import GroundingValidity, persist_grounding_validity
from primeqa.evolution.claim_grounding import ClaimGroundingResult
from primeqa.intelligence.release_substrate_console import (
    _assemble_release_substrate,
    get_release_substrate,
)
from primeqa.test_representation import SemanticTransactionCoordinator

from ._fixtures import empty_conditions, make_value_claim


def _gv(*, overall, claim_verdict="intact"):
    return GroundingValidity(
        claim_grounding=ClaimGroundingResult(
            claim_verdict, reason="subject_not_resolved", unresolved=()),
        recipe_verdicts=(), overall=overall)


def _seed_run(session, *, claim_test_id, outcome, finished_at):
    session.execute(text(
        "INSERT INTO s4_execution_runs (run_id, recipe_id, recipe_version_seq, "
        "claim_test_id, environment_id, outcome, started_at, finished_at, evidence) "
        "VALUES (CAST(:r AS uuid), CAST(:rec AS uuid), 1, CAST(:t AS uuid), 7, "
        "CAST(:o AS run_outcome), CAST(:f AS timestamptz), CAST(:f AS timestamptz), "
        "CAST('{}' AS jsonb))"),
        {"r": str(uuid4()), "rec": str(uuid4()), "t": str(claim_test_id),
         "o": outcome, "f": finished_at})


def test_assemble_release_substrate_rolls_up(session):
    coord = SemanticTransactionCoordinator()
    cr = coord.write_claim(
        session, actor="s3", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=make_value_claim(value="Tech"),
        semantic_conditions=empty_conditions())
    coord.link_requirement(
        session, actor="s3", test_id=cr.test_id,
        external_system="jira", external_key="REL-1", link_kind="generated_from")
    persist_grounding_validity(
        session, test_id=cr.test_id, version_seq=cr.version_seq,
        evaluated_at_version_seq=5, validity=_gv(overall="drifted", claim_verdict="broken"))
    _seed_run(session, claim_test_id=cr.test_id, outcome="failed",
              finished_at="2026-06-01T10:00:00+00:00")
    session.flush()

    out = _assemble_release_substrate(session, ["REL-1"])
    assert out["available"] is True and out["claim_count"] == 1
    assert out["grounding_counts"]["drifted"] == 1
    assert out["verdict_counts"]["failed"] == 1
    assert len(out["at_risk"]) == 1
    a = out["at_risk"][0]
    assert a["test_id"] == str(cr.test_id) and a["overall"] == "drifted"
    assert a["latest_outcome"] == "failed"


def test_assemble_empty_for_unknown_key(session):
    out = _assemble_release_substrate(session, ["NO-SUCH-REL"])
    assert out["claim_count"] == 0 and out["at_risk"] == []


def test_get_release_substrate_empty_keys():
    out = get_release_substrate(1, [])
    assert out["available"] is True and out["claim_count"] == 0


def test_get_release_substrate_best_effort_bad_tenant():
    assert get_release_substrate(-1, ["REL-1"])["available"] is False
