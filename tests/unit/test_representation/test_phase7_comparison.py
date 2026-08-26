"""Phase 7 pure merge-gate tests — the transition matrix, the tool/env
diffs, the CONDITIONAL rung 4 interplay via rank_causes, and the DE-13
ranking with every moved dimension retained."""
from __future__ import annotations

import pytest

from primeqa.interpretation.ui_comparison import (
    FIXED,
    NEW_FAIL,
    STILL_FAILING,
    STILL_PASSING,
    diff_environment,
    diff_tool_pins,
    fingerprint_delta,
    rank_causes,
    transition_for,
)

pytestmark = pytest.mark.unit


def test_transition_matrix():
    assert transition_for("PASS", "FAIL") == NEW_FAIL
    assert transition_for("FAIL", "PASS") == FIXED
    assert transition_for("FAIL", "FAIL") == STILL_FAILING
    assert transition_for("PASS", "PASS") == STILL_PASSING


def test_tool_diff_records_every_moved_pin():
    a = {"axe_sha256": "x", "catalogue_release_id": 2,
         "playwright_version": "1.62.0"}
    b = {"axe_sha256": "y", "catalogue_release_id": 3,
         "playwright_version": "1.62.0"}
    moved = diff_tool_pins(a, b, "bh1", "bh1")
    assert set(moved) == {"axe_sha256", "catalogue_release_id"}
    assert moved["axe_sha256"] == ["x", "y"]
    assert diff_tool_pins(a, a, "bh1", "bh2") == {
        "bindings_hash": ["bh1", "bh2"]}
    assert diff_tool_pins(a, a, "bh1", "bh1") == {}


def test_env_diff_and_not_captured_honesty():
    snap = {"platform_api_version": "63.0",
            "packages": [{"package_id": "P1", "version_id": "V1"}]}
    upgraded = {"platform_api_version": "63.0",
                "packages": [{"package_id": "P1", "version_id": "V2"}]}
    d = diff_environment(snap, upgraded)
    assert d["packages"]["version_changed"] == [
        {"package_id": "P1", "from": "V1", "to": "V2"}]
    assert "platform" not in d
    # a missing snapshot is 'not captured', NEVER 'no change'
    assert diff_environment(None, snap) == {
        "not_captured": {"baseline": True, "candidate": False}}
    assert diff_environment(snap, snap) == {}


def test_fingerprint_delta_none_when_equal_and_named_diff_when_not():
    obs = {"fingerprint": {"sha256": "a" * 64,
                           "summary": {"element_count": 3,
                                       "named": [["link", "Home"]]}}}
    assert fingerprint_delta(obs, obs) is None
    obs2 = {"fingerprint": {"sha256": "b" * 64,
                            "summary": {"element_count": 4,
                                        "named": [["link", "Away"]]}}}
    d = fingerprint_delta(obs, obs2)
    assert d["named_removed"] == [["link", "Home"]]
    assert d["named_added"] == [["link", "Away"]]


def test_de13_ranking_bundle_beats_package_beats_platform_beats_tool():
    bundle = {"bundle": "loanWidget", "bundle_ref": "E1"}
    env = {"platform": ["62.0", "63.0"],
           "packages": {"version_changed": [{"package_id": "P1"}]}}
    tool = {"axe_sha256": ["x", "y"]}
    c = rank_causes(bundle_evidence=bundle, env_delta=env,
                    tool_drift=tool, fp_delta=None)
    assert c["primary"] == "CLIENT_BUNDLE"
    assert c["confidence"] == "MEDIUM"          # several dimensions moved
    # every moved dimension retained under the headline
    dims = {x["dimension"] for x in c["contributing"]}
    assert dims == {"ENVIRONMENT_PACKAGE", "ENVIRONMENT_PLATFORM", "TOOL"}

    c2 = rank_causes(bundle_evidence=None, env_delta=env, tool_drift={},
                     fp_delta=None)
    assert c2["primary"] == "ENVIRONMENT_PACKAGE"
    c3 = rank_causes(bundle_evidence=None,
                     env_delta={"platform": ["62.0", "63.0"]},
                     tool_drift={}, fp_delta=None)
    assert c3["primary"] == "ENVIRONMENT_PLATFORM"
    assert c3["confidence"] == "HIGH"           # exactly one moved
    c4 = rank_causes(bundle_evidence=None, env_delta={}, tool_drift=tool,
                     fp_delta=None)
    assert c4["primary"] == "TOOL"


def test_unexplained_is_honest_low():
    c = rank_causes(bundle_evidence=None, env_delta={}, tool_drift={},
                    fp_delta=None)
    assert c["primary"] is None
    assert c["confidence"] == "LOW"
    assert "unexplained" in c["note"]


def test_amended_rung4_fp_delta_rides_the_evidence():
    fp = {"baseline_sha256": "a" * 64, "candidate_sha256": "b" * 64}
    c = rank_causes(bundle_evidence={"bundle": "x"}, env_delta={},
                    tool_drift={}, fp_delta=fp)
    assert c["fingerprint_delta"] == fp        # evidence, not refusal
