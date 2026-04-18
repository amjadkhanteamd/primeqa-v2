"""Reaper/scheduler entrypoint.

Runs on a timer. Handles:
- Dead job reaper (stuck stages with no worker heartbeat)
- Stuck slot reaper (slots held beyond max_execution_time_sec)
- Stale worker cleanup (workers with no heartbeat for 2+ minutes)
"""

import logging
import os
import time

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

REAPER_INTERVAL = 60


def create_scheduler_context():
    from primeqa import db as dbmod
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL not set")
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    dbmod.init_db(database_url)
    db = dbmod.SessionLocal()
    from primeqa.execution.repository import (
        PipelineRunRepository, PipelineStageRepository,
        ExecutionSlotRepository, WorkerHeartbeatRepository,
    )
    from primeqa.execution.service import PipelineService
    return {
        "db": db,
        "run_repo": PipelineRunRepository(db),
        "stage_repo": PipelineStageRepository(db),
        "slot_repo": ExecutionSlotRepository(db),
        "heartbeat_repo": WorkerHeartbeatRepository(db),
        "service": PipelineService(
            PipelineRunRepository(db), PipelineStageRepository(db),
            ExecutionSlotRepository(db), WorkerHeartbeatRepository(db),
        ),
    }


def reap_stuck_stages(ctx):
    """Find stages running for > 5 minutes with no active worker and mark them failed."""
    stage_repo = ctx["stage_repo"]
    heartbeat_repo = ctx["heartbeat_repo"]
    service = ctx["service"]

    stuck = stage_repo.find_stuck_stages(timeout_seconds=300)
    for stage in stuck:
        worker = heartbeat_repo.get_worker_for_run(stage.run_id)
        if not worker:
            stage_repo.update_stage(stage.id, "failed", last_error="Worker timeout")
            service.fail_run(stage.run_id, error_message="Worker timeout — stage stuck")
            log.warning(f"Reaped stuck stage {stage.id} (run {stage.run_id})")


def reap_stuck_slots(ctx):
    """Release slots held beyond the run's max_execution_time_sec."""
    slot_repo = ctx["slot_repo"]
    run_repo = ctx["run_repo"]
    service = ctx["service"]

    from primeqa.execution.models import ExecutionSlot, PipelineRun
    from datetime import datetime, timezone, timedelta

    db = ctx["db"]
    active_slots = db.query(ExecutionSlot).filter(
        ExecutionSlot.released_at == None,
    ).all()

    for slot in active_slots:
        run = run_repo.get_run(slot.run_id)
        if not run:
            slot.released_at = datetime.now(timezone.utc)
            continue
        cutoff = slot.acquired_at.replace(tzinfo=timezone.utc) + timedelta(seconds=run.max_execution_time_sec)
        if datetime.now(timezone.utc) > cutoff:
            slot.released_at = datetime.now(timezone.utc)
            if run.status == "running":
                service.fail_run(run.id, error_message="Execution timeout")
            log.warning(f"Reaped stuck slot for run {slot.run_id}")
    db.commit()


def reap_stale_workers(ctx):
    """Mark workers with no heartbeat for 2+ minutes as dead."""
    heartbeat_repo = ctx["heartbeat_repo"]
    dead = heartbeat_repo.find_dead_workers(timeout_seconds=120)
    for wh in dead:
        heartbeat_repo.mark_dead(wh.worker_id)
        log.warning(f"Marked worker {wh.worker_id} as dead")


def fire_scheduled_runs(ctx):
    """R4: poll scheduled_runs, create pipeline_runs for due schedules."""
    try:
        from primeqa.runs.schedule import fire_due_schedules
        results = fire_due_schedules(ctx["db"])
        for r in results:
            if r.status == "fired":
                log.info("scheduler fired schedule=%s run=%s", r.schedule_id, r.run_id)
            elif r.status == "error":
                log.warning("schedule %s fire error: %s", r.schedule_id, r.error)
    except Exception as e:
        log.exception("fire_scheduled_runs failed: %s", e)


def dead_mans_switch_check(ctx):
    """R4: log any silent schedules; persistent alerting wires up in R6."""
    try:
        from primeqa.runs.schedule import ScheduledRunRepository
        from primeqa.core.models import Tenant
        for tenant in ctx["db"].query(Tenant).all():
            silent = ScheduledRunRepository(ctx["db"]).find_silent(tenant.id)
            for s in silent:
                log.warning("DMS: schedule %s (tenant %s) silent > %dh",
                            s.id, tenant.id, s.max_silence_hours)
    except Exception as e:
        log.exception("dead_mans_switch_check failed: %s", e)


def scheduler_tick(ctx):
    """Single reaper iteration."""
    reap_stuck_stages(ctx)
    reap_stuck_slots(ctx)
    reap_stale_workers(ctx)
    fire_scheduled_runs(ctx)
    dead_mans_switch_check(ctx)
    reap_stalled_metadata_jobs(ctx)
    trim_run_events(ctx)


_last_trim = {"at": 0}

def trim_run_events(ctx):
    """Keep at most 1000 events per run (oldest trimmed). Runs at most
    once every ~10 min so the reaper is cheap. The hard cap protects
    against runaway event volume from a misbehaving worker; normal
    runs stay well under this.
    """
    import time
    now = time.time()
    if now - _last_trim["at"] < 600:  # 10 min
        return
    _last_trim["at"] = now
    try:
        from sqlalchemy import text
        ctx["db"].execute(text("""
            DELETE FROM run_events
            WHERE id IN (
                SELECT e.id
                FROM (
                    SELECT id, row_number() OVER (PARTITION BY run_id ORDER BY id DESC) AS rn
                    FROM run_events
                ) e
                WHERE e.rn > 1000
            )
        """))
        ctx["db"].commit()
    except Exception as e:
        log.warning("trim_run_events failed: %s", e)


def reap_stalled_metadata_jobs(ctx):
    """Fail metadata-sync jobs whose worker has gone silent > 2 min.

    Added with migration 025 / background-job architecture. Matches the
    existing pattern for reaping stuck pipeline stages.
    """
    try:
        from primeqa.metadata.worker_runner import reap_stalled_jobs
        reaped = reap_stalled_jobs(ctx["db"])
        if reaped:
            log.info("reaped %d stalled metadata sync job(s)", reaped)
    except Exception as e:
        log.warning("metadata reaper failed: %s", e)


def run_scheduler():
    """Main scheduler loop."""
    print("Scheduler starting...")
    ctx = create_scheduler_context()

    try:
        while True:
            scheduler_tick(ctx)
            time.sleep(REAPER_INTERVAL)
    except KeyboardInterrupt:
        print("Scheduler shutting down")
    finally:
        ctx["db"].close()


if __name__ == "__main__":
    run_scheduler()
