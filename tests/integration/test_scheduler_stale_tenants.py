"""DB-real regression for the stale-tenant posture across the scheduler's
per-tenant ticks (the ``test_a0`` pattern from ``test_ui_schedules``): a
tenant without a provisioned schema skips loudly-once — no raise, no
per-tick log flood (FIX PLAN 2026-09-03; ``repair_triage_tick`` was the
observed flood, the rest are the sweep). Gated on S3A3_TEST_DATABASE_URL
(scratch: ``tenant_1`` provisioned, tenant 99 is not)."""
from __future__ import annotations

import logging
import os

import pytest

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DB, reason="set S3A3_TEST_DATABASE_URL"),
]
if DB:
    os.environ.setdefault("DATABASE_URL", DB)


@pytest.fixture(autouse=True)
def _bound_to_scratch():
    from primeqa.semantic import connection as C
    if DB.rsplit("/", 1)[-1] not in str(C.get_engine().url):
        pytest.skip("S1 engine already bound to a different database")


def _guard_warnings(records, table):
    return [r for r in records
            if "has no" in r.message and table in r.message
            and "skipped" in r.message]


def test_repair_triage_skips_unprovisioned_loudly_once(caplog):
    """The deploy-watch incident as a regression: repair triage on a tenant
    without the schema returns an honest no-op — never an UndefinedTable
    warning per 60s tick."""
    from primeqa.intelligence import repair_agent as RA
    from primeqa.shared import stale_tenants as ST
    ST._WARNED_UNPROVISIONED.discard((99, "s6_interpretations"))
    with caplog.at_level(logging.WARNING):
        out1 = RA.triage_new_failures(99)
        out2 = RA.triage_new_failures(99)
    assert out1 == {"proposed": 0, "scanned": 0, "unprovisioned": True}
    assert out2 == out1
    assert len(_guard_warnings(caplog.records, "s6_interpretations")) == 1
    assert not [r for r in caplog.records
                if "repair triage failed" in r.message]  # the old flood line


def test_repair_auto_apply_guard_when_flag_on(monkeypatch, caplog):
    """auto_apply is dormant by default (never reaches the DB); with the
    flag forced on, the guard skips the unprovisioned tenant loudly-once."""
    from primeqa.intelligence import repair_agent as RA
    from primeqa.shared import stale_tenants as ST
    ST._WARNED_UNPROVISIONED.discard((99, "repair_proposals"))
    monkeypatch.setattr(RA, "_repair_settings", lambda tid: {
        "auto_apply": True, "agent_enabled": True,
        "gate_apply_enabled": True, "max_attempts": 3})   # Step A: the switch too
    with caplog.at_level(logging.WARNING):
        out1 = RA.auto_apply_proposals(99)
        out2 = RA.auto_apply_proposals(99)
    assert out1 == {"applied": 0, "skipped": 0, "unprovisioned": True}
    assert out2 == out1
    assert len(_guard_warnings(caplog.records, "repair_proposals")) == 1


def test_s4_schedule_fire_skips_unprovisioned_loudly_once(caplog):
    from primeqa.execution_engine.schedules import fire_due_schedules
    from primeqa.shared import stale_tenants as ST
    ST._WARNED_UNPROVISIONED.discard((99, "s4_run_schedules"))
    with caplog.at_level(logging.WARNING):
        out1 = fire_due_schedules(99, production_env_ids=set())
        out2 = fire_due_schedules(99, production_env_ids=set())
    assert out1 == {"fired": [], "enqueued": 0, "unprovisioned": True}
    assert out2 == out1
    assert len(_guard_warnings(caplog.records, "s4_run_schedules")) == 1


def test_registry_runner_sweep_skips_loudly_once(caplog):
    """Every shared-registry per-tenant runner skips an unprovisioned
    tenant with {99: 0}, warns once per (tenant, table), and never emits
    its per-tenant 'failed' line. The two s1 runners share the
    s1_sync_jobs key — one warning covers both."""
    from primeqa.evolution.recompute import run_s8_grounding_tick
    from primeqa.execution_engine.consumer import (
        run_s4_cleanup_reaper_tick,
        run_s4_reaper_tick,
    )
    from primeqa.generation.consumer import run_s3_reaper_tick
    from primeqa.shared import stale_tenants as ST
    from primeqa.sync.consumer import (
        run_s1_sync_enqueuer_tick,
        run_s1_sync_reaper_tick,
    )
    cases = [
        (run_s3_reaper_tick, "s3_generation_jobs"),
        (run_s4_reaper_tick, "s4_execution_jobs"),
        (run_s4_cleanup_reaper_tick, "s4_created_records"),
        (run_s8_grounding_tick, "s8_grounding_validity"),
        (run_s1_sync_enqueuer_tick, "s1_sync_jobs"),
        (run_s1_sync_reaper_tick, "s1_sync_jobs"),
    ]
    for _, table in cases:
        ST._WARNED_UNPROVISIONED.discard((99, table))
    with caplog.at_level(logging.WARNING):
        for fn, _ in cases:
            assert fn([99]) == {99: 0}
            assert fn([99]) == {99: 0}           # second pass: no new warning
    for table in {t for _, t in cases}:
        assert len(_guard_warnings(caplog.records, table)) == 1
    assert not [r for r in caplog.records if "failed" in r.message]
