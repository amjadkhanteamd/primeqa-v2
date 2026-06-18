"""Unit: the Results-page overview aggregator (pure — no DB).

``_aggregate_overview`` turns the scoped latest-run-per-test rows into the health
summary + the two lenses (by requirement, by root cause). The "real defect vs
fixable" split delegates to repair_agent.proposal_for (the system's source of
truth), so these tests assert the aggregation shape + that the classifier is
applied, NOT a re-derivation of the triage map.
"""
from datetime import datetime, timezone

from primeqa.intelligence.s4_execution_console import _aggregate_overview

_NOW = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)


def _row(test_id, *, req, outcome, verdict=None, cause=None):
    """A scoped row as the SQL returns it (asserted_truth is a dict; a
    prohibition body so claim_title renders a real sentence)."""
    return {
        "test_id": test_id, "run_id": f"run-{test_id}",
        "outcome": outcome, "verdict": verdict, "cause_kind": cause,
        "finished_at": datetime(2026, 6, 18, 10, 0, 0, tzinfo=timezone.utc),
        "duration_ms": 1500, "environment_id": 59,
        "claim_kind": "prohibition-claim",
        "asserted_truth": {"target": {"external_id": "Case"},
                           "operation": "modify_record"},
        "requirement_key": req,
    }


def _overview():
    rows = [
        _row("a", req="SQ-1", outcome="passed", verdict="value_persisted"),
        _row("b", req="SQ-1", outcome="failed",
             verdict="prohibition_not_enforced", cause="enforcement_gap"),
        _row("c", req="SQ-2", outcome="failed",
             verdict="prohibition_not_enforced", cause="enforcement_gap"),
        _row("d", req="SQ-2", outcome="errored", verdict="not_evaluated"),
    ]
    return _aggregate_overview(rows, now=_NOW)


def test_summary_counts():
    s = _overview()["summary"]
    assert s["total"] == 4
    assert s["passed"] == 1
    assert s["failing"] == 3
    assert s["pass_rate"] == 25
    # counts the cluster buckets (enforcement_gap + the unattributed errored one),
    # so the banner number == len(by_cause) — no cross-panel mismatch.
    assert s["causes"] == 2
    assert s["reqs_failing"] == 2      # SQ-1 + SQ-2 each have a failure


def test_by_requirement_groups_and_orders_failing_first():
    by_req = _overview()["by_requirement"]
    keys = [g["requirement_key"] for g in by_req]
    assert keys == ["SQ-2", "SQ-1"]    # SQ-2 (2 failed) before SQ-1 (1 failed)
    sq1 = next(g for g in by_req if g["requirement_key"] == "SQ-1")
    assert (sq1["passed"], sq1["failed"], sq1["total"]) == (1, 1, 2)
    # the failing test carries a real title + plain verdict
    failing = [t for t in sq1["tests"] if t["outcome"] != "passed"][0]
    assert failing["title"] == "Rejects editing records on Case"
    assert "forbidden" in failing["verdict_plain"].lower()


def test_by_cause_clusters_and_classifies():
    by_cause = _overview()["by_cause"]
    # enforcement_gap (count 2) ranks above the single unattributed/errored one
    top = by_cause[0]
    assert top["cause_kind"] == "enforcement_gap"
    assert top["count"] == 2
    assert top["cause_class"] == "real_defect"      # a finding → not auto-fixable
    assert top["requirement_count"] == 2
    assert "requirement_keys" not in top            # internal set is dropped
    # the errored run with no cause → 'rerun' proposal → fixable
    errored = next(c for c in by_cause if c["cause_kind"] is None)
    assert errored["cause_class"] == "fixable"
    assert errored["cause_plain"] == "Unattributed failure"


def test_banner_cause_count_matches_by_cause_lens():
    # regression: the health-banner "N root causes" must equal the number of
    # clusters the by-cause lens shows (incl. the unattributed bucket).
    ov = _overview()
    assert ov["summary"]["causes"] == len(ov["by_cause"])


def test_empty_rows_safe():
    out = _aggregate_overview([], now=_NOW)
    assert out["summary"] == {"total": 0, "passed": 0, "failing": 0,
                              "pass_rate": None, "causes": 0, "reqs_failing": 0}
    assert out["by_requirement"] == []
    assert out["by_cause"] == []
