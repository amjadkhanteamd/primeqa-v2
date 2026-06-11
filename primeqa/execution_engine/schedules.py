"""Substrate-4 run schedules — store + due logic (Build 6, D-214).

The third automated execution trigger on the new engine: on-approval (D-199)
and the CI release gate (D-198.3 + webhook enqueue) already exist; this module
drives the SCHEDULED regression trigger. One ``s4_run_schedules`` row = one
cron cadence for one environment; the scheduler's ``s4_schedule_tick`` fires
due schedules by enqueueing every currently-approved claim on the environment
(``enqueue_all_approved_claims`` — the D-199 enqueue path, idempotent per the
job store's active-set dedup).

Due semantics are LATENESS-TOLERANT: a schedule is due when the next cron
occurrence after ``last_fired_at`` (or ``created_at`` for a never-fired
schedule) is in the past — it fires ONCE per tick regardless of how many
windows were missed (never backfills), then stamps ``last_fired_at = now``.

Production environments never fire (D-214 §4) — the tick checks the env's
``is_production`` flag; scheduled runs are a sandbox regression instrument.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunSchedule:
    id: int
    environment_id: int
    cron_expr: str
    enabled: bool
    last_fired_at: Optional[datetime]
    created_by: Optional[int]
    created_at: datetime


def is_due(cron_expr: str, last_fired_at: Optional[datetime],
           created_at: datetime, now: Optional[datetime] = None) -> bool:
    """Whether the schedule's next cron occurrence after its baseline
    (``last_fired_at``, else ``created_at``) has passed. Pure; an invalid
    cron expression is never-due (logged, not raised — one bad row must not
    poison the tick)."""
    from croniter import croniter

    now = now or datetime.now(timezone.utc)
    base = last_fired_at or created_at
    try:
        nxt = croniter(cron_expr, base).get_next(datetime)
    except Exception as exc:
        log.warning("invalid cron_expr %r: %s", cron_expr, exc)
        return False
    return nxt <= now


_ROW_COLS = ("id, environment_id, cron_expr, enabled, last_fired_at, "
             "created_by, created_at")


def _row_to_schedule(r) -> RunSchedule:
    return RunSchedule(id=r.id, environment_id=r.environment_id,
                       cron_expr=r.cron_expr, enabled=r.enabled,
                       last_fired_at=r.last_fired_at,
                       created_by=r.created_by, created_at=r.created_at)


class RunScheduleStore:
    """Per-tenant CRUD over ``s4_run_schedules`` (tenant schema via
    ``get_tenant_connection`` — each method is its own committed transaction,
    the job-store idiom)."""

    def __init__(self, tenant_id: int):
        self._tenant_id = tenant_id

    def _conn(self):
        from primeqa.semantic.connection import get_tenant_connection
        return get_tenant_connection(self._tenant_id)

    def create(self, *, environment_id: int, cron_expr: str,
               created_by: Optional[int] = None) -> RunSchedule:
        with self._conn() as conn:
            r = conn.execute(text(
                f"INSERT INTO s4_run_schedules (environment_id, cron_expr, created_by) "
                f"VALUES (:eid, :cron, :by) "
                f"ON CONFLICT ON CONSTRAINT uq_s4_run_schedules_env_cron "
                f"DO UPDATE SET enabled = TRUE "
                f"RETURNING {_ROW_COLS}"),
                {"eid": environment_id, "cron": cron_expr, "by": created_by},
            ).one()
            return _row_to_schedule(r)

    def list(self) -> list[RunSchedule]:
        with self._conn() as conn:
            rows = conn.execute(text(
                f"SELECT {_ROW_COLS} FROM s4_run_schedules ORDER BY id")).all()
        return [_row_to_schedule(r) for r in rows]

    def set_enabled(self, schedule_id: int, enabled: bool) -> bool:
        with self._conn() as conn:
            n = conn.execute(text(
                "UPDATE s4_run_schedules SET enabled = :en WHERE id = :sid"),
                {"en": enabled, "sid": schedule_id}).rowcount
        return n == 1

    def delete(self, schedule_id: int) -> bool:
        with self._conn() as conn:
            n = conn.execute(text(
                "DELETE FROM s4_run_schedules WHERE id = :sid"),
                {"sid": schedule_id}).rowcount
        return n == 1

    def stamp_fired(self, schedule_id: int,
                    fired_at: Optional[datetime] = None) -> None:
        with self._conn() as conn:
            conn.execute(text(
                "UPDATE s4_run_schedules SET last_fired_at = :at WHERE id = :sid"),
                {"at": fired_at or datetime.now(timezone.utc),
                 "sid": schedule_id})


def fire_due_schedules(tenant_id: int, *, production_env_ids: set,
                       now: Optional[datetime] = None,
                       enqueue=None, store: Optional[RunScheduleStore] = None,
                       ) -> dict:
    """One tenant's tick body: fire every due, enabled, non-production
    schedule. ``enqueue`` defaults to
    :func:`primeqa.intelligence.s4_execution_console.enqueue_all_approved_claims`;
    a test injects a fake. Per-schedule try/except — one schedule's failure
    never starves the rest. Returns ``{fired: [...ids], enqueued: int}``."""
    if enqueue is None:
        from primeqa.intelligence.s4_execution_console import (
            enqueue_all_approved_claims,
        )
        enqueue = enqueue_all_approved_claims
    store = store or RunScheduleStore(tenant_id)
    fired, enqueued = [], 0
    for sched in store.list():
        if not sched.enabled:
            continue
        if sched.environment_id in production_env_ids:
            continue                                  # D-214 §4: sandbox-only
        if not is_due(sched.cron_expr, sched.last_fired_at,
                      sched.created_at, now=now):
            continue
        try:
            out = enqueue(tenant_id, sched.environment_id)
            store.stamp_fired(sched.id, now)
            fired.append(sched.id)
            enqueued += len(out.get("enqueued") or ())
        except Exception as exc:
            log.warning("s4 schedule %s (tenant %s) failed to fire: %s",
                        sched.id, tenant_id, exc)
    return {"fired": fired, "enqueued": enqueued}
