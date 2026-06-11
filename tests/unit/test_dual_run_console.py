"""Unit: the D-212 dual-run parity classification + assembly (pure, no DB)."""
from __future__ import annotations

from primeqa.intelligence.dual_run_console import (
    PARITY_CLASSES,
    assemble_release_parity,
    classify_parity,
)


# ---------------------------------------------------------------------------
# Classification matrix
# ---------------------------------------------------------------------------

def test_parity_pass_and_fail():
    assert classify_parity("passed", "passed") == "parity_pass"
    assert classify_parity("failed", "failed") == "parity_fail"
    assert classify_parity("error", "errored") == "parity_fail"


def test_divergences():
    # the retirement blocker: v1 catches what the new engine passes
    assert classify_parity("failed", "passed") == "divergent_v1_stricter"
    assert classify_parity("error", "passed") == "divergent_v1_stricter"
    # the (usually good) inverse
    assert classify_parity("passed", "failed") == "divergent_substrate_stricter"
    assert classify_parity("passed", "errored") == "divergent_substrate_stricter"


def test_gaps_and_untested():
    assert classify_parity("passed", None) == "substrate_gap"
    assert classify_parity("failed", None) == "substrate_gap"
    assert classify_parity(None, "passed") == "v1_gap"
    assert classify_parity(None, None) == "untested"
    # v1 skipped counts as nothing-evaluable
    assert classify_parity("skipped", None) == "untested"
    assert classify_parity("skipped", "passed") == "v1_gap"


def test_every_class_reachable():
    seen = {
        classify_parity("passed", "passed"),
        classify_parity("failed", "failed"),
        classify_parity("passed", "failed"),
        classify_parity("failed", "passed"),
        classify_parity("passed", None),
        classify_parity(None, "passed"),
        classify_parity(None, None),
    }
    assert seen == set(PARITY_CLASSES)


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def _reqs(*keys):
    return [{"key": k, "summary": f"about {k}"} for k in keys]


def test_assembly_counts_and_gate():
    out = assemble_release_parity(
        _reqs("SQ-1", "SQ-2", "SQ-3", "SQ-4"),
        v1_by_key={
            "SQ-1": {"status": "passed", "tc_count": 3},
            "SQ-2": {"status": "failed", "tc_count": 2},
            "SQ-3": {"status": "passed", "tc_count": 1},
        },
        substrate_by_key={
            "SQ-1": {"outcome": "passed", "verdict": "value_persisted",
                     "claim_count": 2, "never_run": False},
            "SQ-2": {"outcome": "passed", "verdict": "value_persisted",
                     "claim_count": 1, "never_run": False},
            "SQ-4": {"outcome": "failed", "verdict": "prohibition_not_enforced",
                     "claim_count": 1, "never_run": False},
        })
    assert out["requirement_count"] == 4
    assert out["counts"]["parity_pass"] == 1            # SQ-1
    assert out["counts"]["divergent_v1_stricter"] == 1  # SQ-2 (blocker)
    assert out["counts"]["substrate_gap"] == 1          # SQ-3 (blocker)
    assert out["counts"]["v1_gap"] == 1                 # SQ-4
    assert out["retirement_ready"] is False


def test_assembly_clean_window():
    out = assemble_release_parity(
        _reqs("SQ-1", "SQ-2"),
        v1_by_key={
            "SQ-1": {"status": "passed", "tc_count": 1},
            "SQ-2": {"status": "failed", "tc_count": 1},
        },
        substrate_by_key={
            "SQ-1": {"outcome": "passed", "verdict": None,
                     "claim_count": 1, "never_run": False},
            "SQ-2": {"outcome": "failed", "verdict": None,
                     "claim_count": 1, "never_run": False},
        })
    assert out["retirement_ready"] is True
    assert out["counts"]["parity_pass"] == 1 and out["counts"]["parity_fail"] == 1


def test_assembly_triage_first_ordering():
    out = assemble_release_parity(
        _reqs("A-pass", "B-blocker", "C-gap"),
        v1_by_key={
            "A-pass": {"status": "passed", "tc_count": 1},
            "B-blocker": {"status": "failed", "tc_count": 1},
            "C-gap": {"status": "passed", "tc_count": 1},
        },
        substrate_by_key={
            "A-pass": {"outcome": "passed", "verdict": None,
                       "claim_count": 1, "never_run": False},
            "B-blocker": {"outcome": "passed", "verdict": None,
                          "claim_count": 1, "never_run": False},
        })
    assert [r["parity"] for r in out["rows"]] == [
        "divergent_v1_stricter", "substrate_gap", "parity_pass"]


def test_never_run_claims_classify_as_gap():
    out = assemble_release_parity(
        _reqs("SQ-9"),
        v1_by_key={"SQ-9": {"status": "passed", "tc_count": 1}},
        substrate_by_key={"SQ-9": {"outcome": None, "verdict": None,
                                   "claim_count": 2, "never_run": True}})
    assert out["rows"][0]["parity"] == "substrate_gap"
    assert out["rows"][0]["substrate_claim_count"] == 2
