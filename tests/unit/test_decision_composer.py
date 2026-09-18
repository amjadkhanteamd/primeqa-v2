"""Unit: the release decision composer — substrate-only since D-221 R4 (the
v1 DecisionEngine retired with its engine; D-220 verified its corpus was
empty); Step 5: the POLICY ENGINE owns the recommendation, the substrate
block rides along. Pure, no DB: the substrate wrapper and the policy grading
are stubbed via monkeypatch."""
from __future__ import annotations

import pytest

import primeqa.intelligence.substrate_decision as sd
from primeqa.release.decision_composer import (
    evaluate_and_record,
    external_keys_for_requirements,
)

pytestmark = pytest.mark.unit


class _Release:
    def __init__(self, criteria=None):
        self.id = 7
        self.name = "r7"
        self.decision_criteria = criteria or {}


class _Repo:
    def __init__(self):
        self.decisions = []

    def list_requirements(self, release_id, tenant_id=None):
        return []

    def create_decision(self, **kw):
        self.decisions.append(kw)
        return kw


def _substrate(recommendation="go", confidence=0.95):
    return {
        "available": True, "applicable": True,
        "recommendation": recommendation, "confidence": confidence,
        "reasoning": [{"check": "pass_rate", "status": "pass", "detail": "ok"}],
        "criteria_met": {"pass_rate": True},
        "metrics": {"claim_count": 3},
        "risk": {"score": 5, "level": "low"},
    }


def _policy_decision(recommendation="go", confidence=0.95, **over):
    lines = [{"axis": a, "observed": "obs", "rule": "no rule triggered", "effect": None, "rules": [], "items": []}
             for a in ("functional", "conformance", "regressions", "waivers", "human_reviews", "environments", "grading")]
    d = {"recommendation": recommendation, "confidence": confidence, "effect": "ALLOW", "because": "x",
         "evidence_lines": lines, "triggered_rules": [], "ungraded": [],
         "policy": {"id": "p1", "name": "Plimsol default", "version": 1, "label": "Plimsol default v1"},
         "plan_id": "9ea0c522-0000-0000-0000-000000000000", "plan": None, "observations": {},
         "evaluated_at": "2026-09-10T00:00:00+00:00"}
    d.update(over)
    return {"ok": True, "decision": d}


def _scope(**over):
    """A NON-EMPTY resolved scope (the AUD-014 pre-condition's input)."""
    sc = {"keys": ["SQ-1"], "requirement_count": 1, "functional_ids": ["t1"], "conformance_ids": [],
          "targets": [59], "targets_source": "declared", "active_targets": [59], "checks": 1, "empty": None}
    sc.update(over)
    return sc


def _ready(**over):
    r = {"available": True, "items": [], "non_current": 0, "environments": [59], "claim_count": 1}
    r.update(over)
    return r


@pytest.fixture
def stub(monkeypatch):
    import primeqa.release.decision_composer as dc
    state = {"substrate": _substrate(), "calls": [], "policy": _policy_decision(),
             "scope": _scope(), "readiness": _ready(), "scope_error": None}

    def _fake(tenant_id, keys, criteria=None):
        state["calls"].append({"keys": keys, "criteria": criteria})
        return state["substrate"]
    monkeypatch.setattr(sd, "get_release_substrate_decision", _fake)
    monkeypatch.setattr(dc, "_grade_under_policy", lambda tenant_id, release_id, keys: state["policy"])
    monkeypatch.setattr(dc, "_resolve_scope_and_readiness",
                        lambda tenant_id, release_id, keys: (state["scope"], state["readiness"], state["scope_error"]))
    return state


def test_policy_recommendation_is_the_decision_and_records_policy_plus_plan(stub):
    repo = _Repo()
    env = evaluate_and_record(None, _Release(), 1, release_repo=repo)
    assert env["recommendation"] == "go"
    assert env["recommendation_source"] == "policy"
    assert env["v1"] is None
    assert env["substrate"]["applicable"] is True            # the substrate block rides along
    assert len(repo.decisions) == 1
    d = repo.decisions[0]
    assert d["recommendation"] == "go" and d["policy_id"] == "p1" and d["policy_version"] == 1
    assert d["plan_id"].startswith("9ea0c522")
    assert [r["check"] for r in env["reasoning"]] == [
        "functional", "conformance", "regressions", "waivers", "human_reviews", "environments", "grading"]


def test_no_active_policy_refuses_and_records_nothing(stub):
    stub["policy"] = {"ok": False, "reason": "no_active_policy", "sentence": "No active quality policy — activate one."}
    repo = _Repo()
    env = evaluate_and_record(None, _Release(), 1, release_repo=repo)
    assert env["refused"] is True and env["reason"] == "no_active_policy"
    assert env["recommendation"] is None and repo.decisions == []


def test_unavailable_substrate_does_not_move_the_policy_word(stub):
    stub["substrate"] = {"available": False, "applicable": False, "claim_count": 0}
    stub["policy"] = _policy_decision("no_go", 0.9)
    repo = _Repo()
    env = evaluate_and_record(None, _Release(), 1, release_repo=repo)
    assert env["recommendation"] == "no_go" and env["metrics"] is None


def test_envelope_keeps_the_uniform_keys(stub):
    repo = _Repo()
    env = evaluate_and_record(None, _Release(), 1, release_repo=repo)
    for key in ("recommendation", "confidence", "reasoning", "criteria_met",
                "metrics", "mode", "recommendation_source", "v1", "substrate"):
        assert key in env, key


def test_external_keys_builder_accepts_dicts_and_orm_rows():
    class _Row:
        id = 5
        jira_key = None
    keys = external_keys_for_requirements(
        [{"id": 3, "jira_key": "SQ-1"}, _Row()])
    assert keys == ["SQ-1", "req-5"]


def test_cannot_determine_is_recorded_verbatim_never_mapped(stub):
    # Step B (ruling F1a): the engine's refusal to grade is the fourth
    # recommendation value — recorded as itself (migration 071 admits it),
    # never rewritten to no_go (a verdict rendered for an absence).
    stub["policy"] = _policy_decision("cannot_determine", 0.0)
    stub["policy"]["decision"]["evidence_lines"][-1].update(
        {"observed": "1 ungraded: SQ-1 (NEVER_RUN)", "rule": "rule 8: grading · ungraded_present → BLOCK",
         "effect": "BLOCK"})
    repo = _Repo()
    env = evaluate_and_record(None, _Release(), 1, release_repo=repo)
    assert env["recommendation"] == "cannot_determine"
    assert env["confidence"] == 0.0
    assert repo.decisions[0]["recommendation"] == "cannot_determine"
    assert env["reasoning"][-1]["check"] == "grading" and env["reasoning"][-1]["status"] == "fail"


# --- AUD-014: a decision requires a non-empty graded scope ---------------------

def test_an_empty_scope_refuses_before_readiness_and_before_the_policy(stub):
    # no requirement: the refusal names it, no row, and NEITHER the readiness
    # verdict nor the policy grading is consulted (order: empty -> current -> policy)
    stub["scope"] = _scope(keys=[], requirement_count=0, functional_ids=[], targets=[], active_targets=[], checks=0,
                           empty={"reason": "no_requirement", "sentence": "no requirement is in scope — nothing to grade"})
    stub["readiness"] = _ready(non_current=3, items=[{"state": "NEVER_RUN"}] * 3)   # would refuse too — must not be the reason
    stub["policy"] = _policy_decision("go")                                        # would record GO — must not run
    repo = _Repo()
    env = evaluate_and_record(None, _Release(), 1, release_repo=repo)
    assert env["refused"] is True and env["reason"] == "scope_empty"
    assert env["empty"]["reason"] == "no_requirement"
    assert "no requirement is in scope" in env["sentence"]
    assert env["recommendation"] is None and repo.decisions == []
    assert env["reasoning"] == [{"check": "scope", "status": "fail", "detail": env["sentence"]}]
    assert env["criteria_met"] == {"scope": False}
    assert stub["calls"] == []                                                     # the substrate was not even read


def test_an_inactive_only_target_refuses_naming_the_environment(stub):
    stub["scope"] = _scope(targets=[78], active_targets=[], checks=0,
                           empty={"reason": "no_active_target", "targets": [78],
                                  "sentence": "every declared target environment is inactive (Prod RO) — no active environment is in scope"})
    repo = _Repo()
    env = evaluate_and_record(None, _Release(), 1, release_repo=repo)
    assert env["refused"] and env["reason"] == "scope_empty" and env["empty"]["reason"] == "no_active_target"
    assert "Prod RO" in env["sentence"] and repo.decisions == []


def test_a_scope_that_cannot_be_read_refuses_instead_of_grading(stub):
    # unknown is not satisfied: a failed scope read is a refusal, never a pass-through
    stub["scope"], stub["scope_error"] = None, "OperationalError: connection refused"
    repo = _Repo()
    env = evaluate_and_record(None, _Release(), 1, release_repo=repo)
    assert env["refused"] and env["reason"] == "scope_unavailable"
    assert "connection refused" in env["sentence"] and repo.decisions == []


def test_an_unavailable_readiness_read_refuses_instead_of_grading(stub):
    stub["readiness"] = {"available": False, "items": [], "non_current": 0, "environments": [], "claim_count": 0}
    repo = _Repo()
    env = evaluate_and_record(None, _Release(), 1, release_repo=repo)
    assert env["refused"] and env["reason"] == "scope_unavailable"
    assert repo.decisions == [] and stub["calls"] == []


def test_a_non_current_scope_still_refuses_second_with_its_items(stub):
    stub["readiness"] = _ready(non_current=1, items=[
        {"test_id": "t1", "external_keys": ["SQ-1"], "environment_id": 59, "state": "NEVER_RUN",
         "reason": None, "sentence": "No run in this environment.", "stamp_seq": None, "current_seq": 5},
        {"test_id": "t2", "external_keys": ["SQ-1"], "environment_id": 59, "state": "CURRENT",
         "reason": None, "sentence": "Current.", "stamp_seq": 5, "current_seq": 5}])
    repo = _Repo()
    env = evaluate_and_record(None, _Release(), 1, release_repo=repo)
    assert env["refused"] and env["reason"] == "scope_not_current"
    assert [i["test_id"] for i in env["items"]] == ["t1"]
    assert env["reasoning"][0]["check"] == "readiness" and env["criteria_met"] == {"readiness": False}
    assert repo.decisions == []


def test_the_engines_own_emptiness_refusal_is_carried_verbatim(stub):
    # the engine refuses by construction too (quality_decision_console.grade_release);
    # should the scope change between the two reads, its refusal is the envelope
    stub["policy"] = {"ok": False, "reason": "scope_empty", "sentence": "Evaluate will refuse — no requirement is in scope — nothing to grade",
                      "empty": {"reason": "no_requirement", "sentence": "no requirement is in scope — nothing to grade"}}
    repo = _Repo()
    env = evaluate_and_record(None, _Release(), 1, release_repo=repo)
    assert env["refused"] and env["reason"] == "scope_empty" and env["empty"]["reason"] == "no_requirement"
    assert env["reasoning"][0]["check"] == "scope" and repo.decisions == []


def test_external_keys_builder_prefers_the_identity_key_the_page_now_carries():
    # AUD-014 defect 2: the page's dicts carry external_key now; a manual
    # requirement (no jira_key) resolves to its identity, not to req-<id>
    keys = external_keys_for_requirements(
        [{"id": 3, "jira_key": None, "external_key": "AUD14-REQ"}, {"id": 4, "jira_key": "SQ-2", "external_key": "SQ-2"}])
    assert keys == ["AUD14-REQ", "SQ-2"]
