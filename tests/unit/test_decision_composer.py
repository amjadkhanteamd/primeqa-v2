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


@pytest.fixture
def stub(monkeypatch):
    import primeqa.release.decision_composer as dc
    state = {"substrate": _substrate(), "calls": [], "policy": _policy_decision()}

    def _fake(tenant_id, keys, criteria=None):
        state["calls"].append({"keys": keys, "criteria": criteria})
        return state["substrate"]
    monkeypatch.setattr(sd, "get_release_substrate_decision", _fake)
    monkeypatch.setattr(dc, "_grade_under_policy", lambda tenant_id, release_id, keys: state["policy"])
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
