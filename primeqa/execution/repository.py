"""Repository for the execution domain.

DB queries scoped to: pipeline_runs, pipeline_stages, run_test_results,
                      run_step_results, run_artifacts, run_created_entities,
                      run_cleanup_attempts, execution_slots, worker_heartbeats
"""

from datetime import datetime, timezone, timedelta

from sqlalchemy import func, case, and_, text

from primeqa.execution.models import (
    PipelineRun, PipelineStage, RunTestResult, RunStepResult,
    RunArtifact, RunCreatedEntity, RunCleanupAttempt,
    ExecutionSlot, WorkerHeartbeat,
)
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


class PipelineRunRepository:
    def __init__(self, db):
        self.db = db

    def create_run(self, tenant_id, environment_id, triggered_by, run_type,
                   source_type, source_ids, cancellation_token, **kwargs):
        run = PipelineRun(
            tenant_id=tenant_id,
            environment_id=environment_id,
            triggered_by=triggered_by,
            run_type=run_type,
            source_type=source_type,
            source_ids=source_ids,
            cancellation_token=cancellation_token,
            priority=kwargs.get("priority", "normal"),
            max_execution_time_sec=kwargs.get("max_execution_time_sec", 3600),
            config=kwargs.get("config", {}),
            source_refs=kwargs.get("source_refs", {}),
            parent_run_id=kwargs.get("parent_run_id"),
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def get_run(self, run_id, tenant_id=None):
        q = self.db.query(PipelineRun).filter(PipelineRun.id == run_id)
        if tenant_id:
            q = q.filter(PipelineRun.tenant_id == tenant_id)
        return q.first()

    def list_runs(self, tenant_id, status=None, environment_id=None,
                  triggered_by=None, limit=50, offset=0):
        q = self.db.query(PipelineRun).filter(PipelineRun.tenant_id == tenant_id)
        if status:
            q = q.filter(PipelineRun.status == status)
        if environment_id:
            q = q.filter(PipelineRun.environment_id == environment_id)
        if triggered_by:
            q = q.filter(PipelineRun.triggered_by == triggered_by)
        return q.order_by(PipelineRun.queued_at.desc()).offset(offset).limit(limit).all()

    def update_run_status(self, run_id, status, **kwargs):
        run = self.db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if not run:
            return None
        run.status = status
        if status == "running" and not run.started_at:
            run.started_at = datetime.now(timezone.utc)
        if status in ("completed", "failed", "cancelled"):
            run.completed_at = datetime.now(timezone.utc)
        for k, v in kwargs.items():
            if hasattr(run, k):
                setattr(run, k, v)
        self.db.commit()
        self.db.refresh(run)
        return run

    def get_queued_runs(self, environment_id=None, limit=10):
        q = self.db.query(PipelineRun).filter(PipelineRun.status == "queued")
        if environment_id:
            q = q.filter(PipelineRun.environment_id == environment_id)
        priority_order = case(
            (PipelineRun.priority == "critical", 0),
            (PipelineRun.priority == "high", 1),
            else_=2,
        )
        return q.order_by(priority_order, PipelineRun.queued_at.asc()).limit(limit).all()

    def get_running_runs(self, limit=50):
        return self.db.query(PipelineRun).filter(
            PipelineRun.status == "running",
        ).all()

    def get_next_queued_for_env(self, environment_id):
        priority_order = case(
            (PipelineRun.priority == "critical", 0),
            (PipelineRun.priority == "high", 1),
            else_=2,
        )
        return self.db.query(PipelineRun).filter(
            PipelineRun.environment_id == environment_id,
            PipelineRun.status == "queued",
        ).order_by(priority_order, PipelineRun.queued_at.asc()).first()

    def get_queue_with_position(self, tenant_id=None):
        priority_order = case(
            (PipelineRun.priority == "critical", 0),
            (PipelineRun.priority == "high", 1),
            else_=2,
        )
        q = self.db.query(PipelineRun).filter(
            PipelineRun.status.in_(["queued", "running"]),
        )
        if tenant_id:
            q = q.filter(PipelineRun.tenant_id == tenant_id)
        return q.order_by(priority_order, PipelineRun.queued_at.asc()).all()


class PipelineStageRepository:
    def __init__(self, db):
        self.db = db

    def create_stages(self, run_id):
        stages = []
        for i, name in enumerate(STAGE_ORDER, 1):
            stage = PipelineStage(
                run_id=run_id,
                stage_name=name,
                stage_order=i,
                max_attempts=STAGE_RETRY_POLICY[name],
            )
            self.db.add(stage)
            stages.append(stage)
        self.db.commit()
        for s in stages:
            self.db.refresh(s)
        return stages

    def get_stages(self, run_id):
        return self.db.query(PipelineStage).filter(
            PipelineStage.run_id == run_id,
        ).order_by(PipelineStage.stage_order).all()

    def get_next_pending_stage(self, run_id):
        return self.db.query(PipelineStage).filter(
            PipelineStage.run_id == run_id,
            PipelineStage.status == "pending",
        ).order_by(PipelineStage.stage_order).first()

    def get_current_running_stage(self, run_id):
        return self.db.query(PipelineStage).filter(
            PipelineStage.run_id == run_id,
            PipelineStage.status == "running",
        ).first()

    def update_stage(self, stage_id, status, **kwargs):
        stage = self.db.query(PipelineStage).filter(PipelineStage.id == stage_id).first()
        if not stage:
            return None
        stage.status = status
        if status == "running" and not stage.started_at:
            stage.started_at = datetime.now(timezone.utc)
        if status in ("passed", "failed", "skipped"):
            stage.completed_at = datetime.now(timezone.utc)
            if stage.started_at:
                delta = stage.completed_at - stage.started_at
                stage.duration_ms = int(delta.total_seconds() * 1000)
        for k, v in kwargs.items():
            if hasattr(stage, k):
                setattr(stage, k, v)
        self.db.commit()
        self.db.refresh(stage)
        return stage

    def increment_attempt(self, stage_id):
        stage = self.db.query(PipelineStage).filter(PipelineStage.id == stage_id).first()
        if stage:
            stage.attempt += 1
            stage.status = "pending"
            stage.started_at = None
            stage.completed_at = None
            self.db.commit()
            self.db.refresh(stage)
        return stage

    def find_stuck_stages(self, timeout_seconds=300):
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)
        return self.db.query(PipelineStage).filter(
            PipelineStage.status == "running",
            PipelineStage.started_at < cutoff,
        ).all()

    def skip_remaining_stages(self, run_id):
        self.db.query(PipelineStage).filter(
            PipelineStage.run_id == run_id,
            PipelineStage.status == "pending",
        ).update({"status": "skipped"})
        self.db.commit()


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

    def mark_dead(self, worker_id):
        wh = self.db.query(WorkerHeartbeat).filter(
            WorkerHeartbeat.worker_id == worker_id,
        ).first()
        if wh:
            wh.status = "dead"
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


class RunTestResultRepository:
    def __init__(self, db):
        self.db = db

    def create_result(self, run_id, test_case_id, test_case_version_id, environment_id, status="passed", **kwargs):
        rtr = RunTestResult(
            run_id=run_id,
            test_case_id=test_case_id,
            test_case_version_id=test_case_version_id,
            environment_id=environment_id,
            status=status,
            failure_type=kwargs.get("failure_type"),
            failure_summary=kwargs.get("failure_summary"),
            total_steps=kwargs.get("total_steps", 0),
            passed_steps=kwargs.get("passed_steps", 0),
            failed_steps=kwargs.get("failed_steps", 0),
            duration_ms=kwargs.get("duration_ms"),
        )
        self.db.add(rtr)
        self.db.commit()
        self.db.refresh(rtr)
        return rtr

    def get_result(self, result_id):
        return self.db.query(RunTestResult).filter(RunTestResult.id == result_id).first()

    def list_results(self, run_id):
        return self.db.query(RunTestResult).filter(
            RunTestResult.run_id == run_id,
        ).order_by(RunTestResult.executed_at).all()

    def update_result(self, result_id, updates):
        rtr = self.get_result(result_id)
        if not rtr:
            return None
        for k, v in updates.items():
            if hasattr(rtr, k):
                setattr(rtr, k, v)
        self.db.commit()
        self.db.refresh(rtr)
        return rtr


class RunStepResultRepository:
    def __init__(self, db):
        self.db = db

    def create_step_result(self, run_test_result_id, step_order, step_action, status="passed", **kwargs):
        rsr = RunStepResult(
            run_test_result_id=run_test_result_id,
            step_order=step_order,
            step_action=step_action,
            status=status,
            execution_state=kwargs.get("execution_state", "not_started"),
            target_object=kwargs.get("target_object"),
            target_record_id=kwargs.get("target_record_id"),
            before_state=kwargs.get("before_state"),
            after_state=kwargs.get("after_state"),
            field_diff=kwargs.get("field_diff"),
            api_request=kwargs.get("api_request"),
            api_response=kwargs.get("api_response"),
            error_message=kwargs.get("error_message"),
            duration_ms=kwargs.get("duration_ms"),
        )
        self.db.add(rsr)
        self.db.commit()
        self.db.refresh(rsr)
        return rsr

    def update_step_result(self, step_id, updates):
        rsr = self.db.query(RunStepResult).filter(RunStepResult.id == step_id).first()
        if not rsr:
            return None
        for k, v in updates.items():
            if hasattr(rsr, k):
                setattr(rsr, k, v)
        self.db.commit()
        self.db.refresh(rsr)
        return rsr

    def list_step_results(self, run_test_result_id):
        return self.db.query(RunStepResult).filter(
            RunStepResult.run_test_result_id == run_test_result_id,
        ).order_by(RunStepResult.step_order).all()


class RunCreatedEntityRepository:
    def __init__(self, db):
        self.db = db

    def create_entity(self, run_id, run_step_result_id, entity_type, sf_record_id, creation_source, **kwargs):
        entity = RunCreatedEntity(
            run_id=run_id,
            run_step_result_id=run_step_result_id,
            entity_type=entity_type,
            sf_record_id=sf_record_id,
            creation_source=creation_source,
            logical_identifier=kwargs.get("logical_identifier"),
            primeqa_idempotency_key=kwargs.get("primeqa_idempotency_key"),
            creation_fingerprint=kwargs.get("creation_fingerprint"),
            parent_entity_id=kwargs.get("parent_entity_id"),
            cleanup_required=kwargs.get("cleanup_required", True),
        )
        self.db.add(entity)
        self.db.commit()
        self.db.refresh(entity)
        return entity

    def list_entities_for_cleanup(self, run_id):
        return self.db.query(RunCreatedEntity).filter(
            RunCreatedEntity.run_id == run_id,
            RunCreatedEntity.cleanup_required == True,
        ).order_by(RunCreatedEntity.created_at.desc()).all()

    def find_by_idempotency_key(self, key):
        return self.db.query(RunCreatedEntity).filter(
            RunCreatedEntity.primeqa_idempotency_key == key,
        ).first()

    def mark_cleaned(self, entity_id):
        entity = self.db.query(RunCreatedEntity).filter(
            RunCreatedEntity.id == entity_id,
        ).first()
        if entity:
            entity.cleanup_required = False
            self.db.commit()
