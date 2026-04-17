"""SQLAlchemy models for the release domain.

Tables owned: releases, release_requirements, release_impacts,
              release_test_plan_items, release_runs, release_decisions
"""

from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Date, Text, JSON, Float,
    ForeignKey, CheckConstraint, UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from primeqa.db import Base


class Release(Base):
    __tablename__ = "releases"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name = Column(String(255), nullable=False)
    version_tag = Column(String(100))
    description = Column(Text)
    status = Column(String(30), nullable=False, server_default="planning")
    target_date = Column(Date)
    decision_criteria = Column(JSON, nullable=False, server_default="{}")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    requirements = relationship("ReleaseRequirement", back_populates="release", cascade="all, delete-orphan")
    impacts = relationship("ReleaseImpact", back_populates="release", cascade="all, delete-orphan")
    test_plan_items = relationship("ReleaseTestPlanItem", back_populates="release", cascade="all, delete-orphan")
    runs = relationship("ReleaseRun", back_populates="release", cascade="all, delete-orphan")
    decisions = relationship("ReleaseDecision", back_populates="release", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="releases_tenant_name_unique"),
        CheckConstraint("status IN ('planning', 'in_progress', 'ready', 'decided', 'shipped', 'cancelled')"),
    )


class ReleaseRequirement(Base):
    __tablename__ = "release_requirements"

    id = Column(Integer, primary_key=True)
    release_id = Column(Integer, ForeignKey("releases.id", ondelete="CASCADE"), nullable=False)
    requirement_id = Column(Integer, ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False)
    added_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    added_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    release = relationship("Release", back_populates="requirements")

    __table_args__ = (
        UniqueConstraint("release_id", "requirement_id", name="release_requirements_unique"),
    )


class ReleaseImpact(Base):
    __tablename__ = "release_impacts"

    id = Column(Integer, primary_key=True)
    release_id = Column(Integer, ForeignKey("releases.id", ondelete="CASCADE"), nullable=False)
    metadata_impact_id = Column(Integer, ForeignKey("metadata_impacts.id", ondelete="CASCADE"), nullable=False)
    risk_score = Column(Integer)
    risk_level = Column(String(20))
    risk_reasoning = Column(JSON)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    release = relationship("Release", back_populates="impacts")

    __table_args__ = (
        UniqueConstraint("release_id", "metadata_impact_id", name="release_impacts_unique"),
        CheckConstraint("risk_level IS NULL OR risk_level IN ('low', 'medium', 'high', 'critical')"),
    )


class ReleaseTestPlanItem(Base):
    __tablename__ = "release_test_plan_items"

    id = Column(Integer, primary_key=True)
    release_id = Column(Integer, ForeignKey("releases.id", ondelete="CASCADE"), nullable=False)
    test_case_id = Column(Integer, ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False)
    priority = Column(String(20), nullable=False, server_default="medium")
    position = Column(Integer, nullable=False, server_default="0")
    risk_score = Column(Integer)
    inclusion_reason = Column(String(50))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    release = relationship("Release", back_populates="test_plan_items")

    __table_args__ = (
        UniqueConstraint("release_id", "test_case_id", name="release_test_plan_items_unique"),
        CheckConstraint("priority IN ('low', 'medium', 'high', 'critical')"),
    )


class ReleaseRun(Base):
    __tablename__ = "release_runs"

    id = Column(Integer, primary_key=True)
    release_id = Column(Integer, ForeignKey("releases.id", ondelete="CASCADE"), nullable=False)
    pipeline_run_id = Column(Integer, ForeignKey("pipeline_runs.id", ondelete="CASCADE"), nullable=False)
    triggered_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    triggered_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    release = relationship("Release", back_populates="runs")

    __table_args__ = (
        UniqueConstraint("release_id", "pipeline_run_id", name="release_runs_unique"),
    )


class ReleaseDecision(Base):
    __tablename__ = "release_decisions"

    id = Column(Integer, primary_key=True)
    release_id = Column(Integer, ForeignKey("releases.id", ondelete="CASCADE"), nullable=False)
    recommendation = Column(String(20), nullable=False)
    confidence = Column(Float)
    reasoning = Column(JSON)
    criteria_met = Column(JSON)
    recommended_by = Column(String(20), nullable=False, server_default="ai")
    final_decision = Column(String(20))
    decided_by = Column(Integer, ForeignKey("users.id"))
    decided_at = Column(DateTime(timezone=True))
    override_reason = Column(Text)
    # R5 / Q3: when true (default), CI-facing /status reflects the post-agent
    # verdict. Super Admin can flip per release if pre-agent truth is required.
    agent_verdict_counts = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    release = relationship("Release", back_populates="decisions")

    __table_args__ = (
        CheckConstraint("recommendation IN ('go', 'conditional_go', 'no_go')"),
        CheckConstraint("recommended_by IN ('ai', 'human')"),
        CheckConstraint("final_decision IS NULL OR final_decision IN ('go', 'conditional_go', 'no_go')"),
    )
