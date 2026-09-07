"""Step B (LLD_STEP_B_STALENESS_PIN §e) — the decision-engine pin, DB-real on
the local-PG rollback session (tenant_1 at the substrate head).

The OLD outcome in every reproduction is OBSERVED by executing the pre-B read
shape beside the new path in the same test — never asserted from memory:

  1a. false STALE (the pin): org B ahead of org A; a release evidenced in A
      only. OLD pin = tenant MAX = B's seq → A's current grounding read
      stale → conditional_go. NEW: A's own sequence → current → go.
  1b. the silent None: A bound but never synced. OLD: pin None → stale None
      → nothing between this evidence and GO. NEW: cannot_determine with
      the never-synced sentence.
  1c. the REAL wrong GO: a claim with no approved version, grounding rows
      for both orgs at the same version — A intact, B BROKEN — org ids
      planted so A's uuid sorts higher; a release evidenced in B only.
      OLD: the unpinned org-less read returns A's intact row → GO.
      NEW: B's own row → no_go with the grounding blocker.
  2.  org-less logical_versions rows are never counted by the resolver
      (they WERE the tenant MAX).
  3.  org unbound → cannot_determine, never go, never no_go: zero
      evidence; one env with no connected_orgs row; a roll-up with one
      ungraded org (D9).
  4.  the 2+ env branch is byte-identical to the per-env compute it
      always ran, and its number is the bound model's own.
"""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import text

from primeqa.evolution import persist_grounding_validity, read_grounding_validity_bulk
from primeqa.intelligence.substrate_decision import (
    _assemble_claim_evidence,
    _decide_for_session,
    _rollup_env_decisions,
    compute_substrate_decision,
)
from primeqa.semantic.query import SemanticOrgModel, VersionNotFoundError
from primeqa.sync.readiness import resolve_current_sequence
from primeqa.test_representation import SemanticTransactionCoordinator

from ._fixtures import empty_conditions, make_value_claim
from .test_substrate_decision_evidence import _approved_claim, _gv, _seed_run

ENV_A, ENV_B = 59, 78          # env-59-shaped / env-78-shaped


def _tenant(session):
    session.execute(text("SELECT set_config('app.tenant_id', '1', false)"))


def _org(session, env, label, *, org_id=None):
    if org_id is None:
        return session.execute(text(
            "INSERT INTO connected_orgs (org_type, sf_instance_url, label, "
            "environment_id) VALUES ('sandbox', :u, :l, :e) "
            "RETURNING CAST(id AS text)"),
            {"u": f"https://{label}.example", "l": label, "e": env}).scalar()
    session.execute(text(
        "INSERT INTO connected_orgs (id, org_type, sf_instance_url, label, "
        "environment_id) VALUES (CAST(:i AS uuid), 'sandbox', :u, :l, :e)"),
        {"i": org_id, "u": f"https://{label}.example", "l": label, "e": env})
    return org_id


def _version(session, org, *, version_type="sync_run"):
    return session.execute(text(
        "INSERT INTO logical_versions (version_name, version_type, connected_org_id) "
        "VALUES (:n, :t, CAST(:o AS uuid)) RETURNING version_seq"),
        {"n": f"v-{uuid4().hex[:6]}", "t": version_type, "o": org}).scalar()


def _orgless_version(session, description):
    return session.execute(text(
        "INSERT INTO logical_versions (version_name, version_type, description) "
        "VALUES (:n, 'manual_checkpoint', :d) RETURNING version_seq"),
        {"n": f"surface-{uuid4().hex[:6]}", "d": description}).scalar()


def _decide(session, keys):
    return _decide_for_session(session, session.connection(), keys, None,
                               tenant_id=None)


# ---- 1a. the false STALE ------------------------------------------------

def test_1a_false_stale_old_pin_is_b_new_resolver_is_a(session):
    _tenant(session)
    org_a = _org(session, ENV_A, "A")
    org_b = _org(session, ENV_B, "B")
    seq_a = _version(session, org_a)
    seq_b = _version(session, org_b)                 # B synced AFTER A
    assert seq_b > seq_a
    coord = SemanticTransactionCoordinator()
    cr = _approved_claim(session, coord, key="SB-1A")
    persist_grounding_validity(
        session, connected_org_id=org_a, test_id=cr.test_id,
        version_seq=cr.version_seq, evaluated_at_version_seq=seq_a,
        validity=_gv(overall="intact"))                # current for A
    _seed_run(session, claim_test_id=cr.test_id, outcome="passed",
              finished_at="2026-09-06T10:00:00+00:00",
              claim_version_seq=cr.version_seq, environment_id=ENV_A)
    session.flush()

    # OLD (observed): the org-less model's pin is the tenant MAX = B's seq,
    # and A's current grounding compares stale against it.
    old_pin = SemanticOrgModel(session.connection()).current_version_seq()
    assert old_pin == seq_b and seq_a < old_pin

    # NEW: the 1-env branch resolves A through the seam → A's own sequence.
    out = _decide(session, ["SB-1A"])
    assert out["recommendation"] == "go", out["reasoning"]
    assert out["environments"][0]["connected_org_id"] == org_a
    assert out["sequence"]["current_seq"] == seq_a
    assert out["sequence"]["source"] == "org_current"
    [row] = _assemble_claim_evidence(session, ["SB-1A"], environment_id=ENV_A,
                                     connected_org_id=org_a)
    assert row["grounding"]["stale"] is False
    # and the stale grade the OLD pin implied, shown with the same row:
    assert row["grounding"]["evaluated_at_version_seq"] < old_pin


# ---- 1b. the silent None ------------------------------------------------

def test_1b_never_synced_org_is_cannot_determine_not_a_silent_go(session):
    _tenant(session)
    org_a = _org(session, ENV_A, "A")                # bound, NEVER synced
    coord = SemanticTransactionCoordinator()
    cr = _approved_claim(session, coord, key="SB-1B")
    persist_grounding_validity(
        session, connected_org_id=org_a, test_id=cr.test_id,
        version_seq=cr.version_seq, evaluated_at_version_seq=5,
        validity=_gv(overall="intact"))
    _seed_run(session, claim_test_id=cr.test_id, outcome="passed",
              finished_at="2026-09-06T10:00:00+00:00",
              claim_version_seq=cr.version_seq, environment_id=ENV_A)
    session.flush()

    # OLD (observed): the bound model raises → the engine swallowed it to
    # None → stale None → nothing stood between this evidence and GO.
    try:
        SemanticOrgModel(session.connection(), connected_org_id=org_a).current_version_seq()
        raise AssertionError("expected VersionNotFoundError")
    except VersionNotFoundError:
        pass
    rows = _assemble_claim_evidence(session, ["SB-1B"], environment_id=ENV_A,
                                    connected_org_id=org_a)
    as_if_current = [{**r, "sequence": {**r["sequence"], "state": "CURRENT",
                                        "current_seq": 5, "reason": None}}
                     for r in rows]
    assert compute_substrate_decision(as_if_current)["recommendation"] == "go"

    # NEW
    out = _decide(session, ["SB-1B"])
    assert out["recommendation"] == "cannot_determine"
    assert out["sequence"]["reason"] == "org_never_synced"
    assert "has never synced" in out["reasoning"][0]["detail"]
    assert rows[0]["grounding"]["stale"] is None


# ---- 1c. the REAL wrong GO ---------------------------------------------

def test_1c_the_real_wrong_go_cross_org_latest_verdict_uuid_tiebreak(session):
    _tenant(session)
    org_high = _org(session, ENV_A, "A", org_id="ffffffff-0000-4000-8000-00000000000a")
    org_low = _org(session, ENV_B, "B", org_id="00000000-0000-4000-8000-00000000000b")
    _version(session, org_high)
    _version(session, org_low)
    coord = SemanticTransactionCoordinator()
    cr = coord.write_claim(                         # NO approved version (D-172 idiom)
        session, actor="s3", test_id=None, archetype="data_behavior",
        claim_kind="value-claim", asserted_truth=make_value_claim(value="Tech"),
        semantic_conditions=empty_conditions())
    coord.link_requirement(session, actor="s3", test_id=cr.test_id,
                           external_system="jira", external_key="SB-1C",
                           link_kind="generated_from")
    persist_grounding_validity(
        session, connected_org_id=org_high, test_id=cr.test_id,
        version_seq=cr.version_seq, evaluated_at_version_seq=1,
        validity=_gv(overall="intact"))
    persist_grounding_validity(
        session, connected_org_id=org_low, test_id=cr.test_id,
        version_seq=cr.version_seq, evaluated_at_version_seq=1,
        validity=_gv(overall="broken", claim_verdict="broken"))
    _seed_run(session, claim_test_id=cr.test_id, outcome="passed",
              finished_at="2026-09-06T10:00:00+00:00",
              claim_version_seq=cr.version_seq, environment_id=ENV_B)
    session.flush()

    # OLD (observed): the org-less unpinned read returns org A's row for a
    # release whose evidence is org B's — the uuid tie-break.
    old = read_grounding_validity_bulk(session, [(cr.test_id, None)], connected_org_id=None)
    assert old[cr.test_id].overall == "intact"
    assert str(old[cr.test_id].connected_org_id) == org_high
    old_rows = [{**r, "grounding": {"overall": "intact", "stale": False,
                                    "evaluated_at_version_seq": 1},
                 "sequence": {**r["sequence"], "state": "CURRENT", "current_seq": 1,
                              "reason": None}}
                for r in _assemble_claim_evidence(session, ["SB-1C"], environment_id=ENV_B,
                                                  connected_org_id=org_low)]
    assert compute_substrate_decision(old_rows)["recommendation"] == "go"   # the wrong GO

    # NEW: the 1-env branch reads org B's OWN verdict.
    new = read_grounding_validity_bulk(session, [(cr.test_id, None)], connected_org_id=org_low)
    assert new[cr.test_id].overall == "broken"
    out = _decide(session, ["SB-1C"])
    assert out["recommendation"] == "no_go"
    assert out["criteria_met"]["grounding_integrity"] is False
    assert out["blocking"] and out["blocking"][0]["outcome"] == "grounding_broken"
    assert out["environments"][0]["connected_org_id"] == org_low


# ---- 2. org-less rows never counted -------------------------------------

def test_2_org_less_rows_are_never_the_current_sequence(session):
    _tenant(session)
    org_a = _org(session, ENV_A, "A")
    seq_a = _version(session, org_a)
    for _ in range(5):
        top = _orgless_version(session, "3A-5 declared Surface entities")
    assert top > seq_a
    r = resolve_current_sequence(session, connected_org_id=org_a)
    assert r.state == "CURRENT" and r.current_seq == seq_a and r.as_of is not None
    # the OLD pin (observed) WAS the org-less checkpoint
    assert SemanticOrgModel(session.connection()).current_version_seq() == top


# ---- 3. org unbound → cannot_determine, never go, never no_go ------------

def test_3_org_unbound_is_cannot_determine_never_go_never_no_go(session):
    _tenant(session)
    coord = SemanticTransactionCoordinator()

    # (i) zero evidence — nothing binds
    c0 = _approved_claim(session, coord, key="SB-3-ZERO")
    session.flush()
    out0 = _decide(session, ["SB-3-ZERO"])
    assert out0["recommendation"] == "cannot_determine"
    assert out0["environments"] == []
    assert out0["reasoning"][0]["check"] == "org_sequence"
    assert out0["sequence"]["reason"] == "org_unbound"
    assert {r["check"]: r["status"] for r in out0["reasoning"]}["has_runs"] == "fail"

    # (ii) one env with NO connected_orgs row
    c1 = _approved_claim(session, coord, key="SB-3-ONE")
    _seed_run(session, claim_test_id=c1.test_id, outcome="passed",
              finished_at="2026-09-06T10:00:00+00:00",
              claim_version_seq=c1.version_seq, environment_id=9001)
    session.flush()
    out1 = _decide(session, ["SB-3-ONE"])
    assert out1["recommendation"] == "cannot_determine"
    assert out1["environments"][0]["environment_id"] == 9001
    assert out1["environments"][0]["connected_org_id"] is None
    assert out1["sequence"]["reason"] == "org_unbound"
    assert out1["metrics"]["pass_rate"] == 100.0             # the fact stands

    # (iii) a roll-up: one provisioned green org + one unprovisioned env (D9)
    org_a = _org(session, ENV_A, "A")
    _version(session, org_a)
    c2 = _approved_claim(session, coord, key="SB-3-TWO")
    for env in (ENV_A, 9002):
        _seed_run(session, claim_test_id=c2.test_id, outcome="passed",
                  finished_at="2026-09-06T10:00:00+00:00",
                  claim_version_seq=c2.version_seq, environment_id=env)
    session.flush()
    out2 = _decide(session, ["SB-3-TWO"])
    assert out2["recommendation"] == "cannot_determine"
    by_env = {d["environment_id"]: d["recommendation"] for d in out2["environments"]}
    assert by_env == {ENV_A: "go", 9002: "cannot_determine"}

    for out in (out0, out1, out2):
        assert out["recommendation"] not in ("go", "conditional_go", "no_go")
        assert out["confidence"] == 0.0


# ---- 4. the 2+ env branch is byte-identical -----------------------------

def test_4_two_env_branch_is_byte_identical_to_the_per_env_compute(session):
    _tenant(session)
    orgs = {ENV_A: _org(session, ENV_A, "A"), ENV_B: _org(session, ENV_B, "B")}
    seqs = {env: _version(session, org) for env, org in orgs.items()}
    coord = SemanticTransactionCoordinator()
    c1 = _approved_claim(session, coord, key="SB-4")
    c2 = _approved_claim(session, coord, key="SB-4")
    for env, org in orgs.items():
        for c in (c1, c2):
            persist_grounding_validity(
                session, connected_org_id=org, test_id=c.test_id,
                version_seq=c.version_seq, evaluated_at_version_seq=seqs[env],
                validity=_gv(overall="intact"))
    _seed_run(session, claim_test_id=c1.test_id, outcome="passed",
              finished_at="2026-09-01T10:00:00+00:00",
              claim_version_seq=c1.version_seq, environment_id=ENV_A)
    _seed_run(session, claim_test_id=c1.test_id, outcome="failed",
              finished_at="2026-09-02T10:00:00+00:00",
              claim_version_seq=c1.version_seq, environment_id=ENV_B)
    for env in (ENV_A, ENV_B):
        _seed_run(session, claim_test_id=c2.test_id, outcome="passed",
                  finished_at="2026-09-03T10:00:00+00:00",
                  claim_version_seq=c2.version_seq, environment_id=env)
    session.flush()

    # the per-env compute the branch always ran, with the bound model's number
    expected = []
    for env, org in orgs.items():
        assert SemanticOrgModel(session.connection(),
                                connected_org_id=org).current_version_seq() == seqs[env]
        ev = _assemble_claim_evidence(session, ["SB-4"], environment_id=env,
                                      connected_org_id=org, manual_q={})
        assert all(r["sequence"]["current_seq"] == seqs[env] for r in ev)
        expected.append({"environment_id": env, "connected_org_id": org,
                         **compute_substrate_decision(ev)})
    expected = _rollup_env_decisions(expected)

    out = _decide(session, ["SB-4"])
    assert out.pop("available") is True
    assert out == expected
    assert out["recommendation"] == "no_go"          # B's failure, worst-of
    assert out["metrics"]["grounding"]["stale"] == 0  # each org against its own seq
