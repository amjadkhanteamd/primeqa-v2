"""Integration: the D-214 s4_run_schedules store round-trip (local PG; the
tenant alembic chain in this suite's conftest creates the table)."""
from __future__ import annotations

from datetime import datetime, timezone

from primeqa.execution_engine.schedules import RunScheduleStore

from .conftest import TEST_TENANT_ID


def test_store_round_trip(db_setup):
    store = RunScheduleStore(TEST_TENANT_ID)
    s = store.create(environment_id=59, cron_expr="0 6 * * *", created_by=1)
    assert s.id and s.enabled and s.last_fired_at is None

    listed = [x for x in store.list() if x.id == s.id]
    assert listed and listed[0].cron_expr == "0 6 * * *"

    # create is idempotent on (env, cron) — re-create re-enables, no duplicate
    store.set_enabled(s.id, False)
    again = store.create(environment_id=59, cron_expr="0 6 * * *")
    assert again.id == s.id and again.enabled

    now = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)
    store.stamp_fired(s.id, now)
    assert [x for x in store.list() if x.id == s.id][0].last_fired_at == now

    assert store.set_enabled(s.id, False)
    assert not [x for x in store.list() if x.id == s.id][0].enabled
    assert store.delete(s.id)
    assert not [x for x in store.list() if x.id == s.id]
