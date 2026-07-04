"""D-315 — the requirement Test-plan "Run status" panel shaper.

``_shape_run_status`` is pure: it assembles the panel dict from a job row, the
latest outcome row (or None), and the aggregated LLM row (or None). DB-free, so
every case is a plain-dict unit test. The DB reader
(``_read_generation_run_status``) is covered by boot + the integration console
suite; the attribution SQL is documented at the call site.
"""
from __future__ import annotations

from datetime import datetime, timezone

from primeqa.intelligence.s3_generation_console import (
    _GENERATION_TASK,
    _shape_run_status,
)


def _dt(sec: int) -> datetime:
    return datetime(2026, 7, 4, 12, 0, sec, tzinfo=timezone.utc)


def _llm(**over):
    base = {"models": ["claude-sonnet-5"], "calls": 3, "input_tokens": 1000,
            "output_tokens": 4233, "cost_usd": 0.0123, "prompt_version": "generation@v17"}
    base.update(over)
    return base


def _job(status="completed", **over):
    base = {"status": status, "attempt_count": 1, "created_at": _dt(0),
            "claimed_at": _dt(1), "started_at": _dt(2), "completed_at": _dt(13),
            "error_code": None, "error_message": None}
    base.update(over)
    return base


def test_completed_run_with_llm_shapes_all_fields():
    r = _shape_run_status(_job(attempt_count=2), {"outcome_kind": "draft", "n_tests": 22},
                          _llm(), now=_dt(30))
    assert r["present"] is True
    assert r["status"] == "completed" and r["active"] is False
    assert r["attempts"] == 2
    assert r["tests_written"] == 22 and r["outcome_kind"] == "draft"
    assert r["duration_s"] == 12.0                     # completed(13) - claimed(1)
    assert r["llm"]["cost_usd"] == 0.0123
    assert r["llm"]["models"] == ["claude-sonnet-5"]
    assert r["llm"]["prompt_version"] == "generation@v17"


def test_active_run_uses_now_for_duration_and_marks_active():
    r = _shape_run_status(
        _job(status="running", completed_at=None), None, None, now=_dt(11))
    assert r["active"] is True                          # running is non-terminal
    assert r["duration_s"] == 10.0                      # now(11) - claimed(1)
    assert r["tests_written"] is None                   # no outcome yet
    assert r["llm"] is None


def test_queued_never_claimed_has_no_duration():
    r = _shape_run_status(
        _job(status="queued", attempt_count=0, claimed_at=None,
             started_at=None, completed_at=None), None, None, now=_dt(5))
    assert r["active"] is True
    assert r["duration_s"] is None                      # nothing ran yet
    assert r["llm"] is None


def test_failed_run_surfaces_the_error_and_kind():
    r = _shape_run_status(
        _job(status="failed", attempt_count=3, completed_at=_dt(4),
             error_code="llm_error", error_message="provider 503"),
        {"outcome_kind": "refusal", "n_tests": 0}, None, now=_dt(9))
    assert r["status"] == "failed" and r["active"] is False
    assert r["error_message"] == "provider 503"
    assert r["outcome_kind"] == "refusal" and r["tests_written"] == 0
    assert r["duration_s"] == 3.0                       # completed(4) - claimed(1)


def test_naive_now_does_not_crash_duration():
    # A tz-naive `now` vs tz-aware claimed_at would raise on subtraction; the
    # shaper swallows it to None rather than 500 the requirement page.
    r = _shape_run_status(_job(status="running", completed_at=None), None, None,
                          now=datetime(2026, 7, 4, 12, 0, 30))  # naive
    assert r["duration_s"] is None


def test_generation_task_literal_matches_runtime():
    # The panel hardcodes 'generation' to avoid importing the generation runtime
    # on the requirement page; pin it to the real GENERATION_TASK so a rename
    # can't silently zero out the cost attribution.
    from primeqa.generation.run import GENERATION_TASK
    assert _GENERATION_TASK == GENERATION_TASK
