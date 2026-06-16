"""Role-derived UI capabilities + the two surviving models (D-245, Phase 5).

The additive permission-set authorization layer (``permission_sets`` /
``user_permission_sets``, the union resolution, the ``require_permission`` family,
``check_environment_policy``) was **deleted** in D-245: authorization is now the
role ladder (``primeqa/core/authz.py``) × environment scope (Groups). The tables
were dropped in migration 057.

What remains here:
  - ``SharedDashboardLink`` / ``NotificationPreference`` — two unrelated feature
    models that happened to live in this module; kept verbatim.
  - A small **role-derived** capability map so the existing nav
    (``navigation.build_sidebar``) and a handful of ``{% if has_permission(...) %}``
    templates keep working — each capability is now implied by the caller's role
    **tier**, not stored in DB permission sets. This is a presentation helper;
    the real authorization is the route-level role gates.
"""

from __future__ import annotations

from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, ForeignKey, UniqueConstraint,
)
from sqlalchemy.sql import func

from primeqa.db import Base
from primeqa.core.authz import Tier, rank


# --------------------------------------------------------------------------
# Surviving feature models (NOT part of the deleted permission-set layer).
# --------------------------------------------------------------------------

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
# Role-derived UI capabilities (presentation only — replaces the deleted
# DB permission-set resolution). Each capability is unlocked at a min tier;
# the navigation + the few `has_permission(...)` templates consume the set.
# Superadmin implicitly holds every capability (god-mode).
# --------------------------------------------------------------------------

_CAPABILITY_MIN_TIER: dict[str, Tier] = {
    # Viewer — read surfaces
    "view_dashboard": Tier.VIEWER,
    "view_intelligence_report": Tier.VIEWER,
    "view_test_library": Tier.VIEWER,
    "view_coverage_map": Tier.VIEWER,
    "view_suite_quality_gates": Tier.VIEWER,
    # Member — author / run / triage / review
    "connect_personal_org": Tier.MEMBER,
    "run_single_ticket": Tier.MEMBER,
    "run_sprint": Tier.MEMBER,
    "run_suite": Tier.MEMBER,
    "rerun_own_ticket": Tier.MEMBER,
    "view_own_results": Tier.MEMBER,
    "view_all_results": Tier.MEMBER,
    "review_test_cases": Tier.MEMBER,
    "manage_test_suites": Tier.MEMBER,
    "share_dashboard": Tier.MEMBER,
    "approve_release": Tier.MEMBER,
    "override_quality_gate": Tier.MEMBER,
    "manage_knowledge": Tier.MEMBER,
    # Admin — tenant administration
    "manage_users": Tier.ADMIN,
    "manage_environments": Tier.ADMIN,
    "manage_sf_connections": Tier.ADMIN,
    "manage_jira_connections": Tier.ADMIN,
    "manage_groups": Tier.ADMIN,
    "trigger_metadata_sync": Tier.ADMIN,
    "view_audit_log": Tier.ADMIN,
    "revoke_shared_links": Tier.ADMIN,
}


def _role_capabilities(role) -> set[str]:
    """The set of UI capabilities the caller's role tier unlocks. Superadmin
    holds all; otherwise a capability is held iff ``rank(role) >= its min tier``."""
    tier = rank(role)
    if tier >= Tier.SUPERADMIN:
        return set(_CAPABILITY_MIN_TIER)
    return {cap for cap, min_t in _CAPABILITY_MIN_TIER.items() if tier >= min_t}


# --------------------------------------------------------------------------
# Jinja context processor — feeds the nav + `has_permission(...)` templates
# from the role-derived capability set.
# --------------------------------------------------------------------------

def register_template_context(app) -> None:
    """Wire ``user_permissions``, ``has_permission()`` / ``has_any_permission()``,
    ``can_see_settings`` and ``sidebar_items`` into every template — now sourced
    from the caller's role (``_role_capabilities``), not DB permission sets."""
    @app.context_processor
    def inject_permissions():  # noqa: F811
        from flask import request
        from primeqa.core.navigation import build_sidebar

        user = getattr(request, "user", None)
        if not user:
            return {
                "user_permissions": set(),
                "has_permission": (lambda _p: False),
                "has_any_permission": (lambda *_ps: False),
                "can_see_settings": False,
                "sidebar_items": [],
            }
        perms = _role_capabilities(user.get("role"))
        is_superadmin = user.get("role") == "superadmin"
        can_see_settings = is_superadmin or any(p.startswith("manage_") for p in perms)
        sidebar = build_sidebar(perms, request.path, is_superadmin=is_superadmin)

        # Badge count for the "My Reviews" nav item, only when it renders.
        if any(i["id"] == "my_reviews" for i in sidebar):
            count = _count_pending_reviews_for(user)
            if count:
                for i in sidebar:
                    if i["id"] == "my_reviews":
                        i["badge"] = count

        return {
            "user_permissions": perms,
            "has_permission": (lambda p: is_superadmin or p in perms),
            "has_any_permission": (
                lambda *ps: is_superadmin or any(p in perms for p in ps)
            ),
            "can_see_settings": can_see_settings,
            "sidebar_items": sidebar,
        }


def _count_pending_reviews_for(user: dict) -> int:
    """The My Reviews nav badge: draft claims awaiting approval (tenant-wide)."""
    try:
        from sqlalchemy import text

        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(user["tenant_id"]) as conn:
            return conn.execute(text(
                "SELECT COUNT(*) FROM test_claims "
                "WHERE status = 'draft' AND valid_to IS NULL"
            )).scalar() or 0
    except Exception:
        return 0


__all__ = [
    "SharedDashboardLink",
    "NotificationPreference",
    "register_template_context",
]
