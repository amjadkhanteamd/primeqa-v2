"""Unit: the release decision composer — theme #3 slice 3 (D-198). Pure, no DB:
the v1 engine + the substrate wrapper are stubbed via monkeypatch; asserts the
mode-combine rules, the single-ledger persistence, and the v1 regression guard
(byte-identical top level when no substrate evidence applies).
"""
from __future__ import annotations

import pytest

import primeqa.intelligence.substrate_decision as sd
import primeqa.release.decision_engine as de
from primeqa.release.decision_composer import (
    evaluate_and_record,
    external_keys_for_requirements,
)

pytestmark = pytest.mark.unit


class _Release:
    def __init__(self, criteria=None):
        self.id = 7
        self.decision_criteria = criteria or {}


class _Repo:
    """Records create_decision; serves an empty requirements list."""

    def __init__(self):
        self.decisions = []

    def list_requirements(self, release_id):
        return []

    def create_decision(self, **kw):
        self.decisions.append(kw)
        return kw


def _v1(recommendation="go", confidence=0.95):
    return {
        "recommendation": recommendation, "confidence": confidence,
        "reasoning": [{"check": "pass_rate", "status": "pass", "detail": "ok"}],
        "criteria_met": {"pass_rate": True},
        "metrics": {"total_tests": 3},
    }


@pytest.fixture
def stub(monkeypatch):
    """Pin v1's evaluate + the substrate wrapper; returns a dict to tweak."""
    state = {"v1": _v1(), "substrate": {"available": True, "applicable": False},
             "substrate_calls": []}
    monkeypatch.setattr(de.DecisionEngine, "evaluate",
                        lambda self, release: state["v1"])

    def _fake_substrate(tenant_id, keys, criteria=None):
        state["substrate_calls"].append({"keys": keys, "criteria": criteria})
        return state["substrate"]
    monkeypatch.setattr(sd, "get_release_substrate_decision", _fake_substrate)
    return state


def test_advisory_default_v1_stands_substrate_attached(stub):
    stub["substrate"] = {"available": True, "applicable": True,
                         "recommendation": "no_go", "confidence": 0.9}
    repo = _Repo()
    out = evaluate_and_record(None, _Release(), 1, release_repo=repo)
    # advisory: v1's verdict stands even though the substrate says no_go.
    assert out["recommendation"] == "go" and out["recommendation_source"] == "v1"
    assert out["mode"] == "advisory"
    assert out["substrate"]["recommendation"] == "no_go"   # attached for the human
    assert len(repo.decisions) == 1
    assert repo.decisions[0]["recommendation"] == "go"
    assert repo.decisions[0]["reasoning"]["substrate"]["recommendation"] == "no_go"


def test_gating_degrades_never_upgrades(stub):
    stub["substrate"] = {"available": True, "applicable": True,
                         "recommendation": "no_go", "confidence": 0.9}
    repo = _Repo()
    out = evaluate_and_record(
        None, _Release({"substrate_mode": "gating"}), 1, release_repo=repo)
    assert out["recommendation"] == "no_go"
    assert out["recommendation_source"] == "substrate_gate"
    assert out["reasoning"][-1]["check"] == "substrate_gate"
    # v1's own dict was not mutated by the gate entry.
    assert stub["v1"]["reasoning"][-1]["check"] == "pass_rate"


def test_gating_never_upgrades_a_v1_no_go(stub):
    stub["v1"] = _v1("no_go", 0.9)
    stub["substrate"] = {"available": True, "applicable": True,
                         "recommendation": "go", "confidence": 0.95}
    out = evaluate_and_record(
        None, _Release({"substrate_mode": "gating"}), 1, release_repo=_Repo())
    assert out["recommendation"] == "no_go"                # min-severity, no upgrade
    assert out["recommendation_source"] == "v1"


def test_mode_off_never_queries_the_substrate(stub):
    out = evaluate_and_record(
        None, _Release({"substrate_mode": "off"}), 1, release_repo=_Repo())
    assert stub["substrate_calls"] == []                   # not even called
    assert out["substrate"] is None and out["mode"] == "off"


def test_unavailable_or_inapplicable_substrate_leaves_v1_intact(stub):
    for sub in ({"available": False, "applicable": False},
                {"available": True, "applicable": False}):
        stub["substrate"] = sub
        out = evaluate_and_record(
            None, _Release({"substrate_mode": "gating"}), 1, release_repo=_Repo())
        assert out["recommendation"] == "go"
        assert out["recommendation_source"] == "v1"


def test_regression_guard_top_level_byte_identical_to_v1(stub):
    # No applicable substrate evidence: the v1-shaped top level is identical;
    # the envelope only ADDS mode/recommendation_source/v1/substrate.
    out = evaluate_and_record(None, _Release(), 1, release_repo=_Repo())
    for key in ("recommendation", "confidence", "reasoning", "criteria_met",
                "metrics"):
        assert out[key] == stub["v1"][key]
    assert set(out) - set(stub["v1"]) == {
        "mode", "recommendation_source", "v1", "substrate"}


def test_external_keys_builder_accepts_dicts_and_orm_rows():
    class _Row:
        def __init__(self, id, jira_key=None):
            self.id, self.jira_key = id, jira_key
    assert external_keys_for_requirements(
        [{"id": 1, "jira_key": "SQ-1"}, {"id": 2}, {"id": None},
         _Row(3, "SQ-3"), _Row(4)]) == ["SQ-1", "req-2", "SQ-3", "req-4"]
