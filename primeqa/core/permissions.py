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

from functools import wraps
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


# --------------------------------------------------------------------------
# Enforcement layer: decorators for Flask routes + env policy check.
#
# Context: PrimeQA uses JWT (not Flask-Login). `primeqa.core.auth.require_auth`
# runs first, decodes the JWT, and sets `request.user` = {id, tenant_id,
# email, role, full_name}. The decorators below assume `request.user` is
# already populated. Stack them AFTER @require_auth (or the web-side
# `primeqa.views.login_required`).
#
# Design:
#   - `require_permission(*perms, require_all=True)` is the additive-set
#     check. Reads effective permissions from the permission-set union;
#     superadmin bypasses (mirrors existing require_role semantics).
#   - `check_environment_policy(env_id, action, db)` is a pure function.
#     It is the environment half of the two-layer access model
#     (user permissions AND env run policies).
#   - `require_run_permission(action)` composes both layers for execution
#     routes. Extracts env_id from request body or URL.
#   - Effective permissions are cached on `flask.g.effective_permissions`
#     per request so stacked decorators don't hit the DB repeatedly.
# --------------------------------------------------------------------------

def _is_api_request() -> bool:
    """True when this is an /api/* request (vs. a page view)."""
    from flask import request
    return (request.path or "").startswith("/api/")


def _resolve_effective_permissions() -> set[str]:
    """Return request-scoped cached permissions for the current user.

    Computed once per request and memoised on `flask.g`. Returns empty set
    for anonymous / misconfigured requests (the caller's auth decorator
    should already have failed with 401 in that case).
    """
    from flask import g, request
    cached = getattr(g, "effective_permissions", None)
    if cached is not None:
        return cached

    user = getattr(request, "user", None)
    if not user or "id" not in user:
        g.effective_permissions = set()
        return g.effective_permissions

    from primeqa.db import SessionLocal
    db = SessionLocal()
    try:
        perms = get_effective_permissions(int(user["id"]), db)
    finally:
        db.close()

    g.effective_permissions = perms
    return perms


def _denied_response(required: tuple[str, ...], require_all: bool):
    """Render a 403 response appropriate to API vs page context."""
    from flask import flash, redirect
    from primeqa.shared.api import json_error

    # Log at WARNING so ops can see who's bouncing off what.
    import logging
    from flask import request
    log = logging.getLogger("primeqa.permissions")
    user = getattr(request, "user", None) or {}
    log.warning(
        "permission_denied user=%s route=%s required=%s mode=%s effective=%s",
        user.get("id"),
        request.path,
        list(required),
        "all" if require_all else "any",
        sorted(getattr(__import__("flask").g, "effective_permissions", set())),
    )

    if _is_api_request():
        return json_error(
            "INSUFFICIENT_PERMISSIONS",
            "You do not have permission to perform this action.",
            http=403,
            details={
                "required": list(required),
                "mode": "all" if require_all else "any",
            },
        )
    # Web page — flash + redirect to dashboard. Generic 403 is a dead end;
    # the user at least lands somewhere useful.
    try:
        flash("You don't have permission to view that page.", "error")
    except Exception:
        pass
    return redirect("/")


def require_permission(*required_permissions: str, require_all: bool = True):
    """Decorator: require that `request.user` holds the listed permissions.

    Args:
        *required_permissions: permission strings (e.g. 'manage_environments')
        require_all: if True (default), user must hold ALL listed permissions;
                     if False, holding ANY ONE is enough.

    Superadmin bypass applies — god-mode passes every permission check
    (mirrors `primeqa.core.auth.require_role`). Effective permissions are
    cached on `flask.g` so stacked decorators share the same DB hit.

    Must be applied AFTER `@require_auth` / `login_required` in the chain.

    Usage:
        @core_bp.route('/api/users', methods=['GET'])
        @require_auth
        @require_permission('manage_users')
        def list_users(): ...

        @core_bp.route('/api/runs/<int:run_id>', methods=['GET'])
        @require_auth
        @require_permission('view_own_results', 'view_all_results', require_all=False)
        def get_run(run_id): ...
    """
    if not required_permissions:
        raise ValueError("require_permission expects at least one permission string")

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            from flask import request
            user = getattr(request, "user", None) or {}
            # Superadmin god-mode — consistent with require_role semantics.
            if user.get("role") == "superadmin":
                return f(*args, **kwargs)

            effective = _resolve_effective_permissions()
            required = set(required_permissions)
            if require_all:
                ok = required.issubset(effective)
            else:
                ok = bool(required & effective)
            if not ok:
                return _denied_response(required_permissions, require_all)
            return f(*args, **kwargs)

        return wrapper

    return decorator


# --------------------------------------------------------------------------
# Environment policy check — the second layer of access control.
# --------------------------------------------------------------------------

# Action -> env column mapping.
_ENV_POLICY_COLUMN: dict[str, str] = {
    "single_run":    "allow_single_run",
    "bulk_run":      "allow_bulk_run",
    "scheduled_run": "allow_scheduled_run",
}

# Action -> required user permission (layer 1 half of the combined decorator).
_ACTION_PERMISSION: dict[str, str] = {
    "single_run":    "run_single_ticket",
    "bulk_run":      "run_sprint",
    "scheduled_run": "configure_scheduled_runs",
}


def check_environment_policy(environment_id: int, action: str, db: Session,
                              *, confirm_production: bool = False
                              ) -> tuple[bool, str]:
    """Check whether the env's run policy permits `action`.

    Args:
        environment_id: the environment to evaluate
        action: 'single_run' | 'bulk_run' | 'scheduled_run'
        db: SQLAlchemy session
        confirm_production: set to True when the caller has explicitly
            confirmed running against a production env (e.g. passed
            `confirm_production=true` in the request body).

    Returns:
        (allowed, reason). `reason` is empty when allowed and
        descriptive when denied.

    Side-effect: if the env has `max_api_calls_per_run` set, the limit is
    stashed on `flask.g.env_api_call_limit` so the executor can enforce
    it during run execution (decorator does not block here).
    """
    from primeqa.core.models import Environment

    col = _ENV_POLICY_COLUMN.get(action)
    if col is None:
        return False, f"Unknown action {action!r}"

    env = db.query(Environment).filter_by(id=environment_id).first()
    if env is None:
        return False, f"Environment {environment_id} not found"

    # Policy flag check
    if not getattr(env, col, False):
        return False, (
            f"Environment '{env.name}' does not allow {action.replace('_', ' ')}. "
            f"Update the env's run policy to enable this action."
        )

    # Production confirmation gate. is_production = true requires an
    # explicit confirmation token from the caller — prevents accidental
    # prod runs from a stray curl.
    if getattr(env, "is_production", False) and not confirm_production:
        return False, (
            "Production org confirmation required. "
            "Set confirm_production=true in the request body to proceed."
        )

    # Stash the executor hint on flask.g. Non-fatal if no app context.
    try:
        from flask import g
        if env.max_api_calls_per_run is not None:
            g.env_api_call_limit = env.max_api_calls_per_run
    except Exception:
        pass

    return True, ""


def _extract_environment_id() -> Optional[int]:
    """Pull environment_id from request body / query / URL args.

    Order: JSON body → form field → query string → URL rule kwarg.
    Returns None if not found or not coercible to int.
    """
    from flask import request

    candidates = []
    if request.is_json:
        body = request.get_json(silent=True) or {}
        candidates.append(body.get("environment_id"))
        candidates.append(body.get("env_id"))
    if request.form:
        candidates.append(request.form.get("environment_id"))
        candidates.append(request.form.get("env_id"))
    candidates.append(request.args.get("environment_id"))
    candidates.append(request.args.get("env_id"))
    view_args = request.view_args or {}
    candidates.append(view_args.get("environment_id"))
    candidates.append(view_args.get("env_id"))

    for c in candidates:
        if c is None:
            continue
        try:
            return int(c)
        except (TypeError, ValueError):
            continue
    return None


def _extract_confirm_production() -> bool:
    """True if the caller sent confirm_production=true in body or query."""
    from flask import request
    truthy = {"1", "true", "yes", "on", True}

    if request.is_json:
        body = request.get_json(silent=True) or {}
        if body.get("confirm_production") in truthy:
            return True
    if request.form and request.form.get("confirm_production", "").lower() in {
        "1", "true", "yes", "on"
    }:
        return True
    if request.args.get("confirm_production", "").lower() in {
        "1", "true", "yes", "on"
    }:
        return True
    return False


def require_run_permission(action: str):
    """Composite decorator: user permission AND env run policy.

    Use on every route that triggers test execution.

    Layer 1 — user permissions:
        single_run    -> 'run_single_ticket'
        bulk_run      -> 'run_sprint'
        scheduled_run -> 'configure_scheduled_runs'

    Layer 2 — environment run policy:
        single_run    -> env.allow_single_run
        bulk_run      -> env.allow_bulk_run
        scheduled_run -> env.allow_scheduled_run
        + is_production -> confirm_production flag required

    Both layers must pass. Denial returns 403 with the failing layer
    explicitly called out so the client knows whether to re-auth, ask for
    elevated permissions, or flip an env policy.
    """
    if action not in _ENV_POLICY_COLUMN:
        raise ValueError(f"Unknown run action: {action!r}")

    perm = _ACTION_PERMISSION[action]

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            from flask import request
            from primeqa.shared.api import json_error

            user = getattr(request, "user", None) or {}

            # Layer 1: user permission check (superadmin bypass inside).
            if user.get("role") != "superadmin":
                effective = _resolve_effective_permissions()
                if perm not in effective:
                    return _denied_response((perm,), require_all=True)

            # Layer 2: env policy. env_id may come from body/args/URL.
            env_id = kwargs.get("environment_id") or _extract_environment_id()
            if env_id is None:
                return json_error(
                    "MISSING_ENVIRONMENT",
                    "environment_id is required for this action.",
                    http=400,
                )

            from primeqa.db import SessionLocal
            db = SessionLocal()
            try:
                allowed, reason = check_environment_policy(
                    env_id, action, db,
                    confirm_production=_extract_confirm_production(),
                )
            finally:
                db.close()

            if not allowed:
                # Log the env-policy denial separately so ops can see it
                # in the audit trail without silent fallthrough.
                import logging
                logging.getLogger("primeqa.permissions").warning(
                    "env_policy_denied user=%s env=%s action=%s reason=%s",
                    user.get("id"), env_id, action, reason,
                )
                return json_error(
                    "ENVIRONMENT_POLICY_DENIED",
                    reason,
                    http=403,
                    details={"environment_id": env_id, "action": action},
                )

            return f(*args, **kwargs)

        return wrapper

    return decorator


# --------------------------------------------------------------------------
# Result scoping: the data half of the access model. Query-layer filters
# that mirror what require_permission lets through at the route layer.
# --------------------------------------------------------------------------

def get_scoped_results_query(user: dict, base_query, *, triggered_by_col=None):
    """Apply caller-appropriate scoping to a run-results query.

    - `view_all_results` in the user's effective perms → no filter
      (full visibility across the tenant's runs).
    - else `view_own_results` → filter by triggered_by_user_id = user.id.
    - else → no rows.

    `triggered_by_col` is the SQLAlchemy column to filter on (defaults to
    PipelineRun.triggered_by). Pass a different column if scoping a
    derived table (e.g. RunTestResult via a join).

    This is the query-layer enforcement that matches the route-layer
    check `@require_permission('view_own_results', 'view_all_results',
    require_all=False)`. Call this after the route check — a user with
    only `view_own_results` passes the route gate but their query must
    still be scoped to their own rows.

    Returns a filtered SQLAlchemy query.
    """
    if triggered_by_col is None:
        from primeqa.execution.models import PipelineRun
        triggered_by_col = PipelineRun.triggered_by

    # Superadmin / user with the view_all scope — no filter.
    if user.get("role") == "superadmin":
        return base_query

    perms = _resolve_effective_permissions() if getattr(__import__("flask").request, "user", None) else set()
    if not perms and user.get("id"):
        # Outside request context (e.g. worker): resolve directly.
        from primeqa.db import SessionLocal
        db = SessionLocal()
        try:
            perms = get_effective_permissions(int(user["id"]), db)
        finally:
            db.close()

    if "view_all_results" in perms:
        return base_query
    if "view_own_results" in perms:
        return base_query.filter(triggered_by_col == int(user["id"]))
    # No visibility at all.
    return base_query.filter(False)


def should_redact_step_detail(user: dict) -> bool:
    """True when the user gets a summary-only view (no step-level detail).

    Release Owners see aggregate results but not raw API/step payloads.
    When the user only has `view_all_results_summary` (typical for
    release_owner_base) and NOT `view_all_diagnosis` or
    `view_own_diagnosis`, hide step-level detail.
    """
    if user.get("role") == "superadmin":
        return False
    perms = _resolve_effective_permissions() if getattr(__import__("flask").request, "user", None) else set()
    if not perms:
        return True  # safe default
    if "view_all_diagnosis" in perms or "view_own_diagnosis" in perms:
        return False
    return "view_all_results_summary" in perms


# --------------------------------------------------------------------------
# Page-level permission gating with redirect (vs. API-level 403).
# --------------------------------------------------------------------------

def require_page_permission(*required_permissions: str, require_all: bool = True):
    """Same semantics as `require_permission`, but for Jinja-rendered page
    routes: on denial, flash a message + redirect to the caller's
    landing page instead of returning JSON 403.

    Must be applied AFTER `primeqa.views.login_required` in the decorator
    chain so `request.user` is populated.

    Usage:
        @views_bp.route('/settings')
        @login_required
        @require_page_permission('manage_environments', 'manage_users',
                                  require_all=False)
        def settings_page(): ...
    """
    if not required_permissions:
        raise ValueError("require_page_permission expects at least one permission string")

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            from flask import flash, redirect, request
            user = getattr(request, "user", None) or {}
            # Superadmin god-mode.
            if user.get("role") == "superadmin":
                return f(*args, **kwargs)
            effective = _resolve_effective_permissions()
            required = set(required_permissions)
            ok = (required.issubset(effective) if require_all
                  else bool(required & effective))
            if ok:
                return f(*args, **kwargs)

            # Denied — flash + redirect to the user's landing page rather
            # than leave them on a 403 dead end.
            try:
                flash("You don't have permission to view that page.", "warning")
            except Exception:
                pass

            # Resolve landing page now that we already know effective perms.
            from primeqa.core.navigation import get_landing_page
            preferred = None
            try:
                from primeqa.db import SessionLocal
                from primeqa.core.models import User
                db = SessionLocal()
                try:
                    u = db.query(User).filter_by(id=int(user["id"])).first()
                    preferred = getattr(u, "preferred_landing_page", None)
                finally:
                    db.close()
            except Exception:
                pass

            target = get_landing_page(
                effective, preferred=preferred,
                is_superadmin=(user.get("role") == "superadmin"),
            )
            # Avoid a redirect loop: if the landing page IS the route we
            # just denied, fall back to "/" as the last-resort safety net.
            if target == request.path:
                target = "/"
            return redirect(target)

        return wrapper

    return decorator


# --------------------------------------------------------------------------
# Jinja context processor registration helper.
# --------------------------------------------------------------------------

def register_template_context(app) -> None:
    """Wire `user_permissions`, `has_permission()`, and `sidebar_items`
    into every template.

    Call from app.create_app() after blueprints are registered so
    templates can do `{% if has_permission('manage_users') %}…{% endif %}`
    and `{% for item in sidebar_items %}…{% endfor %}`.
    """
    @app.context_processor
    def inject_permissions():  # noqa: F811
        from flask import request
        from primeqa.core.navigation import build_sidebar

        user = getattr(request, "user", None)
        if not user:
            return {
                "user_permissions": set(),
                "has_permission": (lambda _p: False),
                "sidebar_items": [],
            }
        perms = _resolve_effective_permissions()
        is_superadmin = user.get("role") == "superadmin"
        can_see_settings = is_superadmin or any(p.startswith("manage_") for p in perms)
        sidebar = build_sidebar(perms, request.path, is_superadmin=is_superadmin)

        # Prompt 9: badge count for "My Reviews" nav item. Query is cheap
        # (single COUNT on an indexed tenant_id + status filter) and runs
        # only when the user actually sees the item.
        if any(i["id"] == "my_reviews" for i in sidebar):
            count = _count_pending_reviews_for(user)
            if count:
                for i in sidebar:
                    if i["id"] == "my_reviews":
                        i["badge"] = count

        return {
            "user_permissions": perms,
            "has_permission": (lambda p: p in perms or is_superadmin),
            "has_any_permission": (
                lambda *ps: is_superadmin or any(p in perms for p in ps)
            ),
            "can_see_settings": can_see_settings,
            "sidebar_items": sidebar,
        }


def _count_pending_reviews_for(user: dict) -> int:
    """Return the BA's pending-review count across the tenant.

    Counts reviews with status='pending' and deleted_at IS NULL. Scoped
    by tenant; does NOT filter by assigned_to since BAs often pick up
    each other's queue. The queue page itself has the "assigned to me"
    filter for per-user scoping.
    """
    try:
        from primeqa.db import SessionLocal
        from primeqa.test_management.models import BAReview
        db = SessionLocal()
        try:
            return (db.query(BAReview)
                    .filter(BAReview.tenant_id == user["tenant_id"],
                            BAReview.status == "pending",
                            BAReview.deleted_at.is_(None))
                    .count())
        finally:
            db.close()
    except Exception:
        # Never let a badge query break the entire render.
        return 0


__all__ = [
    # Models
    "PermissionSet", "UserPermissionSet", "SharedDashboardLink", "NotificationPreference",
    # Constants
    "BASE_PERMISSION_SETS", "GRANULAR_PERMISSION_META",
    # Resolution
    "all_known_permissions",
    "seed_permission_sets_for_tenant",
    "default_permission_set_for_role",
    "assign_default_permission_set",
    "get_effective_permissions",
    "user_has_permission",
    "assign_permission_set",
    "revoke_permission_set",
    "list_user_permission_sets",
    # Enforcement
    "require_permission",
    "require_page_permission",
    "check_environment_policy",
    "require_run_permission",
    "get_scoped_results_query",
    "should_redact_step_detail",
    "register_template_context",
]
