"""Per-environment release decision (multi-org closure, Slice A / "3e").

The decision engine's evidence assembly gains an env scope; the wrapper
computes one verdict per environment with evidence in >=2 envs and rolls up
worst-of. These tests cover the seams on the local-PG session:

  - ``_environments_with_evidence`` enumerates the claims' distinct run envs.
  - ``_assemble_claim_evidence(environment_id=N)`` counts ONLY env N's runs
    (latest-run, superseded-newer, flake window all scoped).
  - env=None is the identity: byte-equal rows vs the pre-parameter call shape.
  - the per-env compute + ``_rollup_env_decisions`` end-to-end: green org A +
    red org B ⇒ A card GO-ish, B card NO-GO, roll-up NO-GO with annotated
    blocking entries.
"""
from __future__ import annotations

from sqlalchemy import text

from primeqa.intelligence.substrate_decision import (
    _assemble_claim_evidence,
    _claim_test_ids,
    _environments_with_evidence,
    _rollup_env_decisions,
    compute_substrate_decision,
)
from primeqa.test_representation import SemanticTransactionCoordinator

from .test_substrate_decision_evidence import _approved_claim, _seed_run

ENV_A, ENV_B = 7, 9


def _two_env_release(session):
    """One approved claim linked to MO-1: passed in env A, failed in env B
    (the newer run). A second claim passed in BOTH envs."""
    coord = SemanticTransactionCoordinator()
    c1 = _approved_claim(session, coord, key="MO-1")
    _seed_run(session, claim_test_id=c1.test_id, outcome="passed",
              finished_at="2026-06-01T10:00:00+00:00",
              claim_version_seq=c1.version_seq, environment_id=ENV_A)
    _seed_run(session, claim_test_id=c1.test_id, outcome="failed",
              finished_at="2026-06-02T10:00:00+00:00",
              claim_version_seq=c1.version_seq, environment_id=ENV_B)
    c2 = _approved_claim(session, coord, key="MO-1")
    for env in (ENV_A, ENV_B):
        _seed_run(session, claim_test_id=c2.test_id, outcome="passed",
                  finished_at="2026-06-03T10:00:00+00:00",
                  claim_version_seq=c2.version_seq, environment_id=env)
    session.flush()
    return c1, c2


def test_environments_with_evidence_enumerates_run_envs(session):
    _two_env_release(session)
    test_ids, _ = _claim_test_ids(session, ["MO-1"])
    assert _environments_with_evidence(session, test_ids) == [ENV_A, ENV_B]
    assert _environments_with_evidence(session, []) == []


def test_env_scoped_assembly_counts_only_that_envs_runs(session):
    c1, _ = _two_env_release(session)

    rows_a = _assemble_claim_evidence(session, ["MO-1"], environment_id=ENV_A)
    row1_a = next(r for r in rows_a if r["test_id"] == str(c1.test_id))
    # env A's latest for c1 is the older PASSED run — env B's newer failure
    # must not leak in.
    assert row1_a["latest_run"]["outcome"] == "passed"
    assert row1_a["recent_outcomes"] == ["passed"]

    rows_b = _assemble_claim_evidence(session, ["MO-1"], environment_id=ENV_B)
    row1_b = next(r for r in rows_b if r["test_id"] == str(c1.test_id))
    assert row1_b["latest_run"]["outcome"] == "failed"
    assert row1_b["recent_outcomes"] == ["failed"]


def test_env_none_assembly_is_the_identity(session):
    _two_env_release(session)
    before_shape = _assemble_claim_evidence(session, ["MO-1"])
    explicit = _assemble_claim_evidence(session, ["MO-1"], environment_id=None,
                                        connected_org_id=None, manual_q=None)
    assert before_shape == explicit
    # the unscoped read still counts the tenant-wide latest (env B's failure)
    outcomes = {r["latest_run"]["outcome"] for r in before_shape}
    assert "failed" in outcomes


def test_per_env_compute_and_rollup_worst_of(session):
    _two_env_release(session)
    env_decisions = []
    for env in (ENV_A, ENV_B):
        evidence = _assemble_claim_evidence(session, ["MO-1"],
                                            environment_id=env)
        d = compute_substrate_decision(evidence)
        env_decisions.append({"environment_id": env,
                              "connected_org_id": None, **d})

    d_a = next(d for d in env_decisions if d["environment_id"] == ENV_A)
    d_b = next(d for d in env_decisions if d["environment_id"] == ENV_B)
    assert d_a["metrics"]["pass_rate"] == 100.0
    assert d_b["recommendation"] == "no_go"          # 1 of 2 failed < 95%

    rolled = _rollup_env_decisions(env_decisions)
    assert rolled["recommendation"] == "no_go"       # worst wins
    assert rolled["applicable"] is True
    assert rolled["metrics"]["environments"] == [ENV_A, ENV_B]
    assert rolled["metrics"]["claim_count"] == 2     # shared claims, not summed
    assert rolled["metrics"]["pass_rate"] == d_b["metrics"]["pass_rate"]
    # blocking entries carry which org they block
    assert rolled["blocking"], "the failing env must contribute blockers"
    assert all(b["environment_id"] == ENV_B for b in rolled["blocking"])
    # per-env reasoning lines — one per env, fail for B
    env_lines = [r for r in rolled["reasoning"] if r["check"] == "environment"]
    assert len(env_lines) == 2
    assert any(r["status"] == "fail" and str(ENV_B) in r["detail"]
               for r in env_lines)
    # criteria AND: pass_rate fails overall because env B fails it
    assert rolled["criteria_met"]["pass_rate"] is False
    assert rolled["environments"] == env_decisions


def test_org_bound_staleness_uses_the_orgs_own_seq(session):
    """Two orgs synced to different seqs: grounding evaluated at org A's
    latest must NOT read stale just because org B synced later (the D-292
    residual this slice fixes)."""
    from primeqa.evolution import persist_grounding_validity
    from .test_substrate_decision_evidence import _gv

    session.execute(text("SELECT set_config('app.tenant_id', '1', false)"))
    org_a = session.execute(text(
        "INSERT INTO connected_orgs (org_type, sf_instance_url, label, "
        "environment_id) VALUES ('sandbox', 'https://a.example', 'A', :e) "
        "RETURNING CAST(id AS text)"), {"e": ENV_A}).scalar()
    org_b = session.execute(text(
        "INSERT INTO connected_orgs (org_type, sf_instance_url, label, "
        "environment_id) VALUES ('sandbox', 'https://b.example', 'B', :e) "
        "RETURNING CAST(id AS text)"), {"e": ENV_B}).scalar()
    seq_a = session.execute(text(
        "INSERT INTO logical_versions (version_name, version_type, "
        "connected_org_id) VALUES ('mo-a', 'genesis', CAST(:o AS uuid)) "
        "RETURNING version_seq"), {"o": org_a}).scalar()
    session.execute(text(
        "INSERT INTO logical_versions (version_name, version_type, "
        "connected_org_id) VALUES ('mo-b', 'genesis', CAST(:o AS uuid))"),
        {"o": org_b})                          # org B's newer seq = tenant MAX

    coord = SemanticTransactionCoordinator()
    cr = _approved_claim(session, coord, key="MO-2")
    persist_grounding_validity(
        session, connected_org_id=org_a, test_id=cr.test_id,
        version_seq=cr.version_seq,
        evaluated_at_version_seq=seq_a, validity=_gv(overall="intact"))
    _seed_run(session, claim_test_id=cr.test_id, outcome="passed",
              finished_at="2026-06-01T10:00:00+00:00",
              claim_version_seq=cr.version_seq, environment_id=ENV_A)
    session.flush()

    # org-blind read: anchored to tenant MAX (org B's seq) → falsely stale
    [blind] = _assemble_claim_evidence(session, ["MO-2"])
    assert blind["grounding"]["stale"] is True
    # org-bound read: anchored to org A's own latest → current
    [bound] = _assemble_claim_evidence(session, ["MO-2"],
                                       environment_id=ENV_A,
                                       connected_org_id=org_a)
    assert bound["grounding"]["stale"] is False
