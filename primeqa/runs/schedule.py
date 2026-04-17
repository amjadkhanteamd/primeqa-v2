"""Scheduled-run primitives: model, repo, cron helpers, dead-man's-switch.

Per Q5: v1 schedules only test suites. Per Q9: presets dropdown + raw cron,
bidirectional.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from croniter import CroniterBadCronError, croniter
from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, String,
)
from sqlalchemy.sql import func

from primeqa.db import Base

log = logging.getLogger(__name__)


# ---- Cron UX helpers (Q9: presets + Advanced toggle) ------------------------

PRESETS: Dict[str, str] = {
    "hourly":      "0 * * * *",
    "daily_2am":   "0 2 * * *",
    "weekdays_2am":"0 2 * * 1-5",
    "weekly_sun":  "0 2 * * 0",
    "weekly_mon":  "0 9 * * 1",
}
PRESET_LABELS: Dict[str, str] = {
    "hourly":      "Every hour",
    "daily_2am":   "Daily at 2am",
    "weekdays_2am":"Weekdays at 2am",
    "weekly_sun":  "Weekly on Sunday at 2am",
    "weekly_mon":  "Weekly on Monday at 9am",
}


def preset_to_cron(preset: str) -> Optional[str]:
    return PRESETS.get(preset)


def cron_to_preset(cron_expr: str) -> Optional[str]:
    for preset, expr in PRESETS.items():
        if expr == (cron_expr or "").strip():
            return preset
    return None


def validate_cron(cron_expr: str) -> None:
    try:
        # Basic parse. croniter raises on bad syntax.
        croniter(cron_expr, datetime.now(timezone.utc))
    except (CroniterBadCronError, ValueError, KeyError) as e:
        raise ValueError(f"Invalid cron expression: {e}")


def next_fire(cron_expr: str, base: Optional[datetime] = None) -> datetime:
    base = base or datetime.now(timezone.utc)
    it = croniter(cron_expr, base)
    nxt = it.get_next(datetime)
    # croniter may return naive datetime; make it tz-aware
    if nxt.tzinfo is None:
        nxt = nxt.replace(tzinfo=timezone.utc)
    return nxt


# ---- ORM model --------------------------------------------------------------

class ScheduledRun(Base):
    __tablename__ = "scheduled_runs"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    suite_id = Column(Integer, ForeignKey("test_suites.id", ondelete="CASCADE"), nullable=False)
    environment_id = Column(Integer, ForeignKey("environments.id"), nullable=False)
    cron_expr = Column(String(100), nullable=False)
    preset_label = Column(String(40))
    priority = Column(String(20), nullable=False, server_default="normal")
    enabled = Column(Boolean, nullable=False, server_default="true")
    max_silence_hours = Column(Integer)
    next_fire_at = Column(DateTime(timezone=True))
    last_fired_at = Column(DateTime(timezone=True))
    last_run_id = Column(Integer, ForeignKey("pipeline_runs.id", ondelete="SET NULL"))
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("priority IN ('normal','high','critical')",
                        name="scheduled_runs_priority_ck"),
    )


# ---- Repository -------------------------------------------------------------

class ScheduledRunRepository:
    def __init__(self, db):
        self.db = db

    def create(self, *, tenant_id, suite_id, environment_id, cron_expr,
               preset_label, priority, max_silence_hours, created_by) -> ScheduledRun:
        validate_cron(cron_expr)
        row = ScheduledRun(
            tenant_id=tenant_id,
            suite_id=suite_id,
            environment_id=environment_id,
            cron_expr=cron_expr,
            preset_label=preset_label or cron_to_preset(cron_expr),
            priority=priority or "normal",
            max_silence_hours=max_silence_hours,
            created_by=created_by,
            next_fire_at=next_fire(cron_expr),
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def update(self, schedule_id, tenant_id, *, updated_by, **fields):
        row = self.db.query(ScheduledRun).filter_by(id=schedule_id, tenant_id=tenant_id).first()
        if not row:
            return None
        cron_changed = False
        if "cron_expr" in fields and fields["cron_expr"]:
            validate_cron(fields["cron_expr"])
            fields["preset_label"] = cron_to_preset(fields["cron_expr"])  # may be None
            fields["next_fire_at"] = next_fire(fields["cron_expr"])
            cron_changed = True
        for k in ("cron_expr", "priority", "enabled", "max_silence_hours",
                  "environment_id", "next_fire_at"):
            # Note: for boolean 'enabled', False must still be applied, so
            # we only skip when the key isn't in the payload at all.
            if k in fields and fields[k] is not None:
                setattr(row, k, fields[k])
        # preset_label is special: clearing it to None is a valid outcome of
        # changing cron_expr to a non-preset value, so apply unconditionally
        # when cron_expr changed.
        if cron_changed:
            row.preset_label = fields.get("preset_label")
        row.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(row)
        return row

    def delete(self, schedule_id, tenant_id):
        row = self.db.query(ScheduledRun).filter_by(id=schedule_id, tenant_id=tenant_id).first()
        if not row:
            return False
        self.db.delete(row)
        self.db.commit()
        return True

    def get(self, schedule_id, tenant_id):
        return self.db.query(ScheduledRun).filter_by(id=schedule_id, tenant_id=tenant_id).first()

    def list_for_tenant(self, tenant_id):
        return self.db.query(ScheduledRun).filter_by(tenant_id=tenant_id).order_by(
            ScheduledRun.next_fire_at.asc().nullslast()).all()

    def get_due(self, at: Optional[datetime] = None, limit: int = 20) -> List[ScheduledRun]:
        at = at or datetime.now(timezone.utc)
        return self.db.query(ScheduledRun).filter(
            ScheduledRun.enabled.is_(True),
            ScheduledRun.next_fire_at.isnot(None),
            ScheduledRun.next_fire_at <= at,
        ).order_by(ScheduledRun.next_fire_at.asc()).limit(limit).all()

    def mark_fired(self, schedule_id: int, run_id: Optional[int] = None) -> None:
        row = self.db.query(ScheduledRun).filter_by(id=schedule_id).first()
        if not row:
            return
        row.last_fired_at = datetime.now(timezone.utc)
        row.last_run_id = run_id
        row.next_fire_at = next_fire(row.cron_expr)
        row.updated_at = datetime.now(timezone.utc)
        self.db.commit()

    def find_silent(self, tenant_id: int) -> List[ScheduledRun]:
        """Return schedules whose last_fired_at is older than max_silence_hours.

        If max_silence_hours is null, the schedule opts out of DMS alerting.
        """
        now = datetime.now(timezone.utc)
        silent = []
        rows = self.db.query(ScheduledRun).filter(
            ScheduledRun.tenant_id == tenant_id,
            ScheduledRun.enabled.is_(True),
            ScheduledRun.max_silence_hours.isnot(None),
        ).all()
        for r in rows:
            reference = r.last_fired_at or r.created_at
            if not reference:
                continue
            if (now - reference).total_seconds() / 3600.0 > r.max_silence_hours:
                silent.append(r)
        return silent


# ---- Scheduler tick ---------------------------------------------------------

@dataclass
class FireResult:
    schedule_id: int
    run_id: Optional[int]
    status: str
    error: Optional[str] = None


def fire_due_schedules(db) -> List[FireResult]:
    """Scheduler daemon calls this on its tick; creates pipeline_runs for due schedules.

    Uses PipelineService.create_run. One failure doesn't stop the batch.
    """
    from primeqa.execution.repository import (
        PipelineRunRepository, PipelineStageRepository,
        ExecutionSlotRepository, WorkerHeartbeatRepository,
    )
    from primeqa.execution.service import PipelineService
    from primeqa.test_management.repository import TestSuiteRepository

    repo = ScheduledRunRepository(db)
    due = repo.get_due()
    if not due:
        return []

    svc = PipelineService(
        PipelineRunRepository(db), PipelineStageRepository(db),
        ExecutionSlotRepository(db), WorkerHeartbeatRepository(db),
    )
    suite_repo = TestSuiteRepository(db)

    results: List[FireResult] = []
    for sched in due:
        try:
            suite = suite_repo.get_suite(sched.suite_id, sched.tenant_id)
            if not suite:
                log.warning("schedule %s: suite %s missing; disabling",
                            sched.id, sched.suite_id)
                sched.enabled = False
                db.commit()
                results.append(FireResult(sched.id, None, "disabled_suite_missing"))
                continue

            stcs = suite_repo.get_suite_test_cases(sched.suite_id)
            tc_ids = [s.test_case_id for s in stcs]
            if not tc_ids:
                repo.mark_fired(sched.id, None)
                results.append(FireResult(sched.id, None, "empty_suite"))
                continue

            created = svc.create_run(
                tenant_id=sched.tenant_id,
                environment_id=sched.environment_id,
                triggered_by=sched.created_by,
                run_type="execute_only",
                source_type="test_cases",
                source_ids=tc_ids,
                priority=sched.priority or "normal",
                source_refs={
                    "scheduled_run_id": sched.id,
                    "suite_id": sched.suite_id,
                    "suite_name": suite.name,
                },
            )
            repo.mark_fired(sched.id, created["id"])
            results.append(FireResult(sched.id, created["id"], "fired"))
            log.info("scheduler: fired schedule=%s run=%s", sched.id, created["id"])
        except Exception as e:
            log.exception("schedule %s fire failed", sched.id)
            results.append(FireResult(sched.id, None, "error", error=str(e)))
    return results
