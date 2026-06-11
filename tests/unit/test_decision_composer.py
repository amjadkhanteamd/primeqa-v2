"""Unit: the release decision composer — substrate-only since D-221 R4 (the
v1 DecisionEngine retired with its engine; D-220 verified its corpus was
empty). Pure, no DB: the substrate wrapper is stubbed via monkeypatch."""
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

    def list_requirements(self, release_id):
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


@pytest.fixture
def stub(monkeypatch):
    state = {"substrate": _substrate(), "calls": []}

    def _fake(tenant_id, keys, criteria=None):
        state["calls"].append({"keys": keys, "criteria": criteria})
        return state["substrate"]
    monkeypatch.setattr(sd, "get_release_substrate_decision", _fake)
    return state


def test_substrate_recommendation_is_the_decision(stub):
    repo = _Repo()
    env = evaluate_and_record(None, _Release(), 1, release_repo=repo)
    assert env["recommendation"] == "go"
    assert env["recommendation_source"] == "substrate"
    assert env["v1"] is None
    assert env["substrate"]["applicable"] is True
    assert len(repo.decisions) == 1
    assert repo.decisions[0]["recommendation"] == "go"


def test_no_evidence_is_a_conservative_no_go(stub):
    stub["substrate"] = {"available": True, "applicable": False, "claim_count": 0}
    repo = _Repo()
    env = evaluate_and_record(None, _Release(), 1, release_repo=repo)
    assert env["recommendation"] == "no_go"
    assert env["criteria_met"] == {"has_evidence": False}
    assert repo.decisions[0]["recommendation"] == "no_go"


def test_unavailable_substrate_is_a_conservative_no_go(stub):
    stub["substrate"] = {"available": False, "applicable": False, "claim_count": 0}
    repo = _Repo()
    env = evaluate_and_record(None, _Release(), 1, release_repo=repo)
    assert env["recommendation"] == "no_go"


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
