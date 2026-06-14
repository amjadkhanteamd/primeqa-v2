"""Repository for the execution domain.

DB queries scoped to: worker_heartbeats. (The v1 pipeline / run_* / execution_slots
repositories retired with their tables in migration 053 — D-221.5 / D-240.)
"""

from datetime import datetime, timezone, timedelta

from primeqa.execution.models import WorkerHeartbeat


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

