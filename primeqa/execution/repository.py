"""Repository for the execution domain.

DB queries scoped to: pipeline_runs, pipeline_stages, run_test_results,
                      run_step_results, run_artifacts, run_created_entities,
                      run_cleanup_attempts, execution_slots, worker_heartbeats
"""

from datetime import datetime, timezone, timedelta

from sqlalchemy import func, case, and_, text

from primeqa.execution.models import ExecutionSlot, WorkerHeartbeat
from primeqa.core.models import Environment, User

STAGE_RETRY_POLICY = {
    "metadata_refresh": 3,
    "jira_read": 2,
    "generate": 3,
    "store": 2,
    "execute": 1,
    "record": 3,
}

STAGE_ORDER = [
    "metadata_refresh", "jira_read", "generate", "store", "execute", "record",
]


class ExecutionSlotRepository:
    def __init__(self, db):
        self.db = db

    def acquire_slot(self, environment_id, run_id):
        env = self.db.query(Environment).filter(Environment.id == environment_id).first()
        if not env:
            return False
        held = self.count_held_slots(environment_id)
        if held >= env.max_execution_slots:
            return False
        slot = ExecutionSlot(environment_id=environment_id, run_id=run_id)
        self.db.add(slot)
        self.db.commit()
        return True

    def release_slot(self, environment_id, run_id):
        slot = self.db.query(ExecutionSlot).filter(
            ExecutionSlot.environment_id == environment_id,
            ExecutionSlot.run_id == run_id,
            ExecutionSlot.released_at == None,
        ).first()
        if slot:
            slot.released_at = datetime.now(timezone.utc)
            self.db.commit()
            return True
        return False

    def count_held_slots(self, environment_id):
        return self.db.query(func.count(ExecutionSlot.id)).filter(
            ExecutionSlot.environment_id == environment_id,
            ExecutionSlot.released_at == None,
        ).scalar()

    def get_slot_status(self, environment_id):
        env = self.db.query(Environment).filter(Environment.id == environment_id).first()
        if not env:
            return None
        held = self.count_held_slots(environment_id)
        active_slots = self.db.query(ExecutionSlot).filter(
            ExecutionSlot.environment_id == environment_id,
            ExecutionSlot.released_at == None,
        ).all()
        return {
            "total": env.max_execution_slots,
            "used": held,
            "available": env.max_execution_slots - held,
            "held_by": [
                {"run_id": s.run_id, "acquired_at": s.acquired_at.isoformat()}
                for s in active_slots
            ],
        }

    def release_stale_slots(self, max_age_seconds=3600):
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
        stale = self.db.query(ExecutionSlot).filter(
            ExecutionSlot.released_at == None,
            ExecutionSlot.acquired_at < cutoff,
        ).all()
        released = []
        for slot in stale:
            slot.released_at = datetime.now(timezone.utc)
            released.append(slot.run_id)
        if released:
            self.db.commit()
        return released


class WorkerHeartbeatRepository:
    def __init__(self, db):
        self.db = db

    def register_worker(self, worker_id):
        existing = self.db.query(WorkerHeartbeat).filter(
            WorkerHeartbeat.worker_id == worker_id,
        ).first()
        if existing:
            existing.status = "alive"
            existing.last_heartbeat = datetime.now(timezone.utc)
            self.db.commit()
            self.db.refresh(existing)
            return existing
        wh = WorkerHeartbeat(worker_id=worker_id)
        self.db.add(wh)
        self.db.commit()
        self.db.refresh(wh)
        return wh

    def update_heartbeat(self, worker_id, current_run_id=None, current_stage=None):
        wh = self.db.query(WorkerHeartbeat).filter(
            WorkerHeartbeat.worker_id == worker_id,
        ).first()
        if wh:
            wh.last_heartbeat = datetime.now(timezone.utc)
            wh.current_run_id = current_run_id
            wh.current_stage = current_stage
            self.db.commit()

    def mark_dead(self, worker_id, died_reason=None):
        """Mark a worker dead. `died_reason` (audit 2026-04-19):
        short free-form string. Usual values: 'SIGTERM',
        'heartbeat_timeout', or a truncated exception string. Helps
        ops distinguish graceful Railway deploys from crashes."""
        wh = self.db.query(WorkerHeartbeat).filter(
            WorkerHeartbeat.worker_id == worker_id,
        ).first()
        if wh:
            wh.status = "dead"
            if died_reason and not wh.died_reason:
                # Don't overwrite a specific reason with a later
                # generic one (e.g. the reaper's 'heartbeat_timeout'
                # shouldn't clobber an explicit 'SIGTERM').
                wh.died_reason = died_reason[:255]
                wh.died_at = datetime.now(timezone.utc)
            self.db.commit()

    def find_dead_workers(self, timeout_seconds=120):
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)
        return self.db.query(WorkerHeartbeat).filter(
            WorkerHeartbeat.status == "alive",
            WorkerHeartbeat.last_heartbeat < cutoff,
        ).all()

    def get_worker_for_run(self, run_id):
        return self.db.query(WorkerHeartbeat).filter(
            WorkerHeartbeat.current_run_id == run_id,
            WorkerHeartbeat.status == "alive",
        ).first()

