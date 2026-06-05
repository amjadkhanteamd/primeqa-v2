"""Governance tests for the substrate-insights surface (D-155, Step 2 / slice 2.1).

The v1-owned bridge over the S6 + S8 read APIs (`primeqa/intelligence/
substrate_insights.py`). Seeds substrate rows via the persisters on the per-test
rollback `session` fixture, then asserts the pure `_assemble_insights` surfaces
them (interpretations + clustering + grounding), the empty case, the flattened
dict shape, and the bridge's best-effort degrade (`available=False`).
"""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import text

from primeqa.evolution import GroundingValidity, persist_grounding_validity
from primeqa.evolution.claim_grounding import ClaimGroundingResult
from primeqa.intelligence.substrate_insights import (
    _assemble_insights,
    get_substrate_insights,
)
from primeqa.interpretation.model import Cause, Interpretation
from primeqa.interpretation.result_store import persist_interpretation


def _interp(*, recipe_id, claim_test_id, outcome, verdict,
            cause_kind=None, vr_name=None) -> Interpretation:
    cause = Cause(cause_kind=cause_kind, vr_name=vr_name) if cause_kind else None
    return Interpretation(
        run_id=uuid4(), recipe_id=recipe_id, claim_test_id=claim_test_id,
        outcome=outcome, verdict=verdict, attribution="seeded", cause=cause)


def _gv(*, overall, claim_verdict="intact") -> GroundingValidity:
    """A minimal GroundingValidity (no recipe verdicts) — enough for the store
    round-trip the surface reads back."""
    return GroundingValidity(
        claim_grounding=ClaimGroundingResult(claim_verdict, reason=None, unresolved=()),
        recipe_verdicts=(), overall=overall)


def _seed_run(session, run_id, *, outcome, finished_at) -> None:
    """An s4_execution_runs row for a run_id — so the recency-base recent_runs
    read (s4 LEFT JOIN s6) surfaces it with the seeded verdict/cause."""
    session.execute(text(
        "INSERT INTO s4_execution_runs (run_id, recipe_id, recipe_version_seq, "
        "claim_test_id, environment_id, outcome, started_at, finished_at, evidence) "
        "VALUES (CAST(:r AS uuid), CAST(:rec AS uuid), 1, CAST(:t AS uuid), 7, "
        "CAST(:o AS run_outcome), CAST(:f AS timestamptz), CAST(:f AS timestamptz), "
        "CAST('{}' AS jsonb))"),
        {"r": str(run_id), "rec": str(uuid4()), "t": str(uuid4()),
         "o": outcome, "f": finished_at})


def _seed_full(session) -> None:
    """A coherent S6 + S8 set: 2 enforcement_gap / VR_A failures (→ a cause
    cluster + a vr cluster), a flapping claim_test (passed + failed on one CT),
    2 grounding verdicts — plus an s4_execution_runs row paired to each
    interpretation so the recency-base recent_runs read finds them."""
    rid = uuid4()
    interps = []
    for _ in range(2):                                    # enforcement_gap x2 / VR_A x2
        it = _interp(recipe_id=rid, claim_test_id=uuid4(), outcome="failed",
                     verdict="prohibition_not_enforced",
                     cause_kind="enforcement_gap", vr_name="VR_A")
        persist_interpretation(session, it)
        interps.append(it)
    ct = uuid4()                                          # flapping: same CT, 2 outcomes
    for outcome, verdict in [("passed", "asserted_metadata_present"),
                             ("failed", "asserted_metadata_absent")]:
        it = _interp(recipe_id=rid, claim_test_id=ct, outcome=outcome, verdict=verdict)
        persist_interpretation(session, it)
        interps.append(it)
    for n, it in enumerate(interps):                      # an S4 run per interpretation
        _seed_run(session, it.run_id, outcome=it.outcome,
                  finished_at="2026-06-0%dT10:00:00+00:00" % (n + 1))
    persist_grounding_validity(session, test_id=uuid4(), version_seq=1,
                               evaluated_at_version_seq=5, validity=_gv(overall="drifted"))
    persist_grounding_validity(session, test_id=uuid4(), version_seq=1,
                               evaluated_at_version_seq=5, validity=_gv(overall="broken"))
    session.flush()


def test_assemble_surfaces_seeded_rows(session):
    _seed_full(session)
    out = _assemble_insights(session, limit=50)
    assert out["available"] is True
    assert out["empty"] is False
    assert len(out["recent_runs"]) == 4
    assert [r["outcome"] for r in out["recent_runs"]][0] == "failed"   # newest-first (06-04)
    assert any(c["cause_kind"] == "enforcement_gap" and c["count"] == 2
               for c in out["cause_clusters"])
    assert any(c["vr_name"] == "VR_A" and c["count"] == 2
               for c in out["vr_clusters"])
    assert len(out["flapping"]) == 1
    assert set(out["flapping"][0]["outcomes"]) == {"passed", "failed"}
    assert {g["overall"] for g in out["grounding"]} == {"drifted", "broken"}
    # 4b drill-through: clusters surface their member run_ids (UUIDs as str)
    cc = next(c for c in out["cause_clusters"] if c["cause_kind"] == "enforcement_gap")
    assert len(cc["run_ids"]) == 2 and all(isinstance(x, str) for x in cc["run_ids"])
    assert out["flapping"][0]["run_ids"]              # flapping run links present


def test_assemble_empty_when_unseeded(session):
    out = _assemble_insights(session, limit=50)
    assert out["available"] is True
    assert out["empty"] is True
    assert out["recent_runs"] == [] and out["grounding"] == []
    assert out["cause_clusters"] == [] and out["vr_clusters"] == []
    assert out["flapping"] == []


def test_recent_runs_shape(session):
    it = _interp(recipe_id=uuid4(), claim_test_id=uuid4(), outcome="failed",
                 verdict="prohibition_not_enforced",
                 cause_kind="enforcement_gap", vr_name="VR_B")
    persist_interpretation(session, it)
    _seed_run(session, it.run_id, outcome="failed",
              finished_at="2026-06-01T10:00:00+00:00")
    session.flush()
    row = _assemble_insights(session, limit=50)["recent_runs"][0]
    assert row["outcome"] == "failed"
    assert row["verdict"] == "prohibition_not_enforced"
    assert row["cause"] == {"cause_kind": "enforcement_gap", "vr_name": "VR_B"}
    assert row["finished_at"][:10] == "2026-06-01"
    assert isinstance(row["run_id"], str)        # UUID flattened to str


def test_grounding_dict_shape(session):
    tid = uuid4()
    persist_grounding_validity(session, test_id=tid, version_seq=2,
                               evaluated_at_version_seq=9, validity=_gv(overall="drifted"))
    session.flush()
    g = _assemble_insights(session, limit=50)["grounding"][0]
    assert g["test_id"] == str(tid)
    assert (g["version_seq"], g["evaluated_at_version_seq"]) == (2, 9)
    assert (g["overall"], g["claim_verdict"]) == ("drifted", "intact")


def test_get_substrate_insights_best_effort_on_bad_tenant():
    # tenant_id <= 0 → get_tenant_connection raises in _resolve_schema_name →
    # the bridge degrades to available=False (never raises).
    out = get_substrate_insights(-1)
    assert out["available"] is False
    assert out["empty"] is True
    assert out["recent_runs"] == []
