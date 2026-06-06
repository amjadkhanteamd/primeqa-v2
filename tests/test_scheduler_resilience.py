"""Scheduler crash-resilience (D-178, the 1d/#119 outage fix).

Roots out the bug that caused a 15h S1-sync outage: an unguarded tick exception
killed the whole scheduler process, so the s1 reaper never ran and an orphaned
sync job was never resumed. These tests are hermetic (every tick is mocked; no
DB, no env).

Run: python tests/test_scheduler_resilience.py
"""
import contextlib
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import primeqa.scheduler as sched

# The full tick sequence scheduler_tick runs, in order. The s1 ticks are LAST.
_TICK_NAMES = [
    "reap_stuck_stages", "reap_stuck_slots", "reap_stuck_runs", "reap_orphan_rtrs",
    "reap_stale_workers", "fire_scheduled_runs", "dead_mans_switch_check",
    "reap_stalled_metadata_jobs", "reap_stale_generation_jobs",
    "s3_reaper_tick", "s4_reaper_tick", "s8_grounding_tick",
    "s1_sync_enqueuer_tick", "s1_sync_reaper_tick", "trim_run_events",
]


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_scheduler_tick_isolates_a_failing_tick():
    """An early tick raising must NOT skip the later ticks — especially the s1
    sync enqueuer/reaper, which run last. This is the starvation the outage hit."""
    mocks = {name: mock.MagicMock(name=name) for name in _TICK_NAMES}
    # An EARLY tick throws (s8_grounding_tick is 12th of 15, before both s1 ticks).
    mocks["s8_grounding_tick"].side_effect = RuntimeError("boom")
    with contextlib.ExitStack() as stack:
        for name, m in mocks.items():
            stack.enter_context(mock.patch.object(sched, name, m))
        sched.scheduler_tick({"db": mock.MagicMock()})   # must not raise
    # Every tick ran exactly once despite the early failure.
    for name, m in mocks.items():
        _assert(m.call_count == 1, f"{name} called {m.call_count}x (expected 1)")
    # The two s1 ticks (after the failing one) genuinely ran.
    _assert(mocks["s1_sync_enqueuer_tick"].called, "s1 enqueuer starved by earlier failure")
    _assert(mocks["s1_sync_reaper_tick"].called, "s1 reaper starved by earlier failure")


def test_run_scheduler_survives_a_tick_exception():
    """A scheduler_tick exception must not kill the process: run_scheduler logs,
    rolls back the shared session, and continues — exiting only on KeyboardInterrupt."""
    db = mock.MagicMock()
    ctx = {"db": db}
    # First iteration raises (transient failure); second iteration breaks the loop.
    tick = mock.MagicMock(side_effect=[RuntimeError("transient db blip"), KeyboardInterrupt()])
    with mock.patch.object(sched, "create_scheduler_context", return_value=ctx), \
         mock.patch.object(sched, "scheduler_tick", tick), \
         mock.patch.object(sched, "time") as fake_time:
        fake_time.sleep = mock.MagicMock()
        # Must return cleanly (KeyboardInterrupt handled), NOT propagate RuntimeError.
        sched.run_scheduler()
    _assert(tick.call_count == 2, f"scheduler_tick called {tick.call_count}x (expected 2)")
    # The transient failure triggered a rollback (poisoned-tx recovery) and we continued.
    _assert(db.rollback.called, "ctx db.rollback not called after a tick failure")
    _assert(db.close.called, "ctx db.close not called in finally")


def test_run_scheduler_clean_exit_calls_close():
    """Even with no failures, the finally closes the session."""
    db = mock.MagicMock()
    ctx = {"db": db}
    tick = mock.MagicMock(side_effect=[KeyboardInterrupt()])
    with mock.patch.object(sched, "create_scheduler_context", return_value=ctx), \
         mock.patch.object(sched, "scheduler_tick", tick), \
         mock.patch.object(sched, "time") as fake_time:
        fake_time.sleep = mock.MagicMock()
        sched.run_scheduler()
    _assert(not db.rollback.called, "rollback should NOT be called on a clean exit")
    _assert(db.close.called, "ctx db.close not called in finally")


def run_tests():
    tests = [
        test_scheduler_tick_isolates_a_failing_tick,
        test_run_scheduler_survives_a_tick_exception,
        test_run_scheduler_clean_exit_calls_close,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL  {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    return passed == len(tests)


if __name__ == "__main__":
    sys.exit(0 if run_tests() else 1)
