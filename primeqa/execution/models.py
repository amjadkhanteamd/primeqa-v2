"""SQLAlchemy models for the execution domain.

Tables owned: pipeline_runs, pipeline_stages, run_test_results, run_step_results,
              run_artifacts, run_created_entities, run_cleanup_attempts,
              execution_slots, worker_heartbeats
"""

from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Text, JSON, Float,
    ForeignKey, CheckConstraint, UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from primeqa.db import Base


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    environment_id = Column(Integer, ForeignKey("environments.id"), nullable=False)
    triggered_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    run_type = Column(String(30), nullable=False)
    source_type = Column(String(30), nullable=False)
    source_ids = Column(JSON, nullable=False, server_default="[]")
    status = Column(String(20), nullable=False, server_default="queued")
    priority = Column(String(20), nullable=False, server_default="normal")
    max_execution_time_sec = Column(Integer, nullable=False, server_default="3600")
    cancellation_token = Column(String(100), nullable=False)
    config = Column(JSON, nullable=False, server_default="{}")
    total_tests = Column(Integer, nullable=False, server_default="0")
    passed = Column(Integer, nullable=False, server_default="0")
    failed = Column(Integer, nullable=False, server_default="0")
    skipped = Column(Integer, nullable=False, server_default="0")
    error_message = Column(Text)
    queued_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))

    stages = relationship("PipelineStage", back_populates="run")
    test_results = relationship("RunTestResult", back_populates="run")

    __table_args__ = (
        CheckConstraint("run_type IN ('full', 'generate_only', 'execute_only')"),
        CheckConstraint("source_type IN ('jira_tickets', 'suite', 'requirements', 'rerun')"),
        CheckConstraint("status IN ('queued', 'running', 'completed', 'failed', 'cancelled')"),
        CheckConstraint("priority IN ('normal', 'high', 'critical')"),
    )


class PipelineStage(Base):
    __tablename__ = "pipeline_stages"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("pipeline_runs.id", ondelete="CASCADE"), nullable=False)
    stage_name = Column(String(50), nullable=False)
    stage_order = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, server_default="pending")
    input_payload = Column(JSON)
    output_payload = Column(JSON)
    attempt = Column(Integer, nullable=False, server_default="1")
    max_attempts = Column(Integer, nullable=False, server_default="1")
    last_error = Column(Text)
    duration_ms = Column(Integer)
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))

    run = relationship("PipelineRun", back_populates="stages")

    __table_args__ = (
        UniqueConstraint("run_id", "stage_order", name="pipeline_stages_run_order_unique"),
        CheckConstraint("stage_name IN ('metadata_refresh', 'jira_read', 'generate', 'store', 'execute', 'record')"),
        CheckConstraint("stage_order BETWEEN 1 AND 6"),
        CheckConstraint("status IN ('pending', 'running', 'passed', 'failed', 'skipped')"),
    )


class RunTestResult(Base):
    __tablename__ = "run_test_results"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("pipeline_runs.id", ondelete="CASCADE"), nullable=False)
    test_case_id = Column(Integer, ForeignKey("test_cases.id"), nullable=False)
    test_case_version_id = Column(Integer, ForeignKey("test_case_versions.id"), nullable=False)
    environment_id = Column(Integer, ForeignKey("environments.id"), nullable=False)
    status = Column(String(20), nullable=False)
    failure_type = Column(String(30))
    failure_summary = Column(Text)
    total_steps = Column(Integer, nullable=False, server_default="0")
    passed_steps = Column(Integer, nullable=False, server_default="0")
    failed_steps = Column(Integer, nullable=False, server_default="0")
    duration_ms = Column(Integer)
    executed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    run = relationship("PipelineRun", back_populates="test_results")
    step_results = relationship("RunStepResult", back_populates="test_result")

    __table_args__ = (
        CheckConstraint("status IN ('passed', 'failed', 'error', 'skipped')"),
    )


class RunStepResult(Base):
    __tablename__ = "run_step_results"

    id = Column(Integer, primary_key=True)
    run_test_result_id = Column(Integer, ForeignKey("run_test_results.id", ondelete="CASCADE"), nullable=False)
    step_order = Column(Integer, nullable=False)
    step_action = Column(String(20), nullable=False)
    target_object = Column(String(255))
    target_record_id = Column(String(20))
    status = Column(String(20), nullable=False)
    execution_state = Column(String(20), nullable=False, server_default="not_started")
    before_state = Column(JSON)
    after_state = Column(JSON)
    field_diff = Column(JSON)
    api_request = Column(JSON)
    api_response = Column(JSON)
    error_message = Column(Text)
    duration_ms = Column(Integer)
    executed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    test_result = relationship("RunTestResult", back_populates="step_results")

    __table_args__ = (
        CheckConstraint("step_action IN ('create', 'update', 'query', 'verify', 'convert', 'wait', 'delete')"),
        CheckConstraint("status IN ('passed', 'failed', 'error', 'skipped')"),
        CheckConstraint("execution_state IN ('not_started', 'in_progress', 'partially_completed', 'completed')"),
    )


class RunArtifact(Base):
    __tablename__ = "run_artifacts"

    id = Column(Integer, primary_key=True)
    run_test_result_id = Column(Integer, ForeignKey("run_test_results.id", ondelete="CASCADE"), nullable=False)
    run_step_result_id = Column(Integer, ForeignKey("run_step_results.id", ondelete="CASCADE"))
    artifact_type = Column(String(30), nullable=False)
    storage_url = Column(String(1000), nullable=False)
    filename = Column(String(255))
    file_size_bytes = Column(Integer)
    captured_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("artifact_type IN ('screenshot', 'log', 'debug_log', 'api_trace')"),
    )


class RunCreatedEntity(Base):
    __tablename__ = "run_created_entities"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("pipeline_runs.id", ondelete="CASCADE"), nullable=False)
    run_step_result_id = Column(Integer, ForeignKey("run_step_results.id", ondelete="CASCADE"), nullable=False)
    entity_type = Column(String(255), nullable=False)
    sf_record_id = Column(String(20), nullable=False)
    creation_source = Column(String(30), nullable=False)
    logical_identifier = Column(String(100))
    primeqa_idempotency_key = Column(String(200))
    creation_fingerprint = Column(String(64))
    parent_entity_id = Column(Integer, ForeignKey("run_created_entities.id", ondelete="SET NULL"))
    cleanup_required = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("creation_source IN ('direct', 'trigger', 'workflow', 'process_builder', 'flow')"),
    )


class RunCleanupAttempt(Base):
    __tablename__ = "run_cleanup_attempts"

    id = Column(Integer, primary_key=True)
    run_created_entity_id = Column(Integer, ForeignKey("run_created_entities.id", ondelete="CASCADE"), nullable=False)
    attempt_number = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False)
    failure_reason = Column(Text)
    failure_type = Column(String(30))
    api_response = Column(JSON)
    attempted_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("run_created_entity_id", "attempt_number",
                         name="run_cleanup_attempts_entity_attempt_unique"),
        CheckConstraint("status IN ('success', 'failed', 'skipped')"),
    )


class ExecutionSlot(Base):
    __tablename__ = "execution_slots"

    id = Column(Integer, primary_key=True)
    environment_id = Column(Integer, ForeignKey("environments.id"), nullable=False)
    run_id = Column(Integer, ForeignKey("pipeline_runs.id", ondelete="CASCADE"), nullable=False)
    acquired_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    released_at = Column(DateTime(timezone=True))


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    id = Column(Integer, primary_key=True)
    worker_id = Column(String(100), nullable=False, unique=True)
    status = Column(String(20), nullable=False, server_default="alive")
    current_run_id = Column(Integer, ForeignKey("pipeline_runs.id", ondelete="SET NULL"))
    current_stage = Column(String(50))
    last_heartbeat = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("status IN ('alive', 'dead')"),
    )
