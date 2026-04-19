"""SQLAlchemy models for the intelligence domain.

Tables owned: entity_dependencies, explanation_requests, failure_patterns,
              behaviour_facts, step_causal_links, agent_fix_attempts,
              llm_usage_log
"""

from sqlalchemy import (
    BigInteger, Column, Integer, Numeric, String, Boolean, DateTime, Text,
    JSON, Float, ForeignKey, CheckConstraint, UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import JSONB
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


class AgentFixAttempt(Base):
    """One row per agent triage/fix proposal (R5).

    Forms the audit log for the Agent fixes tab on run detail, the ledger
    for Revert/Accept/Edit decisions, and the training corpus for the
    next-generation agent.
    """
    __tablename__ = "agent_fix_attempts"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("pipeline_runs.id", ondelete="CASCADE"),
                    nullable=False)
    test_case_id = Column(Integer, ForeignKey("test_cases.id", ondelete="CASCADE"),
                          nullable=False)
    run_test_result_id = Column(Integer, ForeignKey("run_test_results.id",
                                                    ondelete="SET NULL"))
    run_step_result_id = Column(Integer, ForeignKey("run_step_results.id",
                                                    ondelete="SET NULL"))
    failure_class = Column(String(40))
    pattern_id = Column(Integer, ForeignKey("failure_patterns.id",
                                            ondelete="SET NULL"))
    root_cause_summary = Column(Text)
    confidence = Column(Float)
    trust_band = Column(String(10))
    proposed_fix_type = Column(String(40))
    before_state = Column(JSON)
    after_state = Column(JSON)
    auto_applied = Column(Boolean, nullable=False, server_default="false")
    rerun_run_id = Column(Integer, ForeignKey("pipeline_runs.id",
                                              ondelete="SET NULL"))
    rerun_outcome = Column(String(20))
    user_decision = Column(String(20))
    decided_at = Column(DateTime(timezone=True))
    decided_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), nullable=False,
                        server_default=func.now())

    __table_args__ = (
        CheckConstraint("trust_band IS NULL OR trust_band IN ('high','medium','low')",
                        name="afa_trust_band_ck"),
        CheckConstraint("user_decision IS NULL OR user_decision IN "
                        "('accepted','reverted','edited')",
                        name="afa_user_decision_ck"),
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


class LLMUsageLog(Base):
    """One row per LLM call across every feature (migration 031).

    Populated by primeqa.intelligence.llm.usage.record() from the
    LLMGateway. Read back by the /settings/llm-usage superadmin
    dashboard (Phase 3) and per-run cost panel.

    Keep this append-only: dashboards do aggregate rollups, they don't
    mutate rows. No PII in here \u2014 prompt text + response text live in
    agent_fix_attempts / explanation_requests for the tasks that need
    them, not in this table.
    """
    __tablename__ = "llm_usage_log"

    id = Column(BigInteger, primary_key=True)
    ts = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))

    task = Column(String(40), nullable=False)
    model = Column(String(100), nullable=False)
    prompt_version = Column(String(60), nullable=False)

    input_tokens = Column(Integer, nullable=False, server_default="0")
    output_tokens = Column(Integer, nullable=False, server_default="0")
    cached_input_tokens = Column(Integer, nullable=False, server_default="0")
    cache_write_tokens = Column(Integer, nullable=False, server_default="0")

    cost_usd = Column(Numeric(10, 6), nullable=False, server_default="0")
    latency_ms = Column(Integer)
    status = Column(String(20), nullable=False, server_default="ok")

    complexity = Column(String(10))
    escalated = Column(Boolean, nullable=False, server_default="false")
    request_id = Column(String(80))

    run_id = Column(Integer, ForeignKey("pipeline_runs.id", ondelete="SET NULL"))
    requirement_id = Column(Integer, ForeignKey("requirements.id", ondelete="SET NULL"))
    test_case_id = Column(Integer, ForeignKey("test_cases.id", ondelete="SET NULL"))
    generation_batch_id = Column(BigInteger, ForeignKey("generation_batches.id", ondelete="SET NULL"))

    context = Column(JSONB, nullable=False, server_default="{}")
