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
    # Step 6a (LLD_STEP_6A §e): the work surfaces are READ by a viewer and ACTED
    # on by a member. Presentation only — the route decorators stay the
    # enforcement. Before 6a the Requirements nav slot was gated on
    # run_single_ticket (MEMBER), so a viewer saw no Requirements item at all.
    "view_requirements": Tier.VIEWER,
    "view_results": Tier.VIEWER,
    "view_releases": Tier.VIEWER,
    "manage_requirements": Tier.MEMBER,
    "manage_results": Tier.MEMBER,
    "manage_releases": Tier.MEMBER,
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
    # revoke matches share + the route gate (@require_tier_api(Tier.MEMBER)) — the
    # shim previously hid the revoke control at ADMIN while the API admitted
    # Member, a UI/API mismatch (D-245 review fix).
    "revoke_shared_links": Tier.MEMBER,
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

        # Step 6a: the badge rides the REQUIREMENTS item and counts everything
        # waiting for a person — draft claims + pending human reviews (§a).
        if any(i["id"] == "requirements" for i in sidebar):
            counts = pending_human_attention(user["tenant_id"])
            if counts["total"]:
                for i in sidebar:
                    if i["id"] == "requirements":
                        i["badge"] = counts["total"]
                        i["badge_detail"] = counts

        return {
            "user_permissions": perms,
            "has_permission": (lambda p: is_superadmin or p in perms),
            "has_any_permission": (
                lambda *ps: is_superadmin or any(p in perms for p in ps)
            ),
            "can_see_settings": can_see_settings,
            "sidebar_items": sidebar,
        }


def pending_human_attention(tenant_id: int) -> dict:
    """Step 6a (§a) — the Requirements badge: everything waiting for a PERSON.

    ``{drafts, human_reviews, total}``. Two halves, summed because both say the
    same thing to a human, and each is shown separately on the Needs review tab
    so the number is never a mystery:

      - ``drafts``        draft claims awaiting approval (tenant-wide) — the
                          count the My Reviews badge carried before 6a,
                          unchanged.
      - ``human_reviews`` the STEP 5 human_reviews axis, reused rather than
                          redefined: NEEDS_HUMAN verdicts on the latest
                          processing run of the ACTIVE claim set, plus
                          HUMAN_REVIEW members of that set, MINUS any item an
                          active waiver covers.

    Best-effort in both halves: a failing read contributes 0 and the badge
    never takes a page down."""
    from sqlalchemy import text

    from primeqa.semantic.connection import get_tenant_connection
    out = {"drafts": 0, "human_reviews": 0, "total": 0}
    try:
        with get_tenant_connection(tenant_id) as conn:
            out["drafts"] = conn.execute(text(
                "SELECT COUNT(*) FROM test_claims "
                "WHERE status = 'draft' AND valid_to IS NULL"
            )).scalar() or 0
            try:
                out["human_reviews"] = _pending_human_reviews(conn, tenant_id)
            except Exception:                       # noqa: BLE001 — half a badge beats none
                out["human_reviews"] = 0
    except Exception:                               # noqa: BLE001
        return out
    out["total"] = out["drafts"] + out["human_reviews"]
    return out


def _pending_human_reviews(conn, tenant_id: int) -> int:
    """NEEDS_HUMAN verdicts on the latest processing run of the ACTIVE claim set
    + HUMAN_REVIEW members of that set, minus what an active waiver covers.
    The same definition the Step 5 evidence assembler grades on."""
    from sqlalchemy import text
    row = conn.execute(text("""
        SELECT CAST(cs.id AS text) FROM claim_sets cs
        WHERE cs.status = 'approved'
          AND cs.inventory_version = (SELECT MAX(inventory_version) FROM claim_sets
                                      WHERE status = 'approved')
        ORDER BY cs.created_at DESC LIMIT 1
    """)).first()
    if row is None:
        return 0
    claim_set = row[0]
    pending = {r[0] for r in conn.execute(text("""
        SELECT CAST(v.test_id AS text) FROM s6_ui_verdicts v
        WHERE v.verdict = 'NEEDS_HUMAN' AND v.job_id = (
            SELECT job_id FROM s6_ui_processing_runs
            WHERE claim_set_id = CAST(:cs AS uuid)
            ORDER BY processed_at DESC LIMIT 1)
    """), {"cs": claim_set}).fetchall()}
    pending |= {r[0] for r in conn.execute(text("""
        SELECT CAST(test_id AS text) FROM claim_set_members
        WHERE claim_set_id = CAST(:cs AS uuid) AND applicability = 'HUMAN_REVIEW'
          AND revoked_at IS NULL
    """), {"cs": claim_set}).fetchall()}
    if not pending:
        return 0
    try:                                            # the waiver table exists from Step 5
        waived = {r[0] for r in conn.execute(text("""
            SELECT item_ref FROM quality_waivers
            WHERE axis IN ('human_reviews', 'conformance') AND item_kind = 'claim'
              AND revoked_at IS NULL AND expires_at > now()
        """)).fetchall()}
    except Exception:                               # noqa: BLE001
        waived = set()
    return len(pending - waived)


__all__ = [
    "SharedDashboardLink",
    "NotificationPreference",
    "register_template_context",
    "pending_human_attention",
]
