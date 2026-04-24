"""SQLAlchemy models for the core domain.

Tables owned: tenants, users, refresh_tokens, environments,
              environment_credentials, activity_log
"""

from sqlalchemy import (
    Column, Integer, Float, String, Boolean, DateTime, Text, JSON,
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
    # Migration 039: DEPRECATED — use permission_sets instead.
    # Kept as a fallback for code paths still reading legacy role checks.
    # Remove once every caller has been migrated to permission-set checks.
    role = Column(String(20), nullable=False)
    is_active = Column(Boolean, nullable=False, server_default="true")
    last_login_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # Migration 040: optional override for post-login redirect. NULL (default)
    # means the landing page is computed from the permission-set union by
    # primeqa.core.navigation.get_landing_page.
    preferred_landing_page = Column(String(50))
    # Migration 041: user's "active org" — what the Active Org switcher
    # on /tickets last pinned. Resolved via
    # primeqa.runs.my_tickets.resolve_active_environment when NULL.
    preferred_environment_id = Column(Integer, ForeignKey("environments.id", ondelete="SET NULL"))

    tenant = relationship("Tenant", back_populates="users")

    # Migration 039: assigned permission sets (many-to-many via UserPermissionSet).
    # Resolving the effective permission set for this user is the job of
    # primeqa.core.permissions.get_effective_permissions(user_id, db) —
    # the SQLAlchemy relationship is here for eager loading / cascade only.
    permission_set_assignments = relationship(
        "UserPermissionSet",
        primaryjoin="User.id == UserPermissionSet.user_id",
        foreign_keys="UserPermissionSet.user_id",
        cascade="all, delete-orphan",
    )

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
    jira_connection_id = Column(Integer, ForeignKey("connections.id"))
    llm_connection_id = Column(Integer, ForeignKey("connections.id"))

    # Migration 039: run policies + ownership.
    # These flags are the environment half of the two-layer access check
    # (see CLAUDE.md ## Permission Model). Even a user with `run_suite` in
    # their permission-set union can't trigger a bulk run against an env
    # where allow_bulk_run=false. `is_production` + `require_approval`
    # gate the release state machine.
    allow_single_run = Column(Boolean, nullable=False, server_default="true")
    allow_bulk_run = Column(Boolean, nullable=False, server_default="true")
    allow_scheduled_run = Column(Boolean, nullable=False, server_default="false")
    is_production = Column(Boolean, nullable=False, server_default="false")
    require_approval = Column(Boolean, nullable=False, server_default="false")
    max_api_calls_per_run = Column(Integer)

    # Environment types: 'team' (default, shared) or 'personal' (owned by
    # a single user, visible only to owner + admins). parent_team_env_id
    # lets a personal env reference the team env it clones.
    environment_type = Column(String(20), nullable=False, server_default="team")
    owner_user_id = Column(Integer, ForeignKey("users.id"))
    parent_team_env_id = Column(Integer, ForeignKey("environments.id"))

    tenant = relationship("Tenant", back_populates="environments")
    credentials = relationship("EnvironmentCredential", back_populates="environment", uselist=False)
    connection = relationship("Connection", foreign_keys=[connection_id])
    jira_connection = relationship("Connection", foreign_keys=[jira_connection_id])
    llm_connection = relationship("Connection", foreign_keys=[llm_connection_id])

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


class TenantAgentSettings(Base):
    """Per-tenant agent autonomy + LLM policy (R2 + Phase 2 LLM gateway)."""
    __tablename__ = "tenant_agent_settings"

    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"),
                       primary_key=True)
    agent_enabled = Column(Boolean, nullable=False, server_default="true")
    trust_threshold_high = Column(String(10), nullable=False, server_default="0.85")
    trust_threshold_medium = Column(String(10), nullable=False, server_default="0.60")
    max_fix_attempts_per_run = Column(Integer, nullable=False, server_default="3")
    updated_by = Column(Integer, ForeignKey("users.id"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Migration 032: LLM rate limits + policy flags. NULL = unlimited
    # (tenant uses the tier preset instead — see migration 034).
    llm_max_calls_per_minute = Column(Integer)
    llm_max_calls_per_hour = Column(Integer)
    llm_max_spend_per_day_usd = Column(Float)
    llm_always_use_opus = Column(Boolean, nullable=False, server_default="false")
    llm_allow_haiku = Column(Boolean, nullable=False, server_default="true")

    # Migration 034: product tier. Named bundles of the five fields above;
    # NULL overrides win over the preset. Values: starter | pro | enterprise
    # | custom. `custom` bypasses the preset entirely (raw columns only).
    llm_tier = Column(String(20), nullable=False, server_default="starter")

    # Migration 048: per-tenant feature flag for the story-view enricher.
    # When off, generate_test_plan skips the Haiku enrichment call — no
    # LLM cost, no story_view populated. Default off so existing tenants
    # don't silently start spending on the feature.
    llm_enable_story_enrichment = Column(
        Boolean, nullable=False, server_default="false",
    )

    # Migration 049: per-tenant feature flag for Domain Packs — long-form
    # prescriptive Salesforce knowledge injected into test_plan_generation
    # when the requirement text matches a pack's keywords. When off,
    # generation.py skips the DomainPackProvider call entirely (no
    # filesystem IO, no prompt overhead). Default off; superadmin opts
    # tenants in via /settings/llm-usage.
    llm_enable_domain_packs = Column(
        Boolean, nullable=False, server_default="false",
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


# Migration 047: per-user recent ticket tracking. Powers the /run
# "Tickets" picker's "Recent tickets" list. A write fires whenever a
# user views a requirement detail, runs a single ticket, or selects
# one in a picker. Last 20 per (user, environment) are kept; older
# rows are pruned in the write path.
class UserRecentTicket(Base):
    __tablename__ = "user_recent_tickets"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     primary_key=True, nullable=False)
    environment_id = Column(Integer,
                            ForeignKey("environments.id", ondelete="CASCADE"),
                            primary_key=True, nullable=False)
    jira_key = Column(String(50), primary_key=True, nullable=False)
    jira_summary = Column(Text)
    viewed_at = Column(DateTime(timezone=True), nullable=False,
                       server_default=func.now())

    __table_args__ = (
        Index("idx_recent_tickets_viewed",
              "user_id", "environment_id", "viewed_at"),
    )


# Migration 039: ensure the permission-set model classes are registered
# with SQLAlchemy's declarative base whenever primeqa.core.models is
# imported, so the User.permission_set_assignments relationship can
# resolve its string target ("UserPermissionSet") during mapper
# configuration. Without this, worker + scheduler processes — which
# don't go through app.py's explicit permissions import — crash at
# first query with `NameError: name 'UserPermissionSet' is not defined`
# inside the relationship resolver.
#
# Kept at the BOTTOM of the file so User/Tenant/Environment are fully
# defined before permissions.py is parsed.
from primeqa.core import permissions as _permissions_models  # noqa: F401, E402
