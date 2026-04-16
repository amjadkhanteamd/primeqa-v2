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
    from primeqa.db import init_db, SessionLocal
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL not set")
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    init_db(database_url)
    db = SessionLocal()
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


def scheduler_tick(ctx):
    """Single reaper iteration."""
    reap_stuck_stages(ctx)
    reap_stuck_slots(ctx)
    reap_stale_workers(ctx)


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
