"""SQLAlchemy models for the metadata domain.

Tables owned: meta_versions, meta_objects, meta_fields,
              meta_validation_rules, meta_flows, meta_triggers, meta_record_types
"""

from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Text, JSON,
    ForeignKey, CheckConstraint, UniqueConstraint, Index, Float,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from primeqa.db import Base


class MetaVersion(Base):
    __tablename__ = "meta_versions"

    id = Column(Integer, primary_key=True)
    environment_id = Column(Integer, ForeignKey("environments.id", ondelete="CASCADE"), nullable=False)
    version_label = Column(String(20), nullable=False)
    snapshot_hash = Column(String(64))
    status = Column(String(20), nullable=False, server_default="in_progress")
    lifecycle = Column(String(20), nullable=False, server_default="active")
    object_count = Column(Integer, nullable=False, server_default="0")
    field_count = Column(Integer, nullable=False, server_default="0")
    vr_count = Column(Integer, nullable=False, server_default="0")
    flow_count = Column(Integer, nullable=False, server_default="0")
    trigger_count = Column(Integer, nullable=False, server_default="0")
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at = Column(DateTime(timezone=True))

    objects = relationship("MetaObject", back_populates="meta_version")

    __table_args__ = (
        UniqueConstraint("environment_id", "version_label", name="meta_versions_env_label_unique"),
        CheckConstraint("status IN ('in_progress', 'complete', 'partial', 'failed')"),
        CheckConstraint("lifecycle IN ('active', 'archived', 'deleted')"),
    )


class MetaObject(Base):
    __tablename__ = "meta_objects"

    id = Column(Integer, primary_key=True)
    meta_version_id = Column(Integer, ForeignKey("meta_versions.id", ondelete="CASCADE"), nullable=False)
    api_name = Column(String(255), nullable=False)
    label = Column(String(255))
    key_prefix = Column(String(5))
    is_custom = Column(Boolean, nullable=False, server_default="false")
    is_queryable = Column(Boolean, nullable=False, server_default="true")
    is_createable = Column(Boolean, nullable=False, server_default="true")
    is_updateable = Column(Boolean, nullable=False, server_default="true")
    is_deletable = Column(Boolean, nullable=False, server_default="true")

    meta_version = relationship("MetaVersion", back_populates="objects")
    fields = relationship("MetaField", back_populates="meta_object")

    __table_args__ = (
        UniqueConstraint("meta_version_id", "api_name", name="meta_objects_version_apiname_unique"),
    )


class MetaField(Base):
    __tablename__ = "meta_fields"

    id = Column(Integer, primary_key=True)
    meta_version_id = Column(Integer, ForeignKey("meta_versions.id", ondelete="CASCADE"), nullable=False)
    meta_object_id = Column(Integer, ForeignKey("meta_objects.id", ondelete="CASCADE"), nullable=False)
    api_name = Column(String(255), nullable=False)
    label = Column(String(255))
    field_type = Column(String(50), nullable=False)
    is_required = Column(Boolean, nullable=False, server_default="false")
    is_custom = Column(Boolean, nullable=False, server_default="false")
    is_createable = Column(Boolean, nullable=False, server_default="true")
    is_updateable = Column(Boolean, nullable=False, server_default="true")
    reference_to = Column(String(255))
    length = Column(Integer)
    precision = Column(Integer)
    scale = Column(Integer)
    picklist_values = Column(JSON)
    default_value = Column(String(500))

    meta_object = relationship("MetaObject", back_populates="fields")

    __table_args__ = (
        UniqueConstraint("meta_version_id", "meta_object_id", "api_name",
                         name="meta_fields_version_object_apiname_unique"),
        Index("idx_meta_fields_object", "meta_object_id"),
    )


class MetaValidationRule(Base):
    __tablename__ = "meta_validation_rules"

    id = Column(Integer, primary_key=True)
    meta_version_id = Column(Integer, ForeignKey("meta_versions.id", ondelete="CASCADE"), nullable=False)
    meta_object_id = Column(Integer, ForeignKey("meta_objects.id", ondelete="CASCADE"), nullable=False)
    rule_name = Column(String(255), nullable=False)
    error_condition_formula = Column(Text)
    error_message = Column(Text)
    is_active = Column(Boolean, nullable=False, server_default="true")

    meta_object = relationship("MetaObject")


class MetaFlow(Base):
    __tablename__ = "meta_flows"

    id = Column(Integer, primary_key=True)
    meta_version_id = Column(Integer, ForeignKey("meta_versions.id", ondelete="CASCADE"), nullable=False)
    api_name = Column(String(255), nullable=False)
    label = Column(String(255))
    flow_type = Column(String(50), nullable=False)
    trigger_object = Column(String(255))
    trigger_event = Column(String(50))
    is_active = Column(Boolean, nullable=False, server_default="true")
    entry_conditions = Column(JSON)

    __table_args__ = (
        CheckConstraint(
            "flow_type IN ('autolaunched', 'record_triggered', 'screen', 'process_builder')"
        ),
    )


class MetaTrigger(Base):
    __tablename__ = "meta_triggers"

    id = Column(Integer, primary_key=True)
    meta_version_id = Column(Integer, ForeignKey("meta_versions.id", ondelete="CASCADE"), nullable=False)
    meta_object_id = Column(Integer, ForeignKey("meta_objects.id", ondelete="CASCADE"), nullable=False)
    trigger_name = Column(String(255), nullable=False)
    events = Column(String(255))
    is_active = Column(Boolean, nullable=False, server_default="true")

    meta_object = relationship("MetaObject")


class MetaRecordType(Base):
    __tablename__ = "meta_record_types"

    id = Column(Integer, primary_key=True)
    meta_version_id = Column(Integer, ForeignKey("meta_versions.id", ondelete="CASCADE"), nullable=False)
    meta_object_id = Column(Integer, ForeignKey("meta_objects.id", ondelete="CASCADE"), nullable=False)
    api_name = Column(String(255), nullable=False)
    label = Column(String(255))
    is_active = Column(Boolean, nullable=False, server_default="true")
    is_default = Column(Boolean, nullable=False, server_default="false")
