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
    # Step 4 (LLD_STEP_4 §d, §e): a schedule references a plan template and
    # carries its own authority ("claim this schedule"); the tick records
    # what it did last — a plan id, or a refusal with its reason.
    plan_template: Optional[dict] = None
    authorised_by: Optional[int] = None
    authorised_at: Optional[datetime] = None
    last_plan_id: Optional[str] = None
    last_refusal: Optional[str] = None
    last_refused_at: Optional[datetime] = None

    @property
    def authority(self) -> Optional[int]:
        """Ruling 2: authorised_by else created_by; None → the tick refuses."""
        return self.authorised_by or self.created_by


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
             "created_by, created_at, plan_template, authorised_by, authorised_at, "
             "last_plan_id, last_refusal, last_refused_at")


def _row_to_schedule(r) -> RunSchedule:
    return RunSchedule(id=r.id, environment_id=r.environment_id,
                       cron_expr=r.cron_expr, enabled=r.enabled,
                       last_fired_at=r.last_fired_at,
                       created_by=r.created_by, created_at=r.created_at,
                       plan_template=r.plan_template, authorised_by=r.authorised_by,
                       authorised_at=r.authorised_at,
                       last_plan_id=(str(r.last_plan_id) if r.last_plan_id else None),
                       last_refusal=r.last_refusal, last_refused_at=r.last_refused_at)


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

    # -- Step 4 ------------------------------------------------------------
    def get(self, schedule_id: int) -> Optional[RunSchedule]:
        with self._conn() as conn:
            r = conn.execute(text(
                f"SELECT {_ROW_COLS} FROM s4_run_schedules WHERE id = :sid"),
                {"sid": schedule_id}).first()
        return _row_to_schedule(r) if r else None

    def claim(self, schedule_id: int, *, user_id: int) -> bool:
        """"Claim this schedule" (ruling 2): a person takes the authority a
        schedule fires under. Audited by the route."""
        with self._conn() as conn:
            n = conn.execute(text(
                "UPDATE s4_run_schedules SET authorised_by = :u, authorised_at = now() "
                "WHERE id = :sid"), {"u": user_id, "sid": schedule_id}).rowcount
        return n == 1

    def set_template(self, schedule_id: int, template: Optional[dict]) -> bool:
        import json as _json
        with self._conn() as conn:
            n = conn.execute(text(
                "UPDATE s4_run_schedules SET plan_template = CAST(:t AS JSONB) WHERE id = :sid"),
                {"t": (_json.dumps(template) if template is not None else None),
                 "sid": schedule_id}).rowcount
        return n == 1

    def record_plan(self, schedule_id: int, plan_id: str,
                    fired_at: Optional[datetime] = None) -> None:
        with self._conn() as conn:
            conn.execute(text(
                "UPDATE s4_run_schedules SET last_fired_at = :at, last_plan_id = CAST(:p AS uuid), "
                "last_refusal = NULL, last_refused_at = NULL WHERE id = :sid"),
                {"at": fired_at or datetime.now(timezone.utc), "p": str(plan_id),
                 "sid": schedule_id})

    def record_refusal(self, schedule_id: int, reason: str,
                       at: Optional[datetime] = None) -> None:
        with self._conn() as conn:
            conn.execute(text(
                "UPDATE s4_run_schedules SET last_refusal = :r, last_refused_at = :at WHERE id = :sid"),
                {"r": reason, "at": at or datetime.now(timezone.utc), "sid": schedule_id})

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
    schedule. Step 4 (LLD_STEP_4 §d, §e): what the cadence runs is a PLAN —
    the tick records a ``run_plans`` row for the schedule's template under
    the schedule's borrowed authority and executes THAT plan; a schedule
    with no authority (``authorised_by`` and ``created_by`` both NULL) is
    REFUSED loudly — no plan, the refusal recorded on the row, audited —
    with the exit "claim this schedule" (ruling 2). ``enqueue`` is the
    legacy seam the pre-Step-4 tests inject; when given, the old
    every-approved-claim enqueue runs instead (never in production, which
    passes nothing). Per-schedule try/except — one schedule's failure never
    starves the rest. Returns ``{fired, enqueued, plans, refused}``."""
    if store is None:
        # Probe only the real-store path — injected stores are DB-free fakes.
        from primeqa.semantic.connection import get_tenant_connection
        from primeqa.shared.stale_tenants import skip_unprovisioned
        with get_tenant_connection(tenant_id) as conn:
            if skip_unprovisioned(conn, tenant_id, "s4_run_schedules", log):
                return {"fired": [], "enqueued": 0, "unprovisioned": True}
        store = RunScheduleStore(tenant_id)
    fired, enqueued, plans, refused = [], 0, [], []
    for sched in store.list():
        if not sched.enabled:
            continue
        if sched.environment_id in production_env_ids:
            continue                                  # D-214 §4: sandbox-only
        if not is_due(sched.cron_expr, sched.last_fired_at,
                      sched.created_at, now=now):
            continue
        if enqueue is not None:                       # the legacy seam (tests only)
            try:
                out = enqueue(tenant_id, sched.environment_id)
                store.stamp_fired(sched.id, now)
                fired.append(sched.id)
                enqueued += len(out.get("enqueued") or ())
            except Exception as exc:
                log.warning("s4 schedule %s (tenant %s) failed to fire: %s",
                            sched.id, tenant_id, exc)
            continue
        if sched.authority is None:                   # ruling 2: refuse loudly, no plan
            reason = "no authorising user — claim this schedule"
            store.record_refusal(sched.id, reason, now)
            _audit_refusal(tenant_id, sched.id, reason)
            log.warning("s4 schedule %s (tenant %s) REFUSED: %s", sched.id, tenant_id, reason)
            refused.append({"id": sched.id, "reason": reason})
            continue
        try:
            plan_id, receipt = plan_and_execute_schedule(tenant_id, sched, now=now)
            store.record_plan(sched.id, plan_id, now)
            fired.append(sched.id)
            plans.append(plan_id)
            enqueued += len(receipt.get("s4_jobs") or ()) + len(receipt.get("ui_jobs") or ())
        except Exception as exc:
            log.warning("s4 schedule %s (tenant %s) failed to fire: %s",
                        sched.id, tenant_id, exc)
            try:
                store.record_refusal(sched.id, f"fire failed: {exc}"[:300], now)
            except Exception:  # noqa: BLE001
                pass
    return {"fired": fired, "enqueued": enqueued, "plans": plans, "refused": refused}


def _audit_refusal(tenant_id: int, schedule_id: int, reason: str) -> None:
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            conn.execute(text(
                "INSERT INTO public.activity_log (tenant_id, user_id, action, entity_type, entity_id, details) "
                "VALUES (:t, NULL, 's4.schedule.refused', 's4_run_schedule', :sid, CAST(:d AS JSONB))"),
                {"t": tenant_id, "sid": schedule_id,
                 "d": __import__("json").dumps({"schedule_id": schedule_id, "reason": reason,
                                                   "exit": "claim this schedule"})})
    except Exception as exc:  # noqa: BLE001
        log.warning("schedule refusal audit not written: %s", exc)


def plan_and_execute_schedule(tenant_id: int, sched: RunSchedule, *,
                              now: Optional[datetime] = None) -> tuple:
    """Record the plan for the schedule's template (committed first), then
    execute THAT plan by id in a second transaction ("Run this plan" —
    the same act a person makes). Returns ``(plan_id, receipt)``."""
    from sqlalchemy.orm import Session

    from primeqa.execution_engine import planner
    from primeqa.semantic.connection import get_tenant_connection
    with get_tenant_connection(tenant_id) as conn:
        s = Session(bind=conn)
        try:
            row = planner.plan(s, tenant_id=tenant_id,
                               scope={"scope_kind": planner.SCOPE_SCHEDULE, "schedule_id": sched.id})
            plan_id = row["id"]; authority = row["authorised_by"]
            role = conn.execute(text("SELECT role FROM public.users WHERE id = :u"),
                                {"u": authority}).scalar() if authority else None
            s.flush()
        finally:
            s.close()
    subject = {"user_id": authority, "tenant_id": tenant_id, "role": role or "tester"}
    with get_tenant_connection(tenant_id) as conn:
        s = Session(bind=conn)
        try:
            receipt = planner.execute_plan(
                s, tenant_id=tenant_id, plan_id=plan_id, executed_by=None, subject=subject,
                trigger_extra={"scheduled_by_schedule": sched.id, "authorised_by_user": authority})
            s.flush()
        finally:
            s.close()
    return plan_id, receipt
