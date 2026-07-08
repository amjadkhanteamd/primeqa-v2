"""UI Pass 1 — pure-helper red-proofs for the sync-history / run-detail readers
and the batched cost+model roll-up. No DB: the I/O wrappers are covered by the
live red-proofs; these pin the SHAPING (envelope math, cost/model attribution,
the honest "-" on zero-summary, gap_details passthrough).
"""
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.unit

from primeqa.metadata_bridge.s1_sync_console import (
    _duration_s, _ai_cost, _shape_history_run, _shape_run_detail,
    _history_envelope,
)
from primeqa.intelligence.llm.usage import get_sync_run_costs_batch


# --- _duration_s -------------------------------------------------------------

def test_duration_both_present():
    a = datetime(2026, 6, 24, 10, 0, 0, tzinfo=timezone.utc)
    b = datetime(2026, 6, 24, 10, 5, 30, tzinfo=timezone.utc)
    assert _duration_s(a, b) == 330


def test_duration_missing_completed_is_none():
    a = datetime(2026, 6, 24, 10, 0, 0, tzinfo=timezone.utc)
    assert _duration_s(a, None) is None
    assert _duration_s(None, a) is None


# --- _ai_cost (embeddings + summaries, NOT total) ----------------------------

def test_ai_cost_none_is_zero():
    assert _ai_cost(None) == 0.0


def test_ai_cost_sums_embedding_and_summary_only():
    cost = {"embedding": {"cost_usd": 0.0125}, "summary": {"cost_usd": 0.0307},
            "total_cost_usd": 0.99}  # total deliberately bigger — must be ignored
    assert _ai_cost(cost) == pytest.approx(0.0432)


# --- _shape_history_run ------------------------------------------------------

def _hrow(**over):
    base = dict(
        id="11111111-1111-1111-1111-111111111111", status="success",
        started_at=datetime(2026, 6, 24, 10, 0, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 6, 24, 10, 2, 0, tzinfo=timezone.utc),
        skipped=False, entities_inserted=5, entities_superseded=3,
        entities_unchanged=10, edges_inserted=7, embeddings_generated=4,
        summaries_generated=2, describe_calls=12, failure_category=None,
        sf_error_code=None, permission_gaps=0, has_error=False,
        attempt_passes=1, active_seconds=115)
    base.update(over)
    return base


def test_history_run_attributes_model_and_cost():
    cost = {"embedding": {"cost_usd": 0.01}, "summary": {"cost_usd": 0.02},
            "summary_models": ["claude-haiku-4-5-20251001"]}
    out = _shape_history_run(_hrow(), cost)
    assert out["duration_s"] == 120
    assert out["ai_cost_usd"] == pytest.approx(0.03)
    assert out["summary_models"] == ["claude-haiku-4-5-20251001"]
    assert out["status"] == "success"
    assert out["entities_inserted"] == 5


def test_history_run_zero_summary_has_empty_model_list():
    # the honest "-": no summary rows -> [] (template renders an em dash, never the
    # current SUMMARY_MODEL setting).
    out = _shape_history_run(_hrow(), {"embedding": {"cost_usd": 0.0},
                                       "summary": {"cost_usd": 0.0},
                                       "summary_models": []})
    assert out["summary_models"] == []
    assert out["ai_cost_usd"] == 0.0


def test_history_run_no_cost_row_is_zero_and_dash():
    out = _shape_history_run(_hrow(skipped=True), None)
    assert out["ai_cost_usd"] == 0.0
    assert out["summary_models"] == []
    assert out["skipped"] is True


def test_history_run_typed_failure_passthrough():
    out = _shape_history_run(
        _hrow(status="failure", failure_category="permission",
              sf_error_code="INSUFFICIENT_FIELD_ACCESS", has_error=True), None)
    assert out["failure_category"] == "permission"
    assert out["sf_error_code"] == "INSUFFICIENT_FIELD_ACCESS"
    assert out["has_error"] is True


# --- _history_envelope (pagination math) -------------------------------------

def test_history_run_pass_accounting_passthrough():
    """D-341: attempt_passes + active_seconds ride through to the UI; a
    legacy pre-migration row (attempt_passes=0) keeps them at zero so the
    template falls back to wall-clock duration_s."""
    out = _shape_history_run(_hrow(attempt_passes=2, active_seconds=497), None)
    assert out["attempt_passes"] == 2
    assert out["active_seconds"] == 497
    assert out["duration_s"] == 120           # wall clock still reported
    legacy = _shape_history_run(_hrow(attempt_passes=0, active_seconds=0), None)
    assert legacy["attempt_passes"] == 0 and legacy["active_seconds"] == 0


def test_envelope_pagination_math():
    env = _history_envelope(runs=[1, 2], total=45, page=2, per_page=20)
    assert env["available"] is True and env["provisioned"] is True
    assert env["total_pages"] == 3            # ceil(45/20)
    assert env["page"] == 2 and env["per_page"] == 20


def test_envelope_zero_total_one_page():
    assert _history_envelope([], 0, 1, 20)["total_pages"] == 1


# --- _shape_run_detail (gap_details passthrough) -----------------------------

def _drow(**over):
    base = dict(
        id="22222222-2222-2222-2222-222222222222", status="partial_success",
        started_at=datetime(2026, 6, 24, 9, 0, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 6, 24, 9, 3, 0, tzinfo=timezone.utc),
        skipped=False, phase="done", last_completed_phase="Flow",
        logical_version_seq=82, entities_inserted=1, entities_superseded=2,
        entities_unchanged=3, edges_inserted=4, edges_superseded=0,
        embeddings_generated=1, summaries_generated=1, summaries_failed=0,
        describe_calls=10, failure_category=None, sf_error_code=None,
        permission_gaps=1, gap_details=[{"site": "fetch_users", "category": "permission",
                                         "sf_error_code": "INSUFFICIENT_ACCESS",
                                         "context": {"dropped": "edges"}}],
        error_message=None, attempt_passes=1, active_seconds=170)
    base.update(over)
    return base


def test_run_detail_gap_details_passthrough():
    out = _shape_run_detail(_drow(), {"summary_models": ["m"]})
    assert out["permission_gaps"] == 1
    assert isinstance(out["gap_details"], list) and out["gap_details"][0]["site"] == "fetch_users"
    assert out["summary_models"] == ["m"]
    assert out["duration_s"] == 180


def test_run_detail_null_gap_details_becomes_empty_list():
    out = _shape_run_detail(_drow(gap_details=None, permission_gaps=0), None)
    assert out["gap_details"] == []
    assert out["summary_models"] == []


# --- get_sync_run_costs_batch empty short-circuit (no DB) ---------------------

def test_costs_batch_empty_input_is_empty_dict():
    assert get_sync_run_costs_batch([]) == {}
    assert get_sync_run_costs_batch(None) == {}
    assert get_sync_run_costs_batch([None, ""]) == {}
