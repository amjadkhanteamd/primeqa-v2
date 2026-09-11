"""Step 5 — the Release Quality Policy (LLD_STEP_5_QUALITY_POLICY §g), DB-real
on the local harness (per-test rollback; tenant-only, so the planner's seams
are injected: environment reader, evidence envs, the plan, the rule levels).

  1. the store: the seed is a DRAFT with ten rules; a human activates it
     (audited); a new version retires the previous; rules change only on a
     draft; a USED version refuses edit (name / rule INSERT / UPDATE); DELETE
     refuses everywhere.
  2. the default policy over a clean stamped scope → GO with the six lines
     and "Nothing ungraded"; the decision cites the plan.
  3. a planted functional failure → BLOCK (no_go), rule 1 named.
  4. a waiver flips that item → ALLOW (go again); its expiry un-flips it.
  5. NEEDS_HUMAN pending → REVIEW (cannot_determine); waived → "N pending,
     waived by <actor> until <date>", never "resolved".
  6. ungraded → BLOCK → cannot_determine, never go / conditional; with a
     graded failure beside it → no_go (D9).
  7. §d: a production read-only target — plan-excluded data checks are NOT
     ungraded; an admitted inspection check never run there → rule 9 BLOCK.
  8. conformance: a level-A FAIL → BLOCK; a level-AA FAIL → CONDITIONAL.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from primeqa.evolution import persist_grounding_validity
from primeqa.execution_engine import planner as P
from primeqa.intelligence import quality_policy as qp
from primeqa.intelligence.quality_decision_console import grade_release
from primeqa.intelligence.quality_evidence import assemble
from primeqa.test_representation import SemanticTransactionCoordinator, claim_sets
from primeqa.test_representation.models.claims.ui.conformance_claim import ConformanceClaimBody
from primeqa.test_representation.models.conditions import SemanticConditionsBody
from primeqa.test_representation.models.surface import SurfaceNaturalKey, canonical_surface_key

from .test_step_2_contemporaneity import (
    _covered_ids, _materialise_entity, _org, _tenant, _version,
)
from .test_substrate_decision_evidence import _approved_claim, _gv, _seed_run

ENV_A, ENV_PROD = 59, 78
ENVS = {ENV_A: P.EnvInfo(59, "Sandbox", True, False, "full"),
        ENV_PROD: P.EnvInfo(78, "Prod1", False, True, "read_only")}
NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def _env(e):
    return ENVS.get(e)


def _seed(session):
    """The migration's draft seed, on this harness DB."""
    p = next(p for p in qp.list_policies(session) if p.name == "Plimsol default" and p.version == 1)
    return p


def _activate_seed(session):
    p = _seed(session)
    return qp.activate(session, policy_id=p.id, user_id=1) if p.status == "draft" else p


def _stamped(session, org, s1, *, key, outcome="passed", finished="2026-09-09T10:00:00+00:00", env=ENV_A):
    coord = SemanticTransactionCoordinator()
    cr = _approved_claim(session, coord, key=key)
    for eid, et in _covered_ids(session, cr.test_id):
        _materialise_entity(session, eid, et, org, valid_from=s1)
    persist_grounding_validity(session, connected_org_id=org, test_id=cr.test_id,
                               version_seq=cr.version_seq, evaluated_at_version_seq=s1,
                               validity=_gv(overall="intact"))
    if outcome is not None:
        _seed_run(session, claim_test_id=cr.test_id, outcome=outcome, finished_at=finished,
                  claim_version_seq=cr.version_seq, environment_id=env,
                  connected_org_id=org, org_version_seq=s1)
    return str(cr.test_id)


def _plan(pairs, exclusions=(), *, executed="2026-09-09T11:00:00+00:00"):
    return {"id": str(uuid4()), "executed_at": executed, "planned_at": executed,
            "inputs": {"targets_source": "declared"},
            "resolution": {"manifests": {"functional": {"pairs": [{"test_id": t, "environment_id": e, "recipes": []}
                                                                    for t, e in pairs], "job_count": len(pairs)}}},
            "exclusions": {"items": [{"kind": "claim", "ref": t, "reason": r, "environment_id": e}
                                     for t, e, r in exclusions]}}


def _grade(session, keys, *, targets=(ENV_A,), plan=None, now=NOW, **seams):
    return grade_release(session, tenant_id=None, release_id=None, keys=keys, targets=list(targets),
                         env_reader=_env, plan=plan, now=now, **seams)


def _line(d, axis):
    return next(ln for ln in d["evidence_lines"] if ln["axis"] == axis)


@pytest.fixture
def world(session):
    _tenant(session)
    org = _org(session, ENV_A, "A")
    s1 = _version(session, org)
    _activate_seed(session)
    key = f"S5-{uuid4().hex[:5]}"
    return {"org": org, "s1": s1, "key": key}


# 1 — the store ---------------------------------------------------------------

def test_seed_is_a_draft_and_a_human_activates_it(session):
    p = _seed(session)
    assert p.status == "draft" and len(p.rules) == 10 and p.created_by is None
    assert [r.condition for r in p.rules] == [
        "failure_present", "level_a_failed", "level_aa_failed", "needs_human_pending",
        "active_waiver_covers_item", "tool_drift_only", "environment_drift", "ungraded_present",
        "production_target_ungraded_on_metadata", "pass_rate_below"]
    assert p.rules[9].parameter == 95 and p.rules[4].scope == "item"
    assert qp.active_policy(session) is None
    with pytest.raises(qp.PolicyError):                       # a draft never grades
        qp.evaluate(p, {})
    a = qp.activate(session, policy_id=p.id, user_id=1)
    assert a.status == "active" and a.activated_by == 1 and qp.active_policy(session).id == p.id
    with pytest.raises(qp.PolicyError):                       # only a draft activates
        qp.activate(session, policy_id=p.id, user_id=1)
    # rules change only on a DRAFT
    with pytest.raises(DBAPIError):
        with session.begin_nested():
            session.execute(text("INSERT INTO quality_policy_rules (policy_id, position, axis, condition, effect) "
                                 "VALUES (CAST(:p AS uuid), 11, 'functional', 'failure_present', 'ALLOW')"), {"p": p.id})
    # the vocabulary is CHECKed at the table too
    d = qp.create_version(session, name="Plimsol default", rules=[qp.Rule(1, "functional", "failure_present", "BLOCK")],
                          created_by=1)
    assert d.version == 2 and d.status == "draft"
    with pytest.raises(DBAPIError):
        with session.begin_nested():
            session.execute(text("INSERT INTO quality_policy_rules (policy_id, position, axis, condition, effect) "
                                 "VALUES (CAST(:p AS uuid), 2, 'functional', 'level_a_failed', 'BLOCK')"), {"p": d.id})
    with pytest.raises(DBAPIError):
        with session.begin_nested():
            session.execute(text("INSERT INTO quality_policy_rules (policy_id, position, axis, condition, effect) "
                                 "VALUES (CAST(:p AS uuid), 2, 'functional', 'failure_present', 'VETO')"), {"p": d.id})
    # a new version activates and retires the previous
    v2 = qp.activate(session, policy_id=d.id, user_id=2)
    assert v2.status == "active" and qp.get_policy(session, p.id).status == "retired"
    assert qp.active_policy(session).version == 2
    for t in ("quality_policies", "quality_policy_rules"):
        with pytest.raises(DBAPIError):
            with session.begin_nested():
                session.execute(text(f"DELETE FROM {t}"))


def test_a_used_policy_version_refuses_edit(session):
    p = _activate_seed(session)
    qp.mark_used(session, policy_id=p.id)
    assert qp.get_policy(session, p.id).first_used_at is not None
    for stmt in ("UPDATE quality_policies SET name = 'x' WHERE id = CAST(:p AS uuid)",
                 "UPDATE quality_policies SET note = 'x' WHERE id = CAST(:p AS uuid)",
                 "UPDATE quality_policies SET status = 'draft' WHERE id = CAST(:p AS uuid)",
                 "UPDATE quality_policy_rules SET effect = 'ALLOW' WHERE policy_id = CAST(:p AS uuid) AND position = 1",
                 "INSERT INTO quality_policy_rules (policy_id, position, axis, condition, effect) "
                 "VALUES (CAST(:p AS uuid), 11, 'functional', 'failure_present', 'ALLOW')"):
        with pytest.raises(DBAPIError):
            with session.begin_nested():
                session.execute(text(stmt), {"p": p.id})
    # retirement is the one state change a used policy still admits
    r = qp.retire(session, policy_id=p.id, user_id=1)
    assert r.status == "retired"


# 2 — the default policy over a clean scope --------------------------------

def test_default_policy_over_a_clean_scope_is_go_with_six_lines_and_nothing_ungraded(session, world):
    w = world
    a = _stamped(session, w["org"], w["s1"], key=w["key"])
    b = _stamped(session, w["org"], w["s1"], key=w["key"])
    plan = _plan([(a, ENV_A), (b, ENV_A)])
    out = _grade(session, [w["key"]], plan=plan)
    assert out["ok"], out
    d = out["decision"]
    assert d["recommendation"] == "go" and d["policy"]["label"] == "Plimsol default v1"
    assert d["plan_id"] == plan["id"]
    assert [ln["axis"] for ln in d["evidence_lines"]] == list(qp.AXES)
    assert all(ln["rule"] == "no rule triggered" for ln in d["evidence_lines"])
    assert _line(d, "grading")["observed"] == "Nothing ungraded." and d["ungraded"] == []
    f = out["evidence"]["functional"]
    assert (f["claims"], f["counted"], f["passed"], f["pass_rate"]) == (2, 2, 2, 100.0)
    assert "Sandbox: 2 of 2 checks current" in _line(d, "environments")["observed"]
    assert plan["id"][:8] in _line(d, "environments")["observed"]


# 3 + 4 — a failure blocks; a waiver flips it; its expiry un-flips it ---------

def test_a_failure_blocks_and_a_waiver_flips_it_until_it_expires(session, world):
    w = world
    a = _stamped(session, w["org"], w["s1"], key=w["key"])
    bad = _stamped(session, w["org"], w["s1"], key=w["key"], outcome="failed")
    d = _grade(session, [w["key"]])["decision"]
    assert d["recommendation"] == "no_go"
    ln = _line(d, "functional")
    assert ln["effect"] == "BLOCK" and 1 in {r["position"] for r in ln["rules"]}
    assert "1 failed" in ln["observed"]
    # the waiver (ruling 4: reviewer + reason + expiry required)
    with pytest.raises(qp.PolicyError):
        qp.record_waiver(session, release_id=None, item_kind="claim", item_ref=bad, axis="functional",
                         reviewer_user_id=1, reason="", expires_at=NOW + timedelta(days=7), created_by=1)
    with pytest.raises(DBAPIError):                            # no expiry, no waiver — at the table too
        with session.begin_nested():
            session.execute(text("INSERT INTO quality_waivers (item_kind, item_ref, axis, reviewer_user_id, reason, created_by) "
                                 "VALUES ('claim', :i, 'functional', 1, 'x', 1)"), {"i": bad})
    wv = qp.record_waiver(session, release_id=None, item_kind="claim", item_ref=bad, axis="functional",
                          reviewer_user_id=2, reason="known flake, ticket QA-9", expires_at=NOW + timedelta(days=7),
                          created_by=1)
    assert wv["state"] == "active"
    d2 = _grade(session, [w["key"]])["decision"]
    assert d2["recommendation"] == "go"
    assert _line(d2, "waivers")["effect"] == "ALLOW" and _line(d2, "waivers")["rules"][0]["position"] == 5
    assert "by user 2 until" in _line(d2, "waivers")["observed"]
    assert _line(d2, "functional")["rule"] == "no rule triggered" and "1 waived" in _line(d2, "functional")["observed"]
    # its expiry un-flips it
    d3 = _grade(session, [w["key"]], now=NOW + timedelta(days=8))["decision"]
    assert d3["recommendation"] == "no_go" and "1 expired, not counted" in _line(d3, "waivers")["observed"]
    # revocation is the state change; DELETE refuses; the substance is immutable
    qp.revoke_waiver(session, waiver_id=wv["id"], user_id=1, reason="no longer accepted")
    assert qp.get_waiver(session, wv["id"])["state"] == "revoked"
    assert _grade(session, [w["key"]])["decision"]["recommendation"] == "no_go"
    for stmt in ("DELETE FROM quality_waivers WHERE id = CAST(:w AS uuid)",
                 "UPDATE quality_waivers SET reason = 'y' WHERE id = CAST(:w AS uuid)",
                 "UPDATE quality_waivers SET revoked_at = NULL, revoked_by = NULL WHERE id = CAST(:w AS uuid)"):
        with pytest.raises(DBAPIError):
            with session.begin_nested():
                session.execute(text(stmt), {"w": wv["id"]})


# 6 — ungraded → cannot_determine, never go / conditional; D9 ------------------

def test_ungraded_blocks_to_cannot_determine_and_a_graded_failure_beside_it_is_no_go(session, world):
    w = world
    _stamped(session, w["org"], w["s1"], key=w["key"])
    never = _stamped(session, w["org"], w["s1"], key=w["key"], outcome=None)
    d = _grade(session, [w["key"]])["decision"]
    assert d["recommendation"] == "cannot_determine" and d["because"] == "an ungraded BLOCK"
    g = _line(d, "grading")
    assert g["effect"] == "BLOCK" and g["rule"].startswith("rule 8:") and w["key"] in g["observed"]
    assert d["ungraded"][0]["test_id"] == never and d["ungraded"][0]["state"] == "NEVER_RUN"
    assert _line(d, "environments")["rule"] == "no rule triggered"          # scope_not_current carries no seed rule
    _stamped(session, w["org"], w["s1"], key=w["key"], outcome="failed")
    d2 = _grade(session, [w["key"]])["decision"]
    assert d2["recommendation"] == "no_go" and len(d2["ungraded"]) == 1     # NO GO stands; the unknown stays named


# 7 — §d: the production read-only target -------------------------------------

def test_production_read_only_target_grades_on_metadata_only(session, world):
    w = world
    prod_org = _org(session, ENV_PROD, "P")
    p1 = _version(session, prod_org)
    data_claim = _stamped(session, w["org"], w["s1"], key=w["key"])          # evidence on the sandbox only
    meta_claim = _stamped(session, prod_org, p1, key=w["key"], env=ENV_PROD)  # an inspection check current on prod
    # the plan admitted the inspection check on Prod1 and EXCLUDED the data check there by reason
    plan = _plan([(data_claim, ENV_A), (meta_claim, ENV_PROD)],
                 exclusions=[(data_claim, ENV_PROD, P.X_READ_ONLY_TARGET)])
    d = _grade(session, [w["key"]], targets=(ENV_A, ENV_PROD), plan=plan)["decision"]
    # the sandbox: data_claim current, meta_claim never run there → that IS ungraded on the sandbox
    # (the plan never admitted meta_claim there? it did not — but readiness is per target) → name it honestly
    env_line = _line(d, "environments")["observed"]
    assert "1 data checks not run here by policy (read-only) · not ungraded" in env_line
    assert not any(u["test_id"] == data_claim and u["environment_id"] == ENV_PROD for u in d["ungraded"])
    assert "production" not in " ".join(r["words"] for ln in d["evidence_lines"] for r in ln["rules"])
    # the same target with the inspection check ADMITTED but never run → rule 9 BLOCK (graded → no_go)
    plan2 = _plan([(data_claim, ENV_A), (meta_claim, ENV_PROD), (data_claim, ENV_PROD)])
    session.execute(text("DELETE FROM s4_execution_runs WHERE CAST(claim_test_id AS text) = :t AND environment_id = :e"),
                    {"t": meta_claim, "e": ENV_PROD})
    d2 = _grade(session, [w["key"]], targets=(ENV_A, ENV_PROD), plan=plan2)["decision"]
    assert d2["recommendation"] == "no_go"
    assert _line(d2, "environments")["effect"] == "BLOCK" and 9 in {r["position"] for r in _line(d2, "environments")["rules"]}
    assert "inspection check(s) admitted but never run here" in _line(d2, "environments")["observed"]


# 5 + 8 — conformance: levels, NEEDS_HUMAN, the never-"resolved" guard ----------

SITE = "step5.example.my.site.com"


def _conformance_world(session, key):
    coord = SemanticTransactionCoordinator()
    s = SurfaceNaturalKey(site=SITE, path="/s", persona_scope="customer")
    org = session.execute(text("INSERT INTO connected_orgs (org_type, sf_instance_url, label) VALUES ('sandbox', 'https://s5c.example', 's5c') RETURNING CAST(id AS text)")).scalar()
    v = claim_sets.create_inventory_version(
        session, members=[{"site": s.site, "path": s.path, "persona_scope": s.persona_scope,
                           "record_context_ref": None, "viewport": None, "display_name": "Portal home"}],
        created_by=1, connected_org_id=org)
    tids = []
    for rule in ("PLM-A11Y-001", "PLM-A11Y-002", "PLM-A11Y-003"):
        r = coord.write_claim(session, actor="s3", test_id=None, archetype="ui", claim_kind="conformance-claim",
                              asserted_truth=ConformanceClaimBody(plimsol_rule_id=rule, surface=s),
                              semantic_conditions=SemanticConditionsBody())
        coord.link_requirement(session, actor="human", test_id=r.test_id, external_system="jira",
                               external_key=key, link_kind="verifies")
        tids.append(str(r.test_id))
    set_id = claim_sets.create_claim_set(session, persona_scope="customer", inventory_version=v, catalogue_release_id=1,
                                         created_by=1, members=[{"test_id": t, "applicability": "APPLICABLE", "executable": True}
                                                                for t in tids])
    session.execute(text("UPDATE claim_sets SET status = 'approved', approved_by = 1, approved_at = clock_timestamp(), "
                         "member_count = 3 WHERE id = CAST(:i AS uuid)"), {"i": str(set_id)})
    return {"set": str(set_id), "tids": tids, "surface": canonical_surface_key(s)}


def _processing_run(session, cw, verdicts):
    job, manifest = str(uuid4()), str(uuid4())
    session.execute(text("""
        INSERT INTO s6_ui_processing_runs (job_id, manifest_id, claim_set_id, engine, engine_version, bindings_hash,
            unmapped_engine_ids, surface_statuses, verdict_counts, no_verdict_members, processed_at)
        VALUES (CAST(:j AS uuid), CAST(:m AS uuid), CAST(:c AS uuid), 'axe', '4.13.0', 'x', '[]'::jsonb, '{}'::jsonb,
            '{}'::jsonb, '[]'::jsonb, clock_timestamp())"""), {"j": job, "m": manifest, "c": cw["set"]})
    for tid, rule, verdict in verdicts:
        session.execute(text("""
            INSERT INTO s6_ui_verdicts (id, manifest_id, job_id, surface_key, claim_set_id, test_id, plimsol_rule_id,
                verdict, verdict_basis, ownership, evidence_state_at_write)
            VALUES (CAST(:i AS uuid), CAST(:m AS uuid), CAST(:j AS uuid), :k, CAST(:c AS uuid), CAST(:t AS uuid), :r,
                :v, '{}'::jsonb, 'CONFIRMED', 'REFERENCED')"""),
                        {"i": str(uuid4()), "m": manifest, "j": job, "k": cw["surface"], "c": cw["set"], "t": tid,
                         "r": rule, "v": verdict})
    return job


LEVELS = {"PLM-A11Y-001": "A", "PLM-A11Y-002": "AA", "PLM-A11Y-003": "AA"}


def test_conformance_level_a_blocks_level_aa_conditions_and_needs_human_reviews(session, world):
    w = world
    cw = _conformance_world(session, w["key"])
    t1, t2, t3 = cw["tids"]
    # no processing run yet → the three conformance checks are ungraded, named
    d0 = _grade(session, [w["key"]], rule_levels=LEVELS)["decision"]
    assert d0["recommendation"] == "cannot_determine" and len(d0["ungraded"]) == 3
    assert "no processing run" in _line(d0, "conformance")["observed"]
    # a run: level-AA FAIL only → CONDITIONAL
    _processing_run(session, cw, [(t1, "PLM-A11Y-001", "PASS"), (t2, "PLM-A11Y-002", "FAIL"), (t3, "PLM-A11Y-003", "PASS")])
    d1 = _grade(session, [w["key"]], rule_levels=LEVELS)["decision"]
    assert d1["recommendation"] == "conditional_go"
    c = _line(d1, "conformance")
    assert c["effect"] == "CONDITIONAL" and c["rule"].startswith("rule 3:") and "1 level-AA FAIL" in c["observed"]
    assert _line(d1, "grading")["observed"] == "Nothing ungraded."
    # a later run: level-A FAIL → BLOCK (no_go); plus NEEDS_HUMAN → REVIEW named beside it
    _processing_run(session, cw, [(t1, "PLM-A11Y-001", "FAIL"), (t2, "PLM-A11Y-002", "PASS"), (t3, "PLM-A11Y-003", "NEEDS_HUMAN")])
    d2 = _grade(session, [w["key"]], rule_levels=LEVELS)["decision"]
    assert d2["recommendation"] == "no_go"
    assert _line(d2, "conformance")["rule"].startswith("rule 2:")
    h = _line(d2, "human_reviews")
    assert h["effect"] == "REVIEW" and h["rule"].startswith("rule 4:") and h["observed"].startswith("1 pending")
    # waive the level-A failure by RULE → the conformance line clears; NEEDS_HUMAN alone → cannot_determine
    qp.record_waiver(session, release_id=None, item_kind="rule", item_ref="PLM-A11Y-001", axis="conformance",
                     reviewer_user_id=3, reason="accepted for this release", expires_at=NOW + timedelta(days=3), created_by=1)
    d3 = _grade(session, [w["key"]], rule_levels=LEVELS)["decision"]
    assert d3["recommendation"] == "cannot_determine" and _line(d3, "conformance")["rule"] == "no rule triggered"
    # waive the human review (ruling 5): "N pending, N waived by <actor> until <date>", never "resolved"
    qp.record_waiver(session, release_id=None, item_kind="claim", item_ref=t3, axis="human_reviews",
                     reviewer_user_id=4, reason="reviewed on screen", expires_at=NOW + timedelta(days=3), created_by=1)
    d4 = _grade(session, [w["key"]], rule_levels=LEVELS)["decision"]
    assert d4["recommendation"] == "go"
    h4 = _line(d4, "human_reviews")
    assert h4["observed"].startswith("0 pending, waived by user 4 until 2026-09-13") and "resolved" not in h4["observed"]
    assert h4["rule"] == "no rule triggered"
    assert _line(d4, "waivers")["observed"].startswith("2 active waiver(s), 2 applied")
