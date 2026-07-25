"""D-315/D-316 — the requirement Test-plan "Generation history" table shaper.

``_shape_run_row`` is pure: it turns one generation run (an attempt row joined to
its job, outcome, and LLM cost) into a table-row dict, merging the run status with
the outcome into a single label/tone + a detail (refusal reason or failure error).
DB-free, so every case is a plain-dict unit test. The list reader
(``_read_generation_runs``) is covered by boot + the integration console suite;
the attribution SQL is documented at the call site.
"""
from __future__ import annotations

from datetime import datetime, timezone

from primeqa.intelligence.s3_generation_console import (
    _GENERATION_TASK,
    _shape_run_row,
)


def _dt(sec: int) -> datetime:
    return datetime(2026, 7, 4, 12, 0, sec, tzinfo=timezone.utc)


def _row(**over):
    base = {
        "attempt_no": 1, "attempt_status": "completed", "attempt_error_code": None,
        "started_at": _dt(1), "finished_at": _dt(13), "s1_version_seq": 110,
        "job_error_message": None, "outcome_kind": "draft",
        "claims_written": [{"i": i} for i in range(22)], "refusal_kind": None,
        "refusals": None, "equivalent_existing": None,
        "models": ["claude-sonnet-5"], "calls": 3, "cost_usd": 0.0123,
        "in_tok": 1000, "out_tok": 4233,
    }
    base.update(over)
    return base


def test_generated_run_maps_to_green_with_tests_and_cost():
    r = _shape_run_row(_row(), now=_dt(30))
    assert r["status_label"] == "generated" and r["status_tone"] == "green"
    assert r["tests_written"] == 22
    assert r["duration_s"] == 12.0                      # finished(13) - started(1)
    assert r["llm"]["cost_usd"] == 0.0123
    assert r["llm"]["models"] == ["claude-sonnet-5"]
    assert r["s1_version_seq"] == 110 and r["attempt_no"] == 1
    assert r["detail"] is None


def test_refusal_maps_to_amber_with_the_reason_as_detail():
    r = _shape_run_row(_row(
        outcome_kind="refusal", claims_written=[], refusal_kind="structural-validation-failure",
        refusals=[{"reason": "x"}]), now=_dt(30))
    assert r["status_label"] == "refused" and r["status_tone"] == "amber"
    assert r["tests_written"] == 0
    assert r["detail"]                                   # a human refusal reason string


def test_failed_run_maps_to_red_with_the_error_as_detail():
    r = _shape_run_row(_row(
        attempt_status="failed", outcome_kind=None, claims_written=None,
        attempt_error_code="llm_error", calls=0, models=None), now=_dt(30))
    assert r["status_label"] == "failed" and r["status_tone"] == "red"
    assert r["detail"] == "llm_error"
    assert r["llm"] is None                              # no LLM calls recorded


def test_failed_falls_back_to_job_error_message_when_no_attempt_code():
    r = _shape_run_row(_row(
        attempt_status="failed", outcome_kind=None, claims_written=None,
        attempt_error_code=None, job_error_message="provider 503", calls=0), now=_dt(30))
    assert r["detail"] == "provider 503"


def test_running_attempt_uses_now_for_duration_and_indigo():
    r = _shape_run_row(_row(
        attempt_status="running", finished_at=None, outcome_kind=None,
        claims_written=None, calls=0, models=None), now=_dt(11))
    assert r["status_label"] == "running" and r["status_tone"] == "indigo"
    assert r["duration_s"] == 10.0                       # now(11) - started(1)


def test_draft_with_zero_tests_but_existing_match_reads_as_matched():
    r = _shape_run_row(_row(
        outcome_kind="draft", claims_written=[], equivalent_existing={"id": "x"}), now=_dt(30))
    assert r["status_label"] == "matched existing" and r["status_tone"] == "gray"


def test_draft_with_zero_tests_and_no_match_reads_as_no_new():
    r = _shape_run_row(_row(outcome_kind="draft", claims_written=[]), now=_dt(30))
    assert r["status_label"] == "no new tests"


def test_naive_now_does_not_crash_duration():
    # tz-naive now vs tz-aware started would raise on subtraction; swallowed to None.
    r = _shape_run_row(_row(attempt_status="running", finished_at=None),
                       now=datetime(2026, 7, 4, 12, 0, 30))  # naive
    assert r["duration_s"] is None


def test_generation_task_literal_matches_runtime():
    from primeqa.generation.run import GENERATION_TASK
    assert _GENERATION_TASK == GENERATION_TASK


def test_cost_attribution_is_by_request_key_never_timestamp_window():
    # D-390: per-attempt cost joins llm_usage_log on context->>'s3_request_id'
    # (stamped since 72eed6c). The old timestamp-window attribution silently
    # dropped 63% of generation rows and mis-attributed concurrent runs; a
    # revert to it must fail here. Source-level pin, matching the
    # _GENERATION_TASK drift-guard style.
    import inspect
    from primeqa.intelligence import s3_generation_console as console
    src = inspect.getsource(console)
    assert src.count("s3_request_id' = CAST(a.request_id AS text)") == 2, (
        "both cost joins (history table + run detail) must key on s3_request_id")
    assert "u.ts >= a.started_at" not in src, (
        "timestamp-window attribution is not an acceptable mechanism (D-390)")
