"""Service layer for the execution domain.

Business logic: pipeline orchestration, queue management, slot acquisition.
"""

import uuid
from datetime import datetime, timezone


class PipelineService:
    def __init__(self, run_repo, stage_repo, slot_repo, heartbeat_repo):
        self.run_repo = run_repo
        self.stage_repo = stage_repo
        self.slot_repo = slot_repo
        self.heartbeat_repo = heartbeat_repo

    def create_run(self, tenant_id, environment_id, triggered_by, run_type,
                   source_type, source_ids, **kwargs):
        cancellation_token = str(uuid.uuid4())
        run = self.run_repo.create_run(
            tenant_id, environment_id, triggered_by, run_type,
            source_type, source_ids, cancellation_token, **kwargs,
        )
        self.stage_repo.create_stages(run.id)

        slot_acquired = self.slot_repo.acquire_slot(environment_id, run.id)
        if slot_acquired:
            self.run_repo.update_run_status(run.id, "running")
            run = self.run_repo.get_run(run.id)
            return self._run_dict(run, queue_position=0)

        queue_pos = self._get_queue_position(run)
        return self._run_dict(run, queue_position=queue_pos)

    def cancel_run(self, run_id, tenant_id):
        run = self.run_repo.get_run(run_id, tenant_id)
        if not run:
            raise ValueError("Run not found")
        if run.status in ("completed", "failed", "cancelled"):
            raise ValueError(f"Cannot cancel run with status '{run.status}'")

        if run.status == "queued":
            self.run_repo.update_run_status(run.id, "cancelled")
            self.stage_repo.skip_remaining_stages(run.id)
            return self._run_dict(run)

        self.run_repo.update_run_status(run.id, "cancelled")
        self.slot_repo.release_slot(run.environment_id, run.id)
        self.stage_repo.skip_remaining_stages(run.id)

        self._start_next_queued(run.environment_id)

        run = self.run_repo.get_run(run.id)
        return self._run_dict(run)

    def get_run_status(self, run_id, tenant_id=None):
        run = self.run_repo.get_run(run_id, tenant_id)
        if not run:
            return None
        stages = self.stage_repo.get_stages(run.id)
        result = self._run_dict(run)
        result["stages"] = [self._stage_dict(s) for s in stages]
        return result

    def list_runs(self, tenant_id, **filters):
        runs = self.run_repo.list_runs(tenant_id, **filters)
        return [self._run_dict(r) for r in runs]

    def get_queue(self, tenant_id):
        runs = self.run_repo.get_queue_with_position(tenant_id)
        result = []
        env_positions = {}
        for run in runs:
            if run.status == "queued":
                env_positions.setdefault(run.environment_id, 0)
                env_positions[run.environment_id] += 1
                pos = env_positions[run.environment_id]
            else:
                pos = 0
            result.append(self._run_dict(run, queue_position=pos))
        return result

    def get_slot_status(self, environment_id):
        return self.slot_repo.get_slot_status(environment_id)

    def complete_run(self, run_id):
        run = self.run_repo.get_run(run_id)
        if not run:
            return
        self.run_repo.update_run_status(run.id, "completed")
        self.slot_repo.release_slot(run.environment_id, run.id)
        self._start_next_queued(run.environment_id)

    def fail_run(self, run_id, error_message=None):
        run = self.run_repo.get_run(run_id)
        if not run:
            return
        self.run_repo.update_run_status(run.id, "failed", error_message=error_message)
        self.slot_repo.release_slot(run.environment_id, run.id)
        self.stage_repo.skip_remaining_stages(run.id)
        self._start_next_queued(run.environment_id)

    def _start_next_queued(self, environment_id):
        next_run = self.run_repo.get_next_queued_for_env(environment_id)
        if not next_run:
            return
        slot_acquired = self.slot_repo.acquire_slot(environment_id, next_run.id)
        if slot_acquired:
            self.run_repo.update_run_status(next_run.id, "running")

    def _get_queue_position(self, run):
        queued = self.run_repo.get_queued_runs(run.environment_id)
        for i, r in enumerate(queued, 1):
            if r.id == run.id:
                return i
        return len(queued) + 1

    @staticmethod
    def _run_dict(run, queue_position=None):
        d = {
            "id": run.id, "tenant_id": run.tenant_id,
            "environment_id": run.environment_id,
            "triggered_by": run.triggered_by,
            "run_type": run.run_type, "source_type": run.source_type,
            "source_ids": run.source_ids, "status": run.status,
            "priority": run.priority,
            "max_execution_time_sec": run.max_execution_time_sec,
            "cancellation_token": run.cancellation_token,
            "total_tests": run.total_tests, "passed": run.passed,
            "failed": run.failed, "skipped": run.skipped,
            "error_message": run.error_message,
            "queued_at": run.queued_at.isoformat() if run.queued_at else None,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        }
        if queue_position is not None:
            d["queue_position"] = queue_position
        return d

    @staticmethod
    def _stage_dict(s):
        return {
            "id": s.id, "run_id": s.run_id, "stage_name": s.stage_name,
            "stage_order": s.stage_order, "status": s.status,
            "attempt": s.attempt, "max_attempts": s.max_attempts,
            "last_error": s.last_error, "duration_ms": s.duration_ms,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "completed_at": s.completed_at.isoformat() if s.completed_at else None,
        }
