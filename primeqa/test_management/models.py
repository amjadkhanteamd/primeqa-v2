"""SQLAlchemy models for the test management domain.

Tables owned: sections, requirements, test_cases, test_case_versions,
              test_suites, suite_test_cases, ba_reviews, metadata_impacts
"""

from sqlalchemy import (
    BigInteger, Column, Integer, Numeric, String, Boolean, DateTime, Text, JSON, Float,
    ForeignKey, CheckConstraint, UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
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


class Tag(Base):
    __tablename__ = "tags"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name = Column(String(100), nullable=False)
    color = Column(String(20), server_default="gray")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="tags_tenant_name_unique"),)


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

