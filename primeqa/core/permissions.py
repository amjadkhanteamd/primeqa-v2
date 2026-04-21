"""Permission Sets: data model, registry, seeding, resolution.

Foundation for the additive permission-set authorization architecture
documented in CLAUDE.md ("## Permission Model"):

  - Additive Permission Sets, union resolution, no deny rules.
  - Two-layer access: user permissions AND environment run policies.
  - Five Base sets: Developer / Tester / Release Owner / Admin / API Access.

Tables owned here: permission_sets, user_permission_sets,
shared_dashboard_links, notification_preferences.

Migration: 039_permission_sets_and_ownership.sql
"""

from __future__ import annotations

from typing import Iterable, Optional

from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Text,
    ForeignKey, UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship, Session
from sqlalchemy.sql import func

from primeqa.db import Base


# --------------------------------------------------------------------------
# SQLAlchemy models
# --------------------------------------------------------------------------

class PermissionSet(Base):
    """A named bundle of permission strings, scoped to a tenant.

    `is_system = True` rows are seeded by migration 039 and should not be
    edited or deleted by tenant admins. `is_base = True` marks the five
    canonical base sets (Developer / Tester / Release Owner / Admin /
    API Access). All other seeded system sets are single-permission
    granular sets for fine-grained assignment.
    """
    __tablename__ = "permission_sets"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name = Column(String(100), nullable=False)
    api_name = Column(String(100), nullable=False)
    description = Column(Text)
    is_system = Column(Boolean, nullable=False, server_default="false")
    is_base = Column(Boolean, nullable=False, server_default="false")
    permissions = Column(JSONB, nullable=False, server_default="[]")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(),
                        onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("tenant_id", "api_name", name="permission_sets_tenant_api_unique"),
        Index("idx_permission_sets_tenant", "tenant_id"),
    )


class UserPermissionSet(Base):
    """Many-to-many assignment: a user holds one or more permission sets.

    The user's effective permissions are the UNION of every set they hold
    (see `get_effective_permissions`). No deny rules — adding a set can
    only grant capabilities.
    """
    __tablename__ = "user_permission_sets"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    permission_set_id = Column(Integer, ForeignKey("permission_sets.id", ondelete="CASCADE"),
                               primary_key=True)
    assigned_by = Column(Integer, ForeignKey("users.id"))
    assigned_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class SharedDashboardLink(Base):
    """Tokenised share link for the release dashboard (read-only, expiring)."""
    __tablename__ = "shared_dashboard_links"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    environment_id = Column(Integer, ForeignKey("environments.id"))
    token = Column(String(64), nullable=False, unique=True)
    created_by = Column(Integer, ForeignKey("users.id"))
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class NotificationPreference(Base):
    """Per-user channel pref for a given event type (e.g. 'run_failed' -> 'email')."""
    __tablename__ = "notification_preferences"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(String(50), nullable=False)
    channel = Column(String(20), nullable=False, server_default="in_app")

    __table_args__ = (
        UniqueConstraint("user_id", "event_type", name="notification_preferences_user_event_unique"),
    )


# --------------------------------------------------------------------------
# Base permission sets — canonical definitions.
#
# Source of truth: the migration file seeds these verbatim. The Python
# copy below is used by `seed_permission_sets_for_tenant()` so new tenants
# get the same sets without running SQL.
# --------------------------------------------------------------------------

BASE_PERMISSION_SETS: tuple[dict, ...] = (
    {
        "api_name": "developer_base",
        "name": "Developer Base",
        "description": "Self-test individual Jira tickets against scratch org or team sandbox.",
        "permissions": [
            "connect_personal_org",
            "run_single_ticket",
            "view_own_results",
            "view_own_diagnosis",
            "rerun_own_ticket",
        ],
    },
    {
        "api_name": "tester_base",
        "name": "Tester Base",
        "description": "Sprint-level testing, test case review, suite management.",
        "permissions": [
            "connect_personal_org",
            "run_single_ticket",
            "view_own_results",
            "view_own_diagnosis",
            "rerun_own_ticket",
            "run_sprint",
            "run_suite",
            "view_all_results",
            "view_all_diagnosis",
            "view_intelligence_report",
            "review_test_cases",
            "manage_test_suites",
            "view_test_library",
            "view_coverage_map",
            "trigger_metadata_sync",
            "view_knowledge_attribution",
        ],
    },
    {
        "api_name": "release_owner_base",
        "name": "Release Owner Base",
        "description": "Release readiness assessment and stakeholder communication.",
        "permissions": [
            "view_dashboard",
            "view_suite_quality_gates",
            "view_all_results_summary",
            "view_intelligence_summary",
            "view_trends",
            "share_dashboard",
            "revoke_shared_links",
            "approve_release",
        ],
    },
    {
        "api_name": "admin_base",
        "name": "Admin Base",
        "description": "Platform configuration, user management, knowledge curation. "
                       "Includes all Tester and Release Owner permissions.",
        "permissions": [
            # Tester Base
            "connect_personal_org", "run_single_ticket", "view_own_results",
            "view_own_diagnosis", "rerun_own_ticket", "run_sprint", "run_suite",
            "view_all_results", "view_all_diagnosis", "view_intelligence_report",
            "review_test_cases", "manage_test_suites", "view_test_library",
            "view_coverage_map", "trigger_metadata_sync", "view_knowledge_attribution",
            # Release Owner Base
            "view_dashboard", "view_suite_quality_gates", "view_all_results_summary",
            "view_intelligence_summary", "view_trends", "share_dashboard",
            "revoke_shared_links", "approve_release",
            # Admin-only
            "manage_environments", "manage_jira_connections", "manage_sf_connections",
            "manage_ai_models", "manage_users", "manage_permission_sets",
            "manage_knowledge", "manage_skills", "view_audit_log", "view_api_usage",
            "configure_scheduled_runs", "manage_rate_limits", "override_quality_gate",
            "view_all_personal_environments", "delete_any_personal_environment",
        ],
    },
    {
        "api_name": "api_access",
        "name": "API Access",
        "description": "CI/CD pipelines, headless execution. Token-based authentication.",
        "permissions": [
            "api_authenticate",
            "run_single_ticket",
            "run_suite",
            "view_all_results",
            "trigger_metadata_sync",
            "webhook_notifications",
        ],
    },
)


# Human-readable labels + descriptions for every unique permission string.
# Used to seed the "granular" single-permission sets. Kept in sync with
# the migration's VALUES list — if you add a permission here, add it to
# the migration's granular seed too.
GRANULAR_PERMISSION_META: dict[str, tuple[str, str]] = {
    "connect_personal_org":           ("Connect Personal Org",           "Connect a personal Salesforce org for individual testing."),
    "run_single_ticket":              ("Run Single Ticket",              "Trigger a run scoped to a single Jira ticket."),
    "view_own_results":               ("View Own Results",               "View pipeline-run results you triggered."),
    "view_own_diagnosis":             ("View Own Diagnosis",             "View AI failure diagnosis on your own runs."),
    "rerun_own_ticket":               ("Rerun Own Ticket",               "Rerun a previous single-ticket run you triggered."),
    "run_sprint":                     ("Run Sprint",                     "Trigger a sprint-scoped test run."),
    "run_suite":                      ("Run Suite",                      "Trigger a suite test run."),
    "view_all_results":               ("View All Results",               "View pipeline-run results from any user."),
    "view_all_diagnosis":             ("View All Diagnosis",             "View AI failure diagnosis across all runs."),
    "view_intelligence_report":       ("View Intelligence Report",       "View detailed risk + coverage intelligence reports."),
    "review_test_cases":              ("Review Test Cases",              "Review AI-generated test cases in the BA queue."),
    "manage_test_suites":             ("Manage Test Suites",             "Create, edit, and delete test suites."),
    "view_test_library":              ("View Test Library",              "Browse the shared test case library."),
    "view_coverage_map":              ("View Coverage Map",              "View the coverage map across metadata objects."),
    "trigger_metadata_sync":          ("Trigger Metadata Sync",          "Kick off a Salesforce metadata refresh."),
    "view_knowledge_attribution":     ("View Knowledge Attribution",     "See which knowledge rules influenced an AI output."),
    "view_dashboard":                 ("View Dashboard",                 "View the release intelligence dashboard."),
    "view_suite_quality_gates":       ("View Suite Quality Gates",       "View suite-level GO/NO-GO quality gates."),
    "view_all_results_summary":       ("View All Results Summary",       "View cross-run aggregate result summaries."),
    "view_intelligence_summary":      ("View Intelligence Summary",      "View the top-line intelligence summary."),
    "view_trends":                    ("View Trends",                    "View quality + cost trend charts over time."),
    "share_dashboard":                ("Share Dashboard",                "Create tokenised shared dashboard links."),
    "revoke_shared_links":            ("Revoke Shared Links",            "Revoke previously issued shared dashboard links."),
    "approve_release":                ("Approve Release",                "Approve a release candidate (PENDING -> APPROVED)."),
    "manage_environments":            ("Manage Environments",            "Create, edit, delete test environments."),
    "manage_jira_connections":        ("Manage Jira Connections",        "Create, edit, delete Jira connections."),
    "manage_sf_connections":          ("Manage Salesforce Connections",  "Create, edit, delete Salesforce connections."),
    "manage_ai_models":               ("Manage AI Models",               "Configure LLM routing + model overrides."),
    "manage_users":                   ("Manage Users",                   "Create, edit, deactivate user accounts."),
    "manage_permission_sets":         ("Manage Permission Sets",         "Create, edit, assign permission sets."),
    "manage_knowledge":               ("Manage Knowledge",               "Curate the knowledge base used by AI prompts."),
    "manage_skills":                  ("Manage Skills",                  "Curate the skills registry."),
    "view_audit_log":                 ("View Audit Log",                 "View the tenant activity / audit log."),
    "view_api_usage":                 ("View API Usage",                 "View API + LLM usage dashboards."),
    "configure_scheduled_runs":       ("Configure Scheduled Runs",       "Create, edit, delete scheduled runs."),
    "manage_rate_limits":             ("Manage Rate Limits",             "Edit tenant-level LLM rate limits + tiers."),
    "override_quality_gate":          ("Override Quality Gate",          "Override a failing quality gate (OVERRIDDEN state)."),
    "view_all_personal_environments": ("View All Personal Environments", "View personal environments owned by any user."),
    "delete_any_personal_environment":("Delete Any Personal Environment","Delete personal environments owned by any user."),
    "api_authenticate":               ("API Authenticate",               "Authenticate via programmatic API token."),
    "webhook_notifications":          ("Webhook Notifications",          "Receive webhook notifications."),
}


def all_known_permissions() -> set[str]:
    """Every permission string known to the system.

    Derived from the union of all base-set permissions + GRANULAR_PERMISSION_META.
    Useful for UI dropdowns and validation.
    """
    result: set[str] = set(GRANULAR_PERMISSION_META.keys())
    for base in BASE_PERMISSION_SETS:
        result.update(base["permissions"])
    return result


# --------------------------------------------------------------------------
# Seeding
# --------------------------------------------------------------------------

def seed_permission_sets_for_tenant(tenant_id: int, db: Session) -> int:
    """Seed the five base + all granular system permission sets for a tenant.

    Idempotent: rows with matching (tenant_id, api_name) are left untouched.
    Returns the number of rows inserted in this call.

    Call this from the tenant-creation path so new tenants get the standard
    system sets. Existing tenants are seeded by migration 039.
    """
    inserted = 0

    # Base sets
    for spec in BASE_PERMISSION_SETS:
        existing = (db.query(PermissionSet)
                    .filter_by(tenant_id=tenant_id, api_name=spec["api_name"])
                    .first())
        if existing:
            continue
        db.add(PermissionSet(
            tenant_id=tenant_id,
            name=spec["name"],
            api_name=spec["api_name"],
            description=spec["description"],
            is_system=True,
            is_base=True,
            permissions=list(spec["permissions"]),
        ))
        inserted += 1

    # Granular single-permission sets
    for perm, (label, description) in GRANULAR_PERMISSION_META.items():
        existing = (db.query(PermissionSet)
                    .filter_by(tenant_id=tenant_id, api_name=perm)
                    .first())
        if existing:
            continue
        db.add(PermissionSet(
            tenant_id=tenant_id,
            name=label,
            api_name=perm,
            description=description,
            is_system=True,
            is_base=False,
            permissions=[perm],
        ))
        inserted += 1

    db.flush()
    return inserted


# Mapping from the legacy `users.role` column to the base Permission Set
# api_name that should be assigned as the default on migration.
#
#   - admin / superadmin -> admin_base
#   - ba                 -> tester_base
#   - viewer             -> release_owner_base
#   - tester (and any other role)  -> developer_base
_DEFAULT_SET_FOR_ROLE: dict[str, str] = {
    "admin":      "admin_base",
    "superadmin": "admin_base",
    "ba":         "tester_base",
    "viewer":     "release_owner_base",
    "tester":     "developer_base",
}


def default_permission_set_for_role(role: Optional[str]) -> str:
    """Return the api_name of the default base set for a legacy role."""
    if not role:
        return "developer_base"
    return _DEFAULT_SET_FOR_ROLE.get(role, "developer_base")


def assign_default_permission_set(user_id: int, tenant_id: int, role: Optional[str],
                                  db: Session, *, assigned_by: Optional[int] = None) -> bool:
    """Assign the default base set for `role` to `user_id`.

    Returns True if a new assignment was created, False if the user already
    held the set.
    """
    api_name = default_permission_set_for_role(role)
    ps = (db.query(PermissionSet)
          .filter_by(tenant_id=tenant_id, api_name=api_name)
          .first())
    if ps is None:
        # Tenant not seeded yet — seed on demand.
        seed_permission_sets_for_tenant(tenant_id, db)
        ps = (db.query(PermissionSet)
              .filter_by(tenant_id=tenant_id, api_name=api_name)
              .first())
        if ps is None:
            return False

    existing = (db.query(UserPermissionSet)
                .filter_by(user_id=user_id, permission_set_id=ps.id)
                .first())
    if existing:
        return False

    db.add(UserPermissionSet(
        user_id=user_id,
        permission_set_id=ps.id,
        assigned_by=assigned_by,
    ))
    db.flush()
    return True


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------

def get_effective_permissions(user_id: int, db: Session) -> set[str]:
    """Return the union of permissions across every set assigned to `user_id`.

    Empty set if the user has no assignments. Never returns None.
    """
    assignments = (db.query(UserPermissionSet)
                   .filter_by(user_id=user_id)
                   .all())
    pset_ids = [a.permission_set_id for a in assignments]
    if not pset_ids:
        return set()

    psets = (db.query(PermissionSet)
             .filter(PermissionSet.id.in_(pset_ids))
             .all())
    permissions: set[str] = set()
    for ps in psets:
        if ps.permissions:
            permissions.update(ps.permissions)
    return permissions


def user_has_permission(user_id: int, permission: str, db: Session) -> bool:
    """Convenience predicate: does the user hold `permission` via any set?"""
    return permission in get_effective_permissions(user_id, db)


def assign_permission_set(user_id: int, permission_set_id: int, db: Session,
                          *, assigned_by: Optional[int] = None) -> bool:
    """Grant a specific permission set to a user. Idempotent.

    Returns True if a new row was created, False if the user already held it.
    """
    existing = (db.query(UserPermissionSet)
                .filter_by(user_id=user_id, permission_set_id=permission_set_id)
                .first())
    if existing:
        return False
    db.add(UserPermissionSet(
        user_id=user_id,
        permission_set_id=permission_set_id,
        assigned_by=assigned_by,
    ))
    db.flush()
    return True


def revoke_permission_set(user_id: int, permission_set_id: int, db: Session) -> bool:
    """Remove a permission-set assignment. Idempotent."""
    existing = (db.query(UserPermissionSet)
                .filter_by(user_id=user_id, permission_set_id=permission_set_id)
                .first())
    if not existing:
        return False
    db.delete(existing)
    db.flush()
    return True


def list_user_permission_sets(user_id: int, db: Session) -> list[PermissionSet]:
    """Return the PermissionSet rows currently assigned to `user_id`."""
    assignments = (db.query(UserPermissionSet)
                   .filter_by(user_id=user_id)
                   .all())
    pset_ids = [a.permission_set_id for a in assignments]
    if not pset_ids:
        return []
    return (db.query(PermissionSet)
            .filter(PermissionSet.id.in_(pset_ids))
            .all())


__all__ = [
    # Models
    "PermissionSet", "UserPermissionSet", "SharedDashboardLink", "NotificationPreference",
    # Constants
    "BASE_PERMISSION_SETS", "GRANULAR_PERMISSION_META",
    # Functions
    "all_known_permissions",
    "seed_permission_sets_for_tenant",
    "default_permission_set_for_role",
    "assign_default_permission_set",
    "get_effective_permissions",
    "user_has_permission",
    "assign_permission_set",
    "revoke_permission_set",
    "list_user_permission_sets",
]
