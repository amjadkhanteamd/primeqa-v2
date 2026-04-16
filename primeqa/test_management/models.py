"""SQLAlchemy models for the test management domain.

Tables owned: sections, requirements, test_cases, test_case_versions,
              test_suites, suite_test_cases, ba_reviews, metadata_impacts
"""

from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Text, JSON, Float,
    ForeignKey, CheckConstraint, UniqueConstraint, Index,
)
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

    children = relationship("Section", back_populates="parent")
    parent = relationship("Section", back_populates="children", remote_side=[id])

    __table_args__ = (
        Index("idx_sections_tenant_parent", "tenant_id", "parent_id", "position"),
    )


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
    reviewed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'approved', 'rejected', 'needs_edit')"),
    )


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

    __table_args__ = (
        CheckConstraint(
            "impact_type IN ('field_removed', 'field_added', 'field_changed', "
            "'vr_changed', 'flow_changed', 'trigger_changed')"
        ),
        CheckConstraint("resolution IN ('pending', 'regenerated', 'edited', 'dismissed')"),
    )
