"""SQLAlchemy models for the execution domain.

Tables owned: worker_heartbeats. (The v1 pipeline_runs / run_* / pipeline_stages /
execution_slots tables were dropped in migration 053 — D-221.5; the ExecutionSlot
model retired in D-240.)
"""

from sqlalchemy import Column, Integer, String, DateTime, CheckConstraint
from sqlalchemy.sql import func

from primeqa.db import Base


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
