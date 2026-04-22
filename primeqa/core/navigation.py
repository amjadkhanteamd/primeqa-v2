"""Dynamic navigation + landing-page resolution driven by permission sets.

Every sidebar / top-nav item declares the permission(s) that unlock it.
`build_sidebar()` filters the static registry against the caller's
effective permissions so the rendered nav only ever shows what the user
can actually use. `get_landing_page()` answers "after login, where?"
based on the permission-set union — same source of truth as the UI.

URL conventions are adapted to PrimeQA's actual route space (the prompt
used aspirational URLs like /tickets, /run, /results — those don't exist
yet as standalone pages). Mapping:

    Prompt URL         PrimeQA URL     Notes
    ─────────────────  ──────────────  ──────────────────────────────
    /tickets           /requirements   The requirement = Jira ticket.
    /run               /runs/new       The Run Wizard.
    /results           /runs           Runs list = results.
    /dashboard         /               Root is the dashboard.
    /test-library      /test-cases     Library list.
    /my-reviews        /reviews        Review queue.
    /test-suites       /suites         Suite list.
    /settings          /settings       Matches.

Items that point at URLs which don't yet exist (e.g. Coverage Map,
Knowledge, Audit Log) are still declared here — they're gated by
permissions that no seeded role holds yet, so they don't render. When
those pages ship, the nav item appears automatically for granted users.
"""

from __future__ import annotations

from typing import Iterable, Optional


# --------------------------------------------------------------------------
# Registry
#
# Each entry declares exactly ONE of:
#   - permission                — single permission string
#   - permission_any            — list of permission strings; ANY grants it
#   - permission_any_prefix     — permission prefix; ANY matching permission
#                                 grants access (e.g. "manage_" matches every
#                                 manage_* permission)
#
# Ordering here is the render order. Section markers group related items.
# --------------------------------------------------------------------------

SIDEBAR_ITEMS: list[dict] = [
    # Primary — the things you do day-to-day
    {
        "id": "my_tickets",
        "label": "My Tickets",
        "icon": "ticket",
        "url": "/requirements",
        "permission": "run_single_ticket",
        "section": "primary",
    },
    {
        "id": "run_tests",
        "label": "Run Tests",
        "icon": "play",
        "url": "/run",
        "permission_any": ["run_sprint", "run_suite"],
        "section": "primary",
    },
    {
        "id": "results",
        "label": "Results",
        "icon": "chart",
        "url": "/results",
        # Keep the Results tab highlighted when the user follows a
        # link or redirect into /runs/* (the aliased canonical path).
        "active_also_for": ("/runs",),
        "permission_any": ["view_own_results", "view_all_results"],
        "section": "primary",
    },
    {
        "id": "my_reviews",
        "label": "My Reviews",
        "icon": "check-circle",
        "url": "/reviews",
        "permission": "review_test_cases",
        "section": "primary",
    },
    {
        "id": "dashboard",
        "label": "Dashboard",
        "icon": "dashboard",
        "url": "/",
        "permission": "view_dashboard",
        "section": "primary",
    },

    # Testing — the artefacts of the practice
    {
        "id": "test_library",
        "label": "Test Library",
        "icon": "library",
        "url": "/test-cases",
        "permission": "view_test_library",
        "section": "testing",
    },
    {
        "id": "test_suites",
        "label": "Test Suites",
        "icon": "folder",
        "url": "/suites",
        "permission_any": ["manage_test_suites", "view_suite_quality_gates"],
        "section": "testing",
    },
    {
        "id": "coverage",
        "label": "Coverage Map",
        "icon": "map",
        "url": "/coverage",
        "permission": "view_coverage_map",
        "section": "testing",
    },

    # Admin — tenant-wide config
    {
        "id": "knowledge",
        "label": "Knowledge",
        "icon": "brain",
        "url": "/knowledge",
        "permission": "manage_knowledge",
        "section": "admin",
    },
    {
        "id": "audit_log",
        "label": "Audit Log",
        "icon": "history",
        "url": "/audit-log",
        "permission": "view_audit_log",
        "section": "admin",
    },
    {
        "id": "settings",
        "label": "Settings",
        "icon": "settings",
        "url": "/settings",
        "permission_any_prefix": "manage_",
        "section": "admin",
    },
]


_SECTION_ORDER = {"primary": 0, "testing": 1, "admin": 2}


def _item_allowed(item: dict, user_permissions: set, is_superadmin: bool = False) -> bool:
    """True if the user's permission union unlocks this nav item."""
    if is_superadmin:
        return True
    if "permission" in item:
        return item["permission"] in user_permissions
    if "permission_any" in item:
        return any(p in user_permissions for p in item["permission_any"])
    if "permission_any_prefix" in item:
        prefix = item["permission_any_prefix"]
        return any(p.startswith(prefix) for p in user_permissions)
    # Item without a gate — defensive default: do NOT show.
    return False


def build_sidebar(user_permissions: set, current_path: str = "/",
                  *, is_superadmin: bool = False) -> list[dict]:
    """Build the visible sidebar for a caller.

    Returns a list of item dicts (in render order) with these extra keys:
      - active (bool): whether the item's url matches current_path
      - section_first (bool): first item of its section (true → render a
        divider above it, except when it's the very first item in the list)

    Rules:
      - Only items whose permission gate the user passes are included.
      - Section dividers only make sense if ≥2 sections are populated;
        callers that want to render dividers can inspect section_first.
      - Active match is longest-prefix: e.g. on /runs/42 the Results
        item (url=/runs) is marked active.
    """
    visible = [dict(item) for item in SIDEBAR_ITEMS
               if _item_allowed(item, user_permissions, is_superadmin=is_superadmin)]

    # Sort by section then original order (stable).
    visible.sort(key=lambda it: (_SECTION_ORDER.get(it["section"], 99),
                                 SIDEBAR_ITEMS.index(
                                     next(x for x in SIDEBAR_ITEMS if x["id"] == it["id"]))))

    # Active marking — longest url match wins so /runs/42 highlights Results
    # (url=/runs) rather than Dashboard (url=/).
    #
    # Items can additionally declare `active_also_for`: a tuple of URL
    # prefixes that should count for highlight even though the nav item
    # points elsewhere. Used for /results → /runs/* so the Results tab
    # stays lit when the user opens a run detail page.
    best_match_len = -1
    best_match_id = None
    for it in visible:
        url = it["url"]
        candidates: list[str] = [url, *list(it.get("active_also_for", ()))]
        best = -1
        for u in candidates:
            if current_path == u:
                ml = len(u) + 1000  # exact match trumps prefix match
            elif u != "/" and current_path.startswith(u + "/"):
                ml = len(u)
            elif u == "/" and current_path == "/":
                ml = 1
            else:
                ml = -1
            if ml > best:
                best = ml
        if best > best_match_len:
            best_match_len = best
            best_match_id = it["id"]

    # Section-first markers — useful for rendering section dividers.
    seen_sections: set[str] = set()
    for it in visible:
        it["active"] = (it["id"] == best_match_id)
        it["section_first"] = it["section"] not in seen_sections
        seen_sections.add(it["section"])

    return visible


# --------------------------------------------------------------------------
# Landing-page resolution
# --------------------------------------------------------------------------

# Valid destinations a `users.preferred_landing_page` value can point to.
# Defensive: if a user had a preference for a URL they've since lost access
# to, we fall back to the computed default so they don't bounce between
# 403s and redirects.
_LANDING_PAGE_PERMISSION: dict[str, Iterable[str]] = {
    "/":              ("view_dashboard",),
    "/requirements":  ("run_single_ticket",),
    "/run":           ("run_sprint", "run_suite"),
    "/runs/new":      ("run_sprint", "run_suite"),  # legacy — wizard
    "/runs":          ("view_own_results", "view_all_results"),
    "/results":       ("view_own_results", "view_all_results"),
    "/reviews":       ("review_test_cases",),
    "/test-cases":    ("view_test_library",),
    "/suites":        ("manage_test_suites", "view_suite_quality_gates"),
    "/settings":      (),  # special: any manage_* permission
}


def _landing_permission_satisfied(url: str, perms: set,
                                  is_superadmin: bool = False) -> bool:
    if is_superadmin:
        return True
    if url == "/settings":
        return any(p.startswith("manage_") for p in perms)
    required = _LANDING_PAGE_PERMISSION.get(url)
    if required is None:
        return False
    if not required:  # empty tuple -> no perms required
        return True
    return any(p in perms for p in required)


def get_landing_page(user_permissions: set,
                     *, preferred: Optional[str] = None,
                     is_superadmin: bool = False) -> str:
    """Pick the best landing URL for this caller after login.

    Priority:
      1. Explicit `preferred` if set AND the caller still has access.
      2. Developer-only (run_single_ticket but no bulk-run / sprint) → /requirements
      3. Tester / Release Owner with run_sprint or run_suite → /runs/new
      4. view_dashboard but no run_* perms → / (dashboard)
      5. Any manage_* perm (admin-only) → / (dashboard)
      6. Fallback → / if they can view_dashboard else /requirements.
    """
    perms = set(user_permissions or ())

    # 1. Honour preference, but only if still reachable.
    if preferred and _landing_permission_satisfied(preferred, perms,
                                                   is_superadmin=is_superadmin):
        return preferred

    # Superadmin: no computation needed, just drop them on the dashboard.
    if is_superadmin:
        return "/"

    has_single = "run_single_ticket" in perms
    has_bulk = bool({"run_sprint", "run_suite"} & perms)
    has_any_run = has_single or has_bulk
    has_dashboard = "view_dashboard" in perms
    has_any_manage = any(p.startswith("manage_") for p in perms)

    # 2. Developer: single-ticket runs only.
    if has_single and not has_bulk:
        return "/requirements"

    # 3. Tester / Release Owner with bulk capability.
    if has_bulk:
        return "/run"

    # 4. Release Owner read-only view.
    if has_dashboard and not has_any_run:
        return "/"

    # 5. Admin without explicit dashboard perm still lands there.
    if has_any_manage:
        return "/"

    # 6. Fallback.
    if has_dashboard:
        return "/"
    if "run_single_ticket" in perms:
        return "/requirements"
    # Utterly unprivileged — send them to / so the page can render an
    # empty-state explaining the situation.
    return "/"


__all__ = [
    "SIDEBAR_ITEMS",
    "build_sidebar",
    "get_landing_page",
]
