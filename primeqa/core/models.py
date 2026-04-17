"""SQLAlchemy models for the core domain.

Tables owned: tenants, users, refresh_tokens, environments,
              environment_credentials, activity_log
"""

from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Text, JSON,
    ForeignKey, CheckConstraint, UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from primeqa.db import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), nullable=False, unique=True)
    status = Column(String(20), nullable=False, server_default="active")
    settings = Column(JSON, nullable=False, server_default="{}")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    users = relationship("User", back_populates="tenant")
    environments = relationship("Environment", back_populates="tenant")

    __table_args__ = (
        CheckConstraint("status IN ('active', 'suspended')", name="tenants_status_check"),
    )


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    email = Column(String(255), nullable=False)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False)
    is_active = Column(Boolean, nullable=False, server_default="true")
    last_login_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    tenant = relationship("Tenant", back_populates="users")

    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="users_tenant_email_unique"),
        CheckConstraint("role IN ('admin', 'tester', 'ba', 'viewer')", name="users_role_check"),
        Index("idx_users_tenant_active", "tenant_id", postgresql_where="is_active = true"),
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(String(255), nullable=False, unique=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked = Column(Boolean, nullable=False, server_default="false")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Environment(Base):
    __tablename__ = "environments"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name = Column(String(255), nullable=False)
    env_type = Column(String(30), nullable=False)
    sf_instance_url = Column(String(500), nullable=False)
    sf_api_version = Column(String(10), nullable=False)
    execution_policy = Column(String(20), nullable=False, server_default="full")
    capture_mode = Column(String(20), nullable=False, server_default="smart")
    max_execution_slots = Column(Integer, nullable=False, server_default="2")
    cleanup_mandatory = Column(Boolean, nullable=False, server_default="false")
    current_meta_version_id = Column(Integer, ForeignKey("meta_versions.id"))
    is_active = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    created_by = Column(Integer, ForeignKey("users.id"))
    connection_id = Column(Integer, ForeignKey("connections.id"))

    tenant = relationship("Tenant", back_populates="environments")
    credentials = relationship("EnvironmentCredential", back_populates="environment", uselist=False)
    connection = relationship("Connection")

    __table_args__ = (
        CheckConstraint("env_type IN ('sandbox', 'uat', 'staging', 'production')"),
        CheckConstraint("execution_policy IN ('full', 'read_only', 'disabled')"),
        CheckConstraint("capture_mode IN ('minimal', 'smart', 'full')"),
        Index("idx_environments_tenant", "tenant_id", postgresql_where="is_active = true"),
    )


class EnvironmentCredential(Base):
    __tablename__ = "environment_credentials"

    id = Column(Integer, primary_key=True)
    environment_id = Column(Integer, ForeignKey("environments.id", ondelete="CASCADE"), nullable=False, unique=True)
    client_id = Column(String(500), nullable=False)
    client_secret = Column(String(500), nullable=False)
    access_token = Column(String(2000))
    refresh_token = Column(String(2000))
    token_expires_at = Column(DateTime(timezone=True))
    last_refreshed_at = Column(DateTime(timezone=True))
    status = Column(String(20), nullable=False, server_default="valid")

    environment = relationship("Environment", back_populates="credentials")

    __table_args__ = (
        CheckConstraint("status IN ('valid', 'expired', 'failed')"),
    )


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"))
    action = Column(String(50), nullable=False)
    entity_type = Column(String(50), nullable=False)
    entity_id = Column(Integer)
    details = Column(JSON, nullable=False, server_default="{}")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_activity_log_created_at_desc", "created_at"),
        Index("idx_activity_log_tenant_created", "tenant_id", "created_at"),
        Index("idx_activity_log_entity", "entity_type", "entity_id"),
    )


class Group(Base):
    __tablename__ = "groups"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    members = relationship("GroupMember", back_populates="group", cascade="all, delete-orphan")
    group_environments = relationship("GroupEnvironment", back_populates="group", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="groups_tenant_name_unique"),
    )


class GroupMember(Base):
    __tablename__ = "group_members"

    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    added_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    added_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    group = relationship("Group", back_populates="members")
    user = relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="group_members_unique"),
    )


class GroupEnvironment(Base):
    __tablename__ = "group_environments"

    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    environment_id = Column(Integer, ForeignKey("environments.id", ondelete="CASCADE"), nullable=False)
    added_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    added_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    group = relationship("Group", back_populates="group_environments")
    environment = relationship("Environment")

    __table_args__ = (
        UniqueConstraint("group_id", "environment_id", name="group_environments_unique"),
    )


class Connection(Base):
    __tablename__ = "connections"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    connection_type = Column(String(20), nullable=False)
    name = Column(String(255), nullable=False)
    config = Column(JSON, nullable=False, server_default="{}")
    status = Column(String(20), nullable=False, server_default="inactive")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("connection_type IN ('salesforce', 'jira', 'llm')"),
        CheckConstraint("status IN ('active', 'inactive', 'error')"),
        UniqueConstraint("tenant_id", "name", name="connections_tenant_name_unique"),
    )
