"""SQLAlchemy models for the test management domain.

Tables owned: sections, requirements, test_cases, test_case_versions,
              test_suites, suite_test_cases, ba_reviews, metadata_impacts
"""

from sqlalchemy import (
    BigInteger, Column, Integer, Numeric, String, Boolean, DateTime, Text, JSON, Float,
    ForeignKey, CheckConstraint, UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from primeqa.db import Base


class Section(Base):
    __tablename__ = "sections"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    parent_id = Column(Integer, ForeignKey("sections.id", ondelete="CASCADE"))
    name = Column(String(255), nullable=False)
    description = Column(Text)
    position = Column(Integer, nullable=False, server_default="0")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    version = Column(Integer, nullable=False, server_default="1")
    deleted_at = Column(DateTime(timezone=True))
    deleted_by = Column(Integer, ForeignKey("users.id"))

    children = relationship("Section", back_populates="parent")
    parent = relationship("Section", back_populates="children", remote_side=[id])

    __table_args__ = (
        Index("idx_sections_tenant_parent", "tenant_id", "parent_id", "position"),
    )


class GenerationBatch(Base):
    """Links the N test cases produced by a single "Generate" click.

    Rationale (migration 028): multi-TC generation means one click
    produces 3\u20136 TCs covering different scenario angles. This row
    captures the AI's rationale ("why these tests?") surfaced on the
    requirement detail page, plus token / cost for superadmin audit.
    """
    __tablename__ = "generation_batches"

    id = Column(BigInteger, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    requirement_id = Column(Integer, ForeignKey("requirements.id"), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    llm_model = Column(String(100))
    input_tokens = Column(Integer)
    output_tokens = Column(Integer)
    cost_usd = Column(Numeric(10, 4))
    explanation = Column(Text)
    coverage_types = Column(ARRAY(Text))


class Requirement(Base):
    __tablename__ = "requirements"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    section_id = Column(Integer, ForeignKey("sections.id"), nullable=False)
    source = Column(String(20), nullable=False)
    jira_key = Column(String(50))
    jira_summary = Column(String(500))
    jira_description = Column(Text)
    acceptance_criteria = Column(Text)
    jira_version = Column(Integer, nullable=False, server_default="0")
    is_stale = Column(Boolean, nullable=False, server_default="false")
    jira_last_synced = Column(DateTime(timezone=True))
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    version = Column(Integer, nullable=False, server_default="1")
    deleted_at = Column(DateTime(timezone=True))
    deleted_by = Column(Integer, ForeignKey("users.id"))

    __table_args__ = (
        CheckConstraint("source IN ('jira', 'manual')"),
    )


class TestCase(Base):
    __tablename__ = "test_cases"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    requirement_id = Column(Integer, ForeignKey("requirements.id"))
    section_id = Column(Integer, ForeignKey("sections.id"))
    title = Column(String(500), nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    visibility = Column(String(20), nullable=False, server_default="private")
    status = Column(String(20), nullable=False, server_default="draft")
    current_version_id = Column(Integer, ForeignKey("test_case_versions.id"))
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    version = Column(Integer, nullable=False, server_default="1")
    deleted_at = Column(DateTime(timezone=True))
    deleted_by = Column(Integer, ForeignKey("users.id"))
    # R6: flake quarantine
    is_quarantined = Column(Boolean, nullable=False, server_default="false")
    quarantined_at = Column(DateTime(timezone=True))
    quarantined_reason = Column(Text)
    # Multi-TC generation (migration 028): scenario angle the test
    # validates, and the "Generate" click this TC came from so the
    # whole batch can be shown together on the requirement detail.
    coverage_type = Column(String(30))
    generation_batch_id = Column(Integer, ForeignKey("generation_batches.id", ondelete="SET NULL"))

    versions = relationship("TestCaseVersion", back_populates="test_case",
                            foreign_keys="TestCaseVersion.test_case_id")

    __table_args__ = (
        CheckConstraint("requirement_id IS NOT NULL OR section_id IS NOT NULL",
                        name="test_cases_anchor_check"),
        CheckConstraint("visibility IN ('private', 'shared')"),
        CheckConstraint("status IN ('draft', 'approved', 'active')"),
    )


class TestCaseVersion(Base):
    __tablename__ = "test_case_versions"

    id = Column(Integer, primary_key=True)
    test_case_id = Column(Integer, ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False)
    version_number = Column(Integer, nullable=False)
    metadata_version_id = Column(Integer, ForeignKey("meta_versions.id"), nullable=False)
    steps = Column(JSON, nullable=False, server_default="[]")
    expected_results = Column(JSON, nullable=False, server_default="[]")
    preconditions = Column(JSON, nullable=False, server_default="[]")
    generation_method = Column(String(20), nullable=False)
    confidence_score = Column(Float)
    referenced_entities = Column(JSON, nullable=False, server_default="[]")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    test_case = relationship("TestCase", back_populates="versions",
                             foreign_keys=[test_case_id])

    __table_args__ = (
        UniqueConstraint("test_case_id", "version_number",
                         name="test_case_versions_case_number_unique"),
        CheckConstraint("generation_method IN ('ai', 'manual', 'regenerated')"),
    )


class TestSuite(Base):
    __tablename__ = "test_suites"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    suite_type = Column(String(30), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    version = Column(Integer, nullable=False, server_default="1")
    deleted_at = Column(DateTime(timezone=True))
    deleted_by = Column(Integer, ForeignKey("users.id"))

    __table_args__ = (
        CheckConstraint("suite_type IN ('regression', 'smoke', 'sprint', 'custom')"),
    )


class SuiteTestCase(Base):
    __tablename__ = "suite_test_cases"

    id = Column(Integer, primary_key=True)
    suite_id = Column(Integer, ForeignKey("test_suites.id", ondelete="CASCADE"), nullable=False)
    test_case_id = Column(Integer, ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False)
    position = Column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        UniqueConstraint("suite_id", "test_case_id", name="suite_test_cases_unique"),
    )


class BAReview(Base):
    __tablename__ = "ba_reviews"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    test_case_version_id = Column(Integer, ForeignKey("test_case_versions.id", ondelete="CASCADE"), nullable=False)
    assigned_to = Column(Integer, ForeignKey("users.id"), nullable=False)
    reviewed_by = Column(Integer, ForeignKey("users.id"))
    status = Column(String(20), nullable=False, server_default="pending")
    feedback = Column(Text)
    step_comments = Column(JSON, nullable=False, server_default="[]")
    reviewed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    version = Column(Integer, nullable=False, server_default="1")
    deleted_at = Column(DateTime(timezone=True))
    deleted_by = Column(Integer, ForeignKey("users.id"))

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'approved', 'rejected', 'needs_edit')"),
    )


class Tag(Base):
    __tablename__ = "tags"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name = Column(String(100), nullable=False)
    color = Column(String(20), server_default="gray")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="tags_tenant_name_unique"),)


class TestCaseTag(Base):
    __tablename__ = "test_case_tags"
    id = Column(Integer, primary_key=True)
    test_case_id = Column(Integer, ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False)
    tag_id = Column(Integer, ForeignKey("tags.id", ondelete="CASCADE"), nullable=False)
    __table_args__ = (UniqueConstraint("test_case_id", "tag_id", name="test_case_tags_unique"),)


class Milestone(Base):
    __tablename__ = "milestones"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    due_date = Column(DateTime(timezone=True))
    status = Column(String(20), nullable=False, server_default="active")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="milestones_tenant_name_unique"),
        CheckConstraint("status IN ('active', 'completed', 'archived')"),
    )


class MilestoneSuite(Base):
    __tablename__ = "milestone_suites"
    id = Column(Integer, primary_key=True)
    milestone_id = Column(Integer, ForeignKey("milestones.id", ondelete="CASCADE"), nullable=False)
    suite_id = Column(Integer, ForeignKey("test_suites.id", ondelete="CASCADE"), nullable=False)
    added_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (UniqueConstraint("milestone_id", "suite_id", name="milestone_suites_unique"),)


class CustomField(Base):
    __tablename__ = "custom_fields"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    entity_type = Column(String(30), nullable=False)
    name = Column(String(100), nullable=False)
    field_type = Column(String(20), nullable=False)
    options = Column(JSON, nullable=False, server_default="[]")
    required = Column(Boolean, nullable=False, server_default="false")
    position = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        CheckConstraint("entity_type IN ('test_case', 'test_case_version', 'release', 'suite')"),
        CheckConstraint("field_type IN ('text', 'number', 'date', 'select', 'multiselect', 'user')"),
        UniqueConstraint("tenant_id", "entity_type", "name", name="custom_fields_unique"),
    )


class CustomFieldValue(Base):
    __tablename__ = "custom_field_values"
    id = Column(Integer, primary_key=True)
    custom_field_id = Column(Integer, ForeignKey("custom_fields.id", ondelete="CASCADE"), nullable=False)
    entity_id = Column(Integer, nullable=False)
    value = Column(JSON)
    __table_args__ = (UniqueConstraint("custom_field_id", "entity_id", name="custom_field_values_unique"),)


class StepTemplate(Base):
    __tablename__ = "step_templates"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    steps = Column(JSON, nullable=False, server_default="[]")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    usage_count = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="step_templates_tenant_name_unique"),)


class TestCaseParameterSet(Base):
    __tablename__ = "test_case_parameter_sets"
    id = Column(Integer, primary_key=True)
    test_case_version_id = Column(Integer, ForeignKey("test_case_versions.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    parameters = Column(JSON, nullable=False, server_default="{}")
    is_default = Column(Boolean, nullable=False, server_default="false")
    position = Column(Integer, nullable=False, server_default="0")
    __table_args__ = (UniqueConstraint("test_case_version_id", "name", name="test_case_parameter_sets_unique"),)


class MetadataImpact(Base):
    __tablename__ = "metadata_impacts"

    id = Column(Integer, primary_key=True)
    new_meta_version_id = Column(Integer, ForeignKey("meta_versions.id", ondelete="CASCADE"), nullable=False)
    prev_meta_version_id = Column(Integer, ForeignKey("meta_versions.id", ondelete="CASCADE"), nullable=False)
    test_case_id = Column(Integer, ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False)
    impact_type = Column(String(30), nullable=False)
    entity_ref = Column(String(255), nullable=False)
    change_details = Column(JSON, nullable=False, server_default="{}")
    resolution = Column(String(20), nullable=False, server_default="pending")
    resolved_by = Column(Integer, ForeignKey("users.id"))
    resolved_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deleted_at = Column(DateTime(timezone=True))
    deleted_by = Column(Integer, ForeignKey("users.id"))

    __table_args__ = (
        CheckConstraint(
            "impact_type IN ('field_removed', 'field_added', 'field_changed', "
            "'vr_changed', 'flow_changed', 'trigger_changed')"
        ),
        CheckConstraint("resolution IN ('pending', 'regenerated', 'edited', 'dismissed')"),
    )
