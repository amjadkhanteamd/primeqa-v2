"""D-450 pins: the TODAY comparison arm — run-date clocked, never wall-clocked.

Direction pins: the clock comes from the context alone (source-scan proves
the evaluator never consults the system clock; the stability pin evaluates
identical evidence "at two different times" and gets the same verdict);
strict ISO dates decide; blank / datetime / symbolic / clockless all refuse;
the fallback clock refuses the ±1 boundary window; realized payloads are the
evaluation state where captured.
"""
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

import primeqa.semantic.formula.eval as eval_mod
from primeqa.execution_engine.evidence import (
    CleanupRecord, CreateAttemptEvidence, RunEvidence)
from primeqa.interpretation.attribution import (
    _effective_state, _eval_context, _run_clock)
from primeqa.semantic.formula import (
    EvalContext, NonEvaluable, evaluate, parse)

pytestmark = pytest.mark.unit

_RUN = date(2026, 8, 12)
_CTX = EvalContext(run_date=_RUN)
_FB = EvalContext(run_date=_RUN, run_date_is_fallback=True)

_VR06 = ('ISPICKVAL(PLS_BM_Stage__c, "Approved") && '
         '(ISBLANK(PLS_BM_Contract_Start_Date__c) || '
         'PLS_BM_Contract_Start_Date__c < TODAY())')


def _ev(formula, payload, ctx=None):
    return evaluate(parse(formula), payload, context=ctx)


# ---------------------------------------------------------------------------
# The arm decides
# ---------------------------------------------------------------------------

def test_field_before_today_fires():
    assert _ev("D__c < TODAY()", {"D__c": "2026-08-11"}, _CTX) is True
    assert _ev("D__c < TODAY()", {"D__c": "2026-08-13"}, _CTX) is False


def test_today_on_the_left_flips():
    assert _ev("TODAY() > D__c", {"D__c": "2026-08-11"}, _CTX) is True
    assert _ev("TODAY() <= D__c", {"D__c": "2026-08-11"}, _CTX) is False


def test_vr06_real_formula_decides_with_the_arm():
    past = {"PLS_BM_Stage__c": "Approved",
            "PLS_BM_Contract_Start_Date__c": "2026-08-11"}
    future = {"PLS_BM_Stage__c": "Approved",
              "PLS_BM_Contract_Start_Date__c": "2026-08-20"}
    assert _ev(_VR06, past, _CTX) is True
    assert _ev(_VR06, future, _CTX) is False


# ---------------------------------------------------------------------------
# What refuses
# ---------------------------------------------------------------------------

def test_no_clock_refuses():
    out = _ev("D__c < TODAY()", {"D__c": "2026-08-11"})
    assert isinstance(out, NonEvaluable) and "run-date clock" in out.reason
    out2 = _ev("D__c < TODAY()", {"D__c": "2026-08-11"}, EvalContext())
    assert isinstance(out2, NonEvaluable)


def test_blank_refuses():
    out = _ev("D__c < TODAY()", {"D__c": None}, _CTX)
    assert isinstance(out, NonEvaluable) and "blank" in out.reason


def test_datetime_string_refuses():
    out = _ev("D__c < TODAY()", {"D__c": "2026-08-11T10:00:00Z"}, _CTX)
    assert isinstance(out, NonEvaluable) and "ISO date" in out.reason


def test_symbolic_token_refuses():
    tok = {"$relative_date": {"anchor": "RUN_DATE", "offset_days": -1}}
    out = _ev("D__c < TODAY()", {"D__c": tok}, _CTX)
    assert isinstance(out, NonEvaluable)


def test_fallback_clock_refuses_the_boundary_window_only():
    assert isinstance(_ev("D__c < TODAY()", {"D__c": "2026-08-12"}, _FB),
                      NonEvaluable)
    assert isinstance(_ev("D__c < TODAY()", {"D__c": "2026-08-11"}, _FB),
                      NonEvaluable)
    assert isinstance(_ev("D__c < TODAY()", {"D__c": "2026-08-13"}, _FB),
                      NonEvaluable)
    assert _ev("D__c < TODAY()", {"D__c": "2026-08-01"}, _FB) is True
    assert _ev("D__c < TODAY()", {"D__c": "2026-09-01"}, _FB) is False
    # the persisted reference has no such window:
    assert _ev("D__c < TODAY()", {"D__c": "2026-08-11"}, _CTX) is True


# ---------------------------------------------------------------------------
# Never the wall clock
# ---------------------------------------------------------------------------

def test_evaluator_source_never_consults_the_system_clock():
    import inspect
    src = inspect.getsource(eval_mod)
    assert "datetime.now" not in src
    assert "date.today" not in src


def _run_with(reference, started):
    step = CreateAttemptEvidence(
        step_id="c", ordinal=0, sobject="PLS_BM_Deal__c",
        field_values={"PLS_BM_Contract_Start_Date__c": "2026-08-11"},
        http_status=201, success=True, error_code=None, message=None,
        rejection_body=(), matched=False,
        cleanup=CleanupRecord(attempted=False),
        started_at=started, finished_at=started, duration_ms=1)
    return RunEvidence(
        run_id=uuid4(), recipe_id=uuid4(), recipe_version_seq=1,
        claim_test_id=uuid4(), claim_version_seq=None, environment_id=59,
        api_choice="rest", outcome="failed", started_at=started,
        finished_at=started, steps=(step,), temporal_reference=reference)


def test_reread_days_later_cannot_move_the_verdict():
    """The same evidence evaluated 'at two different wall-clock times':
    the context builder reads ONLY the evidence (persisted reference /
    started_at), so nothing about the verdict can depend on when
    attribution runs — asserted by building the context twice around a
    simulated multi-day gap and comparing verdicts."""
    ref = {"reference_date": "2026-08-12", "reference_timezone": "UTC",
           "captured_at": "2026-08-12T00:01:00+00:00", "source": "test"}
    ev = _run_with(ref, datetime(2026, 8, 12, tzinfo=timezone.utc))

    class _S1:
        pass
    ctx_now = _eval_context(ev.steps[0], ev, _S1())
    verdict_now = evaluate(parse("PLS_BM_Contract_Start_Date__c < TODAY()"),
                           _effective_state(ev.steps[0], ev), context=ctx_now)
    # ... days pass; attribution re-reads the SAME evidence (D-425.1 style)
    ctx_later = _eval_context(ev.steps[0], ev, _S1())
    verdict_later = evaluate(
        parse("PLS_BM_Contract_Start_Date__c < TODAY()"),
        _effective_state(ev.steps[0], ev), context=ctx_later)
    assert verdict_now is True and verdict_later is True
    assert ctx_now.run_date == ctx_later.run_date == date(2026, 8, 12)
    assert ctx_now.run_date_is_fallback is False


def test_run_clock_fallback_uses_started_at_and_flags_it():
    ev = _run_with(None, datetime(2026, 8, 12, 23, 50, tzinfo=timezone.utc))
    d, fb = _run_clock(ev)
    assert d == date(2026, 8, 12) and fb is True


# ---------------------------------------------------------------------------
# Realized payloads are the evaluation state
# ---------------------------------------------------------------------------

def test_effective_state_prefers_the_realized_payload():
    tok = {"$relative_date": {"anchor": "RUN_DATE", "offset_days": -1}}
    started = datetime(2026, 8, 12, tzinfo=timezone.utc)
    step = CreateAttemptEvidence(
        step_id="c", ordinal=0, sobject="X__c",
        field_values={"D__c": tok},
        http_status=201, success=True, error_code=None, message=None,
        rejection_body=(), matched=False,
        cleanup=CleanupRecord(attempted=False),
        started_at=started, finished_at=started, duration_ms=1,
        field_values_realized={"D__c": "2026-08-11"})
    ev = _run_with(None, started)
    state = _effective_state(step, ev)
    assert state == {"D__c": "2026-08-11"}
