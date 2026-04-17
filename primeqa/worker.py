"""Background worker entrypoint.

Polls pipeline_runs for running jobs with pending stages, executes them,
sends heartbeats, and checks cancellation tokens between steps.
"""

import logging
import os
import time
import uuid

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

POLL_INTERVAL = 5
HEARTBEAT_INTERVAL = 30


def create_worker_context():
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


def execute_stage(stage, ctx):
    """Execute a single pipeline stage. Currently a stub that marks as passed."""
    stage_repo = ctx["stage_repo"]
    stage_repo.update_stage(stage.id, "running")
    ctx["heartbeat_repo"].update_heartbeat(
        ctx["worker_id"], current_run_id=stage.run_id, current_stage=stage.stage_name,
    )
    stage_repo.update_stage(stage.id, "passed")
    return True


def process_run(run, ctx):
    """Process a single running pipeline run — advance through its stages."""
    stage_repo = ctx["stage_repo"]
    run_repo = ctx["run_repo"]
    service = ctx["service"]

    while True:
        fresh_run = run_repo.get_run(run.id)
        if fresh_run.status == "cancelled":
            log.info(f"Run {run.id} cancelled")
            return

        stage = stage_repo.get_next_pending_stage(run.id)
        if not stage:
            service.complete_run(run.id)
            return

        try:
            success = execute_stage(stage, ctx)
            if not success:
                raise RuntimeError("Stage execution returned failure")
        except Exception as e:
            if stage.attempt < stage.max_attempts:
                stage_repo.update_stage(stage.id, "failed", last_error=str(e))
                stage_repo.increment_attempt(stage.id)
                continue
            else:
                stage_repo.update_stage(stage.id, "failed", last_error=str(e))
                service.fail_run(run.id, error_message=f"Stage {stage.stage_name} failed: {e}")
                return


def worker_tick(ctx):
    """Single poll iteration — find running runs with pending stages and process them."""
    run_repo = ctx["run_repo"]
    runs = run_repo.get_running_runs()
    for run in runs:
        stage = ctx["stage_repo"].get_next_pending_stage(run.id)
        if stage:
            process_run(run, ctx)


def run_worker():
    """Main worker loop."""
    worker_id = f"worker-{uuid.uuid4().hex[:8]}"
    print(f"Worker {worker_id} starting...")

    ctx = create_worker_context()
    ctx["worker_id"] = worker_id
    ctx["heartbeat_repo"].register_worker(worker_id)

    last_heartbeat = time.time()

    try:
        while True:
            worker_tick(ctx)

            if time.time() - last_heartbeat > HEARTBEAT_INTERVAL:
                ctx["heartbeat_repo"].update_heartbeat(worker_id)
                last_heartbeat = time.time()

            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print(f"Worker {worker_id} shutting down")
    finally:
        ctx["heartbeat_repo"].mark_dead(worker_id)
        ctx["db"].close()


if __name__ == "__main__":
    run_worker()
