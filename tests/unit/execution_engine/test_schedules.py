"""Unit: D-214 scheduled substrate runs — due logic + the tick body (no DB)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from primeqa.execution_engine.schedules import (
    RunSchedule,
    fire_due_schedules,
    is_due,
)

_NOW = datetime(2026, 6, 11, 11, 55, tzinfo=timezone.utc)
_CREATED = _NOW - timedelta(hours=3)


def _sched(sid, env=59, cron="0 * * * *", enabled=True, last=None):
    return RunSchedule(id=sid, environment_id=env, cron_expr=cron,
                       enabled=enabled, last_fired_at=last,
                       created_by=None, created_at=_CREATED)


class _FakeStore:
    def __init__(self, schedules):
        self._schedules = schedules
        self.stamped = []

    def list(self):
        return list(self._schedules)

    def stamp_fired(self, sid, fired_at=None):
        self.stamped.append(sid)


# ---------------------------------------------------------------------------
# is_due
# ---------------------------------------------------------------------------

def test_is_due_semantics():
    assert is_due("0 * * * *", None, _CREATED, now=_NOW)            # never fired
    assert not is_due("0 * * * *", _NOW - timedelta(minutes=10),
                      _CREATED, now=_NOW)                            # window not reached
    assert is_due("0 * * * *", _NOW - timedelta(hours=2),
                  _CREATED, now=_NOW)                                # overdue
    assert not is_due("garbage", None, _CREATED, now=_NOW)           # invalid: never due


# ---------------------------------------------------------------------------
# fire_due_schedules
# ---------------------------------------------------------------------------

def test_fires_due_and_stamps():
    store = _FakeStore([_sched(1), _sched(2, last=_NOW - timedelta(minutes=10))])
    calls = []
    out = fire_due_schedules(
        1, production_env_ids=set(), now=_NOW, store=store,
        enqueue=lambda t, e: calls.append((t, e)) or {"enqueued": [101, 102]})
    assert out["fired"] == [1]                  # 2 is inside its window
    assert out["enqueued"] == 2
    assert calls == [(1, 59)]
    assert store.stamped == [1]


def test_disabled_and_production_never_fire():
    store = _FakeStore([_sched(1, enabled=False), _sched(2, env=99)])
    out = fire_due_schedules(
        1, production_env_ids={99}, now=_NOW, store=store,
        enqueue=lambda t, e: (_ for _ in ()).throw(AssertionError("fired")))
    assert out["fired"] == []
    assert store.stamped == []


def test_one_failing_schedule_never_starves_the_rest():
    store = _FakeStore([_sched(1, env=10), _sched(2, env=20)])

    def enqueue(t, e):
        if e == 10:
            raise RuntimeError("boom")
        return {"enqueued": [7]}

    out = fire_due_schedules(1, production_env_ids=set(), now=_NOW,
                             store=store, enqueue=enqueue)
    assert out["fired"] == [2]                  # 1 failed, 2 still fired
    assert store.stamped == [2]                 # the failure is NOT stamped (retries next tick)
