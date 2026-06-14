"""Unit: compute_substrate_decision — theme #3 slice 2 (D-198). Pure, no DB.

Table-driven over slice-1 evidence shapes: blockers (no runs at all, pass-rate
below threshold, broken grounding), warnings (drifted/stale grounding, partial
coverage, version currency, freshness), the go/conditional_go/no_go ladder, and
the substrate-native risk score/level.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from primeqa.intelligence.substrate_decision import compute_substrate_decision

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
_FRESH = "2026-06-10T10:00:00+00:00"          # 2h old — inside any window
_OLD = "2026-05-01T10:00:00+00:00"            # ~40 days old — outside 168h


def _claim(outcome="passed", *, overall="intact", stale=False, never=False,
           superseded=False, version_unknown=False, finished_at=_FRESH,
           flaky=False, recent=None):
    return {
        "test_id": "t",
        "approved_seq": 1,
        "grounding": {"overall": overall, "stale": stale,
                      "evaluated_at_version_seq": 1},
        "latest_run": None if never else {
            "run_id": "r", "outcome": outcome, "verdict": None,
            "finished_at": finished_at, "version_unknown": version_unknown},
        "superseded_newer_run": superseded,
        "never_run": never,
        "flaky": flaky,
        "recent_outcomes": recent or ([] if never else [outcome]),
    }


def _checks(out):
    return {r["check"]: r["status"] for r in out["reasoning"]}


def test_all_passing_intact_is_go():
    out = compute_substrate_decision([_claim(), _claim()], now=_NOW)
    assert out["recommendation"] == "go" and out["confidence"] == 0.95
    assert out["metrics"]["blockers"] == 0 and out["metrics"]["warnings"] == 0
    assert out["risk"] == {"score": 0, "level": "low"}


def test_empty_evidence_is_not_applicable():
    out = compute_substrate_decision([], now=_NOW)
    assert out == {"applicable": False, "claim_count": 0}


def test_all_never_run_is_no_go_via_has_runs():
    out = compute_substrate_decision([_claim(never=True)], now=_NOW)
    assert out["recommendation"] == "no_go"
    assert _checks(out)["has_runs"] == "fail"
    assert out["criteria_met"]["has_runs"] is False


def test_pass_rate_below_threshold_blocks():
    # 1 of 2 passed = 50% < 95 default.
    out = compute_substrate_decision([_claim(), _claim("failed")], now=_NOW)
    assert out["recommendation"] == "no_go"
    assert _checks(out)["pass_rate"] == "fail"
    assert out["metrics"]["pass_rate"] == 50.0


def test_errored_counts_as_not_passed():
    out = compute_substrate_decision([_claim(), _claim("errored")], now=_NOW)
    assert _checks(out)["pass_rate"] == "fail"
    assert out["metrics"]["errored"] == 1


def test_custom_pass_rate_threshold_respected():
    out = compute_substrate_decision(
        [_claim(), _claim("failed")],
        {"substrate_min_pass_rate": 50}, now=_NOW)
    assert _checks(out)["pass_rate"] == "pass"


def test_broken_grounding_blocks_by_default():
    out = compute_substrate_decision([_claim(overall="broken")], now=_NOW)
    assert out["recommendation"] == "no_go"
    assert _checks(out)["grounding_integrity"] == "fail"


def test_broken_grounding_warns_when_blocking_disabled():
    out = compute_substrate_decision(
        [_claim(overall="broken")],
        {"substrate_block_on_broken_grounding": False}, now=_NOW)
    assert _checks(out)["grounding_integrity"] == "warn"
    assert out["recommendation"] == "conditional_go"


def test_drifted_and_stale_warn_to_conditional_go():
    out = compute_substrate_decision(
        [_claim(overall="drifted"), _claim(stale=True)], now=_NOW)
    assert out["recommendation"] == "conditional_go" and out["confidence"] == 0.75
    assert _checks(out)["grounding_integrity"] == "warn"


def test_partial_coverage_warns():
    out = compute_substrate_decision([_claim(), _claim(never=True)], now=_NOW)
    assert _checks(out)["coverage"] == "warn"
    assert out["recommendation"] == "conditional_go"
    assert out["metrics"]["never_run"] == 1


def test_version_currency_warns_on_superseded_and_unknown():
    out = compute_substrate_decision(
        [_claim(superseded=True), _claim(version_unknown=True)], now=_NOW)
    assert _checks(out)["version_currency"] == "warn"


def test_stale_run_warns_on_freshness():
    out = compute_substrate_decision([_claim(finished_at=_OLD)], now=_NOW)
    assert _checks(out)["freshness"] == "warn"
    assert out["recommendation"] == "conditional_go"


def test_freshness_window_configurable():
    out = compute_substrate_decision(
        [_claim(finished_at=_OLD)],
        {"substrate_max_run_age_hours": 24 * 365}, now=_NOW)
    assert "freshness" not in _checks(out)
    assert out["recommendation"] == "go"


def test_risk_score_scales_with_failures_and_findings():
    # 0% pass (1 failed) -> 50 base + 25 blocker = 75 -> critical.
    out = compute_substrate_decision([_claim("failed")], now=_NOW)
    assert out["risk"]["score"] == 75 and out["risk"]["level"] == "critical"
    # one warning only -> 10 -> low.
    out2 = compute_substrate_decision([_claim(overall="drifted")], now=_NOW)
    assert out2["risk"]["score"] == 10 and out2["risk"]["level"] == "low"


def test_output_mirrors_v1_shape_keys():
    out = compute_substrate_decision([_claim()], now=_NOW)
    for key in ("recommendation", "confidence", "reasoning", "criteria_met",
                "metrics", "risk"):
        assert key in out


# --- D-200: flake quarantine ---------------------------------------------------

def test_flaky_failure_is_quarantined_not_blocking():
    # A chronically-flipping claim whose latest run failed must NOT block the
    # release — it leaves the pass rate and surfaces as a quarantine warning.
    out = compute_substrate_decision(
        [_claim(), _claim("failed", flaky=True,
                          recent=["failed", "passed", "failed", "passed"])],
        now=_NOW)
    assert out["recommendation"] == "conditional_go"       # warn, not no_go
    assert _checks(out)["flaky_quarantine"] == "warn"
    assert _checks(out)["pass_rate"] == "pass"             # 1/1 scored = 100%
    assert out["metrics"]["quarantined"] == 1


def test_flaky_but_passing_counts_normally():
    out = compute_substrate_decision(
        [_claim(flaky=True, recent=["passed", "failed", "passed"])], now=_NOW)
    assert out["metrics"]["quarantined"] == 0
    assert _checks(out)["pass_rate"] == "pass"


def test_stable_regression_is_never_quarantined():
    # One pass->fail edge is a REAL regression: flaky=False from _is_flaky, so
    # the failure blocks as it should.
    out = compute_substrate_decision(
        [_claim("failed", recent=["failed", "passed", "passed"])], now=_NOW)
    assert out["recommendation"] == "no_go"
    assert "flaky_quarantine" not in _checks(out)


def test_quarantine_opt_out_via_criteria():
    out = compute_substrate_decision(
        [_claim("failed", flaky=True, recent=["failed", "passed", "failed"])],
        {"substrate_quarantine_flaky": False}, now=_NOW)
    assert out["recommendation"] == "no_go"                # blocks when opted out


def test_is_flaky_detection_thresholds():
    from primeqa.intelligence.substrate_decision import _is_flaky
    assert _is_flaky(["failed", "passed", "failed"]) is True       # 2 transitions
    assert _is_flaky(["failed", "passed", "passed"]) is False      # 1 = regression
    assert _is_flaky(["passed", "passed", "passed"]) is False
    assert _is_flaky(["passed"]) is False and _is_flaky([]) is False


# ---------------------------------------------------------------------------
# D-237 — the explainable NO-GO: the `blocking` list + the cause phrase.
# ---------------------------------------------------------------------------

from primeqa.intelligence.substrate_decision import _cause_phrase  # noqa: E402


def _bclaim(outcome, *, keys=(), cause=None, verdict=None, flaky=False,
            recent=None, finished_at=_FRESH):
    """An evidence row carrying the D-237 enrichment (external_keys + cause)."""
    return {
        "test_id": "t-" + (keys[0] if keys else outcome),
        "external_keys": list(keys),
        "approved_seq": 1,
        "grounding": {"overall": "intact", "stale": False,
                      "evaluated_at_version_seq": 1},
        "latest_run": {"run_id": "r", "outcome": outcome, "verdict": verdict,
                       "finished_at": finished_at, "version_unknown": False,
                       "cause": cause},
        "superseded_newer_run": False,
        "never_run": False,
        "flaky": flaky,
        "recent_outcomes": recent or [outcome],
    }


def test_blocking_names_failed_scored_claim_with_cause():
    out = compute_substrate_decision(
        [_bclaim("passed", keys=["SQ-207"]),
         _bclaim("failed", keys=["SQ-205"], verdict="state_not_transitioned",
                 cause="the escalation Flow's effect was not observed")],
        now=_NOW)
    assert out["recommendation"] == "no_go"
    assert len(out["blocking"]) == 1
    b = out["blocking"][0]
    assert b["external_keys"] == ["SQ-205"]
    assert b["verdict"] == "state_not_transitioned"
    assert b["outcome"] == "failed"
    assert b["cause"] == "the escalation Flow's effect was not observed"


def test_blocking_includes_errored_claims():
    out = compute_substrate_decision(
        [_bclaim("errored", keys=["SQ-9"], cause="credentials/infrastructure")],
        now=_NOW)
    assert [b["outcome"] for b in out["blocking"]] == ["errored"]


def test_blocking_excludes_passed_and_quarantined():
    # The only failure is a chronically-flipping (quarantined) claim → excluded
    # from `scored`, so it must NOT appear as a release blocker.
    out = compute_substrate_decision(
        [_bclaim("passed", keys=["A"]),
         _bclaim("failed", keys=["B"], flaky=True,
                 recent=["failed", "passed", "failed", "passed"])],
        now=_NOW)
    assert out["blocking"] == []


def test_blocking_empty_when_all_pass():
    out = compute_substrate_decision([_bclaim("passed", keys=["A"])], now=_NOW)
    assert out["recommendation"] == "go"
    assert out["blocking"] == []


def test_blocking_sorted_by_requirement_key():
    out = compute_substrate_decision(
        [_bclaim("failed", keys=["SQ-30"]), _bclaim("failed", keys=["SQ-12"])],
        now=_NOW)
    assert [b["external_keys"][0] for b in out["blocking"]] == ["SQ-12", "SQ-30"]


def test_blocking_tolerates_legacy_evidence_without_keys_or_cause():
    # _claim() predates the D-237 enrichment (no external_keys / no cause) — the
    # blocking builder must default gracefully, never KeyError.
    out = compute_substrate_decision([_claim(), _claim("failed")], now=_NOW)
    assert len(out["blocking"]) == 1
    assert out["blocking"][0]["external_keys"] == []
    assert out["blocking"][0]["cause"] is None


# ---- _cause_phrase ---------------------------------------------------------

def test_cause_phrase_prefers_s6_attribution_sentence():
    detail = {"cause": {"detail": "an active Flow did not fire",
                        "cause_kind": "automation_effect_absent"}}
    assert _cause_phrase(detail, "state_not_transitioned", "failed") == \
        "an active Flow did not fire"


def test_cause_phrase_accepts_json_string_detail():
    import json
    detail = json.dumps({"cause": {"detail": "blocked by the wrong rule"}})
    assert _cause_phrase(detail, None, "failed") == "blocked by the wrong rule"


def test_cause_phrase_falls_back_to_verdict_plain():
    out = _cause_phrase(None, "prohibition_not_enforced", "failed")
    assert "ALLOWED" in out          # the humanized verdict from _VERDICT_PLAIN


def test_cause_phrase_falls_back_to_outcome_when_no_verdict():
    assert _cause_phrase({}, None, "errored") == "Could not run to completion"


def test_cause_phrase_never_raises_on_garbage():
    assert _cause_phrase(12345, None, "failed")        # non-dict/str detail
    assert _cause_phrase("{not valid json", None, "failed")  # bad json string
