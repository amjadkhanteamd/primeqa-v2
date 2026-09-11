"""Dynamic navigation + landing-page resolution driven by permission sets.

Every sidebar / top-nav item declares the permission(s) that unlock it.
`build_sidebar()` filters the static registry against the caller's
effective permissions so the rendered nav only ever shows what the user
can actually use. `get_landing_page()` answers "after login, where?"
based on the permission-set union — same source of truth as the UI.

Step 6a (LLD_STEP_6A_WORK_SURFACES §a) collapsed the registry to four rendered
items — Requirements, Results, Releases, Settings — each gated on a view_*
capability held from VIEWER up. The mapping table below is the historical
record of how the original prompt's URLs landed on real routes.

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
    # Step 6a (LLD_STEP_6A §a): FOUR items. Eight slots collapsed to two work
    # surfaces plus Releases and Settings — Requirements is "what should be
    # true", Results is "what happened". The removed slots' content has a home:
    #   Run Tests    -> retired to a redirect at D-486 (/run -> /releases?from=run)
    #   Test Library -> the Requirements "All claims" tab
    #   My Reviews   -> the Requirements "Needs review" tab + the badge below
    #   Dashboard    -> Releases in 6b; the CHIP goes now, the ROUTE and its
    #                   landing entry stay working until 6b re-homes the content
    #                   (ruling 1) — /dashboard is not retired by this slice.
    # The gates are the new view_* capabilities (VIEWER): reading the work is a
    # viewer's right, acting on it is not. Before 6a, Requirements was gated on
    # run_single_ticket (MEMBER), so a viewer saw no Requirements item at all.
    {
        "id": "requirements",
        "label": "Requirements",
        "icon": "ticket",
        "url": "/requirements",
        # /claims and /reviews redirect here; keep the tab lit on the way through
        "active_also_for": ("/claims", "/reviews", "/test-cases", "/tickets"),
        "permission": "view_requirements",
        "section": "primary",
    },
    {
        "id": "results",
        "label": "Results",
        "icon": "chart",
        # D-218: results live on the substrate runs index. Step 6a adds the
        # conformance lane and the re-homed run view, so /ui-report lights it too.
        "url": "/runs/substrate",
        "active_also_for": ("/runs", "/results", "/ui-report"),
        "permission": "view_results",
        "section": "primary",
    },
    {
        "id": "releases",
        "label": "Releases",
        "icon": "package",
        "url": "/releases",
        # /dashboard's content moves here in 6b; light the tab from now so the
        # surviving route reads as part of Releases rather than as an orphan.
        "active_also_for": ("/dashboard",),
        "permission": "view_releases",
        "section": "primary",
    },
    {
        "id": "settings",
        "label": "Settings",
        "icon": "settings",
        "url": "/settings",
        "permission_any_prefix": "manage_",
        "section": "admin",
    },
    # Never rendered (no backing page); kept so the item reappears when one ships.
    {
        "id": "coverage",
        "label": "Coverage Map",
        "icon": "map",
        "url": "/coverage",
        "permission": "view_coverage_map",
        "section": "testing",
        "enabled": False,
    },
    {
        "id": "audit_log",
        "label": "Audit Log",
        "icon": "history",
        "url": "/audit-log",
        "permission": "view_audit_log",
        "section": "admin",
        "enabled": False,
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
    # `enabled: False` hides an item from the sidebar entirely until the
    # backing page ships. Missing the key defaults to enabled (True).
    visible = [dict(item) for item in SIDEBAR_ITEMS
               if item.get("enabled", True)
               and _item_allowed(item, user_permissions, is_superadmin=is_superadmin)]

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
    # Step 6a ruling 1: /dashboard keeps working and keeps its landing entry
    # until 6b re-homes it; only the nav chip went.
    "/dashboard":     ("view_dashboard",),
    "/requirements":  ("view_requirements", "run_single_ticket"),
    "/run":           ("run_sprint", "run_suite"),
    "/runs/new":      ("run_sprint", "run_suite"),  # legacy — wizard
    "/runs":          ("view_results", "view_own_results", "view_all_results"),
    "/runs/substrate": ("view_results", "view_own_results", "view_all_results"),
    "/results":       ("view_results", "view_own_results", "view_all_results"),
    # Step 6a: these three redirect into Requirements; a stored preference for
    # one still resolves rather than bouncing the caller to the fallback.
    "/reviews":       ("view_requirements", "review_test_cases"),
    "/claims/inbox":  ("view_requirements", "review_test_cases"),
    "/claims":        ("view_requirements", "view_test_library"),
    "/test-cases":    ("view_requirements", "view_test_library"),
    "/releases":      ("view_releases", "approve_release", "view_dashboard"),
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

    # 3. Tester / Release Owner with bulk capability. D-218: land on the
    # substrate-native requirements surface, not the legacy /run trigger page.
    if has_bulk:
        return "/requirements"

    # 4. Release Owner read-only view — new dashboard at /dashboard.
    if has_dashboard and not has_any_run:
        return "/dashboard"

    # 5. Admin without explicit dashboard perm still lands there.
    if has_any_manage:
        return "/"

    # 6. Fallback.
    if has_dashboard:
        return "/dashboard"
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
