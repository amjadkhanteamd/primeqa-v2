"""SQLAlchemy models for the intelligence domain.

Tables owned: entity_dependencies, explanation_requests, failure_patterns,
              behaviour_facts, step_causal_links
"""

from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Text, JSON, Float,
    ForeignKey, CheckConstraint, UniqueConstraint, Index,
)
from sqlalchemy.sql import func

from primeqa.db import Base


class EntityDependency(Base):
    __tablename__ = "entity_dependencies"

    id = Column(Integer, primary_key=True)
    meta_version_id = Column(Integer, ForeignKey("meta_versions.id", ondelete="CASCADE"), nullable=False)
    source_entity = Column(String(255), nullable=False)
    source_type = Column(String(30), nullable=False)
    target_entity = Column(String(255), nullable=False)
    dependency_type = Column(String(20), nullable=False)
    discovery_source = Column(String(20), nullable=False, server_default="metadata_parse")
    confidence = Column(Float, nullable=False, server_default="1.0")

    __table_args__ = (
        CheckConstraint("source_type IN ('flow', 'trigger', 'validation_rule', 'process_builder', 'workflow_rule')"),
        CheckConstraint("dependency_type IN ('creates', 'updates', 'reads', 'deletes', 'validates')"),
        CheckConstraint("discovery_source IN ('metadata_parse', 'execution_trace', 'inferred', 'manual')"),
        CheckConstraint("confidence BETWEEN 0.0 AND 1.0"),
    )


class ExplanationRequest(Base):
    __tablename__ = "explanation_requests"

    id = Column(Integer, primary_key=True)
    run_test_result_id = Column(Integer, ForeignKey("run_test_results.id", ondelete="CASCADE"), nullable=False)
    run_step_result_id = Column(Integer, ForeignKey("run_step_results.id", ondelete="CASCADE"))
    explanation_type = Column(String(30), nullable=False)
    structured_input = Column(JSON, nullable=False)
    llm_response = Column(JSON)
    parsed_explanation = Column(JSON)
    model_used = Column(String(50))
    prompt_tokens = Column(Integer)
    completion_tokens = Column(Integer)
    requested_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at = Column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "explanation_type IN ('failure_analysis', 'root_cause', 'impact_assessment', 'anomaly_detection')"
        ),
    )


class FailurePattern(Base):
    __tablename__ = "failure_patterns"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    environment_id = Column(Integer, ForeignKey("environments.id"))
    pattern_signature = Column(String(64), nullable=False)
    failure_type = Column(String(30), nullable=False)
    root_entity = Column(String(255))
    description = Column(Text)
    occurrence_count = Column(Integer, nullable=False, server_default="1")
    confidence = Column(Float, nullable=False, server_default="1.0")
    affected_test_case_ids = Column(JSON, nullable=False, server_default="[]")
    status = Column(String(20), nullable=False, server_default="active")
    first_seen = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_validated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("tenant_id", "environment_id", "pattern_signature",
                         name="failure_patterns_tenant_signature_unique"),
        CheckConstraint("confidence BETWEEN 0.0 AND 1.0"),
        CheckConstraint("status IN ('active', 'decayed', 'resolved')"),
    )


class BehaviourFact(Base):
    __tablename__ = "behaviour_facts"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    environment_id = Column(Integer, ForeignKey("environments.id"), nullable=False)
    entity_ref = Column(String(255), nullable=False)
    fact_type = Column(String(30), nullable=False)
    fact_description = Column(Text, nullable=False)
    source = Column(String(20), nullable=False)
    confidence = Column(Float, nullable=False, server_default="1.0")
    is_active = Column(Boolean, nullable=False, server_default="true")
    learned_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("fact_type IN ('constraint', 'default', 'side_effect', 'sequence', 'dependency')"),
        CheckConstraint("source IN ('seeded', 'learned', 'ba_feedback', 'execution_trace')"),
        CheckConstraint("confidence BETWEEN 0.0 AND 1.0"),
    )


class StepCausalLink(Base):
    __tablename__ = "step_causal_links"

    id = Column(Integer, primary_key=True)
    run_test_result_id = Column(Integer, ForeignKey("run_test_results.id", ondelete="CASCADE"), nullable=False)
    from_step_result_id = Column(Integer, ForeignKey("run_step_results.id", ondelete="CASCADE"), nullable=False)
    to_step_result_id = Column(Integer, ForeignKey("run_step_results.id", ondelete="CASCADE"), nullable=False)
    link_type = Column(String(30), nullable=False)
    reason = Column(Text)
    confidence = Column(Float, nullable=False, server_default="1.0")
    discovery_source = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "link_type IN ('data_dependency', 'trigger_cascade', 'validation_block', "
            "'state_mutation', 'cleanup_dependency')"
        ),
        CheckConstraint("confidence BETWEEN 0.0 AND 1.0"),
        CheckConstraint("discovery_source IN ('execution_trace', 'metadata_analysis', 'llm_inferred')"),
        CheckConstraint("from_step_result_id <> to_step_result_id", name="step_causal_links_no_self"),
    )
