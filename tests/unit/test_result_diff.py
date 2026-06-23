"""Unit: the per-org RESULT diff aggregator (pure — no DB).

per-org Slice 5, Leg B (D-259). ``_aggregate_result_diff`` groups the latest run
per (claim_test_id, environment_id) into a cross-org outcome comparison
(agree | differ | missing_in_a | missing_in_b). The SQL ``DISTINCT ON`` does the
per-(claim, env) dedup; the aggregator also defends latest-run-wins in Python, so
this proves both the classification and the recency rule offline. The live
cross-org proof (env-59 vs org#2) is the push-time run cycle.
"""
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.unit

from primeqa.intelligence.s4_execution_console import _aggregate_result_diff

ENV_A = 59
ENV_B = 78


def _row(claim, env, outcome, *, at):
    return {"claim_test_id": claim, "environment_id": env, "outcome": outcome,
            "finished_at": datetime(2026, 6, 23, at, 0, 0, tzinfo=timezone.utc)}


def test_four_statuses():
    rows = [
        _row("agree-claim", ENV_A, "passed", at=10),
        _row("agree-claim", ENV_B, "passed", at=10),
        _row("differ-claim", ENV_A, "passed", at=10),
        _row("differ-claim", ENV_B, "failed", at=10),
        _row("only-a-claim", ENV_A, "passed", at=10),     # → missing_in_b
        _row("only-b-claim", ENV_B, "errored", at=10),    # → missing_in_a
    ]
    out = _aggregate_result_diff(rows, ENV_A, ENV_B)
    assert out["totals"] == {"agree": 1, "differ": 1,
                             "missing_in_a": 1, "missing_in_b": 1}
    c = out["claims"]
    assert c["agree-claim"] == {"outcome_a": "passed", "outcome_b": "passed",
                                "status": "agree"}
    assert c["differ-claim"] == {"outcome_a": "passed", "outcome_b": "failed",
                                 "status": "differ"}
    assert c["only-a-claim"] == {"outcome_a": "passed", "outcome_b": None,
                                 "status": "missing_in_b"}
    assert c["only-b-claim"] == {"outcome_a": None, "outcome_b": "errored",
                                 "status": "missing_in_a"}


def test_latest_run_wins_per_claim_env():
    """Two runs for the SAME (claim, env): the later finished_at is compared."""
    rows = [
        _row("c", ENV_A, "failed", at=9),    # earlier
        _row("c", ENV_A, "passed", at=12),   # later → wins
        _row("c", ENV_B, "passed", at=11),
    ]
    out = _aggregate_result_diff(rows, ENV_A, ENV_B)
    assert out["claims"]["c"] == {"outcome_a": "passed", "outcome_b": "passed",
                                  "status": "agree"}


def test_env_grouping_is_directional():
    """outcome_a comes from env_a's run, outcome_b from env_b's — not swapped."""
    rows = [_row("c", ENV_A, "failed", at=10), _row("c", ENV_B, "passed", at=10)]
    out = _aggregate_result_diff(rows, ENV_A, ENV_B)
    assert out["claims"]["c"]["outcome_a"] == "failed"
    assert out["claims"]["c"]["outcome_b"] == "passed"
    assert out["claims"]["c"]["status"] == "differ"


def test_unrelated_env_ignored():
    """A run from a third environment is not one of the two — dropped."""
    rows = [
        _row("c", ENV_A, "passed", at=10),
        _row("c", 999, "failed", at=11),       # not env_a/env_b → ignored
    ]
    out = _aggregate_result_diff(rows, ENV_A, ENV_B)
    assert out["claims"]["c"]["status"] == "missing_in_b"  # only env_a present


def test_empty():
    out = _aggregate_result_diff([], ENV_A, ENV_B)
    assert out == {"claims": {},
                   "totals": {"agree": 0, "differ": 0,
                              "missing_in_a": 0, "missing_in_b": 0}}
