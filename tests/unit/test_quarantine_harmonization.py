"""Unit: D-232 — the release decision honors the persisted MANUAL quarantine.

A MANUAL pin wins (always quarantined → excluded from the pass-rate); a MANUAL lift
overrides the live flake signal (the claim is counted); absent a manual entry the live
signal governs (flaky AND latest not-passed). Pure `compute_substrate_decision` over
hand-built evidence — no DB."""
from primeqa.intelligence.substrate_decision import (
    _claim_quarantined,
    compute_substrate_decision,
)


def _claim(test_id, outcome, *, flaky=False, manual=None):
    return {"test_id": test_id, "approved_seq": 1, "grounding": None,
            "latest_run": {"run_id": "r", "outcome": outcome, "verdict": None,
                           "finished_at": "2026-06-14T00:00:00+00:00",
                           "version_unknown": False},
            "superseded_newer_run": False, "never_run": False,
            "flaky": flaky, "recent_outcomes": [outcome], "manual_quarantine": manual}


def test_claim_quarantined_precedence():
    assert _claim_quarantined(_claim("a", "failed", manual="pinned")) is True
    # a manual pin wins even when the latest run PASSED:
    assert _claim_quarantined(_claim("a", "passed", manual="pinned")) is True
    # a manual lift overrides the live flake+fail signal:
    assert _claim_quarantined(_claim("a", "failed", flaky=True, manual="lifted")) is False
    # no manual entry → the live signal governs:
    assert _claim_quarantined(_claim("a", "failed", flaky=True)) is True
    assert _claim_quarantined(_claim("a", "passed", flaky=True)) is False
    assert _claim_quarantined(_claim("a", "failed")) is False


def test_manual_pin_excludes_from_pass_rate_even_when_passing():
    ev = [_claim("a", "passed"), _claim("b", "passed", manual="pinned")]
    d = compute_substrate_decision(ev)
    assert d["metrics"]["quarantined"] == 1
    assert d["metrics"]["pass_rate"] == 100.0      # only claim a scored → 1/1


def test_manual_lift_overrides_live_flake_so_it_counts():
    # b is flaky+failing (would auto-quarantine) but manually LIFTED → it counts.
    ev = [_claim("a", "passed"), _claim("b", "failed", flaky=True, manual="lifted")]
    d = compute_substrate_decision(ev)
    assert d["metrics"]["quarantined"] == 0
    assert d["metrics"]["pass_rate"] == 50.0


def test_no_manual_entry_uses_the_live_signal():
    ev = [_claim("a", "passed"), _claim("b", "failed", flaky=True)]
    d = compute_substrate_decision(ev)
    assert d["metrics"]["quarantined"] == 1        # auto-quarantined by the live signal
