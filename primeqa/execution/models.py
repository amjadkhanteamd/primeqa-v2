"""SQLAlchemy models for the execution domain.

Tables owned: pipeline_runs, pipeline_stages, run_test_results, run_step_results,
              run_artifacts, run_created_entities, run_cleanup_attempts,
              execution_slots, worker_heartbeats
"""

from sqlalchemy import (
    BigInteger, Column, Integer, String, Boolean, DateTime, Text, JSON, Float,
    ForeignKey, CheckConstraint, UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from primeqa.db import Base


class ExecutionSlot(Base):
    __tablename__ = "execution_slots"

    id = Column(Integer, primary_key=True)
    environment_id = Column(Integer, ForeignKey("environments.id"), nullable=False)
    run_id = Column(Integer, nullable=False)
    acquired_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    released_at = Column(DateTime(timezone=True))


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    id = Column(Integer, primary_key=True)
    worker_id = Column(String(100), nullable=False, unique=True)
    status = Column(String(20), nullable=False, server_default="alive")
    current_run_id = Column(Integer)
    current_stage = Column(String(50))
    last_heartbeat = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # Migration 038 (2026-04-19): observability for worker deaths.
    # Short string captured at shutdown — "SIGTERM", "heartbeat_timeout",
    # or a truncated exception message. Lets ops + the dashboard
    # distinguish graceful deploy-shutdown from crashes.
    died_reason = Column(String(255))
    died_at = Column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("status IN ('alive', 'dead')"),
    )
