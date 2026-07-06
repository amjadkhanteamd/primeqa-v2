"""Tests for role-driven navigation + landing pages (D-245).

Authorization is the role ladder now (viewer < member < admin < superadmin via
primeqa.core.authz.rank). The nav + landing still consume a *capability set*,
but that set is derived from the caller's ROLE (primeqa.core.permissions.
_role_capabilities) instead of a stored permission-set union. These tests assert
the nav/landing each role unlocks.

Covers:

  Sidebar builder (unit, no Flask needed)
    1. viewer  -> read surfaces (Dashboard, Test Library, Releases, Insights, Ask)
    2. member  -> primary run/triage section + testing surfaces + Settings
    3. admin   -> every section incl. Org Model
    4. Ask + Knowledge nav gates
    5. Active highlighting: /runs/42 highlights the Results item (url=/runs/substrate)
    6. section_first markers only at section boundaries

  Landing-page resolver
    7. viewer -> /dashboard
    8. member -> /requirements
    9. admin  -> /requirements
   10. preferred_landing_page honoured when role still has access
   11. preferred_landing_page the role can't reach -> falls back to computed

  End-to-end (Flask test client)
   12. Login as the seeded superadmin -> redirected to /
   13. /requirements renders with sidebar nav items

NOTE (D-245): the E2E tests (12, 13) are integration checks — they run against
the live Railway DB + mint JWTs. Re-run on a real environment to confirm green;
they cannot execute in a sandbox that blocks DB writes / token minting. Tests
1-11 are pure-unit (no DB / no Flask) and run anywhere.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from primeqa.app import app
from primeqa.core.navigation import (
    SIDEBAR_ITEMS, build_sidebar, get_landing_page,
)
from primeqa.core.permissions import _role_capabilities

TENANT_ID = 1
client = app.test_client()


def test(name, fn):
    try:
        fn()
        print(f"  PASS  {name}")
        return True
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        return False
    except Exception as e:
        import traceback
        print(f"  ERROR {name}: {type(e).__name__}: {e}")
        traceback.print_exc()
        return False


def _nav_ids(role: str, path: str = "/") -> list:
    return [i["id"] for i in build_sidebar(_role_capabilities(role), path)]


def _cookie_login(email, password):
    """Login via the HTML form handler (sets access_token cookie)."""
    return client.post("/login", data={"email": email, "password": password},
                       follow_redirects=False)


def login_via_client(email, password):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    return r.get_json().get("access_token", ""), r


def run_tests():
    results = []
    print("\n=== Role-driven Navigation + Landing Tests (D-245) ===\n")

    # ---------------- Sidebar builder (unit) ----------------

    def test_viewer_sidebar():
        ids = _nav_ids("viewer", "/dashboard")
        # Viewer holds read surfaces only.
        assert "dashboard" in ids, ids
        assert "test_library" in ids, ids
        assert "releases" in ids, ids          # via view_dashboard
        # No run / triage / admin items.
        assert "my_tickets" not in ids, ids
        assert "run_tests" not in ids, ids
        assert "results" not in ids, ids
        assert "settings" not in ids, ids      # no manage_* at Viewer
        assert "org_model" not in ids, ids
    results.append(test("1. viewer sidebar = read surfaces only",
                        test_viewer_sidebar))

    def test_member_sidebar():
        ids = _nav_ids("tester", "/run")
        # Member can run / triage / review.
        assert "my_tickets" in ids, ids
        assert "run_tests" in ids, ids
        assert "results" in ids, ids
        assert "my_reviews" in ids, ids
        assert "test_library" in ids, ids
        # Member holds manage_knowledge + manage_test_suites -> Settings.
        assert "settings" in ids, ids
        # But NOT the admin-tier Org Model (needs manage_environments).
        assert "org_model" not in ids, ids
    results.append(test("2. member sidebar = run/triage + testing + settings",
                        test_member_sidebar))

    def test_admin_sidebar():
        ids = _nav_ids("admin", "/settings")
        sections = {i["section"] for i in build_sidebar(_role_capabilities("admin"), "/settings")}
        assert "primary" in sections
        assert "testing" in sections
        assert "admin" in sections, sections
        assert "settings" in ids, ids
        assert "releases" in ids, ids
        # Disabled-page items stay hidden until their pages ship.
        assert "audit_log" not in ids, ids
        assert "coverage" not in ids, ids
    results.append(test("3. admin sidebar = primary/testing/admin sections",
                        test_admin_sidebar))

    def test_tools_moved_out_of_nav():
        # AK 2026-07-07: Substrate Insights / Ask / Org Model / Knowledge moved
        # from the top bar to the Settings sidebar's Tools section — they must
        # not appear in nav for ANY tier (routes + gates unchanged).
        for role, path in (("viewer", "/dashboard"), ("tester", "/run"),
                           ("admin", "/settings"), ("superadmin", "/settings")):
            ids = _nav_ids(role, path)
            for moved in ("ask", "substrate_insights", "org_model", "knowledge"):
                assert moved not in ids, (role, moved, ids)
    results.append(test("4. Ask/Insights/Org Model/Knowledge moved to Settings",
                        test_tools_moved_out_of_nav))

    def test_active_highlights_prefix_match():
        # /runs/42 highlights Results (url=/runs/substrate, active_also_for /runs).
        nav = build_sidebar(_role_capabilities("admin"), "/runs/42")
        active = [i for i in nav if i["active"]]
        assert len(active) == 1, active
        assert active[0]["id"] == "results", active[0]
    results.append(test("5. Active highlight uses longest-URL-match, not just root",
                        test_active_highlights_prefix_match))

    def test_section_first_markers():
        nav = build_sidebar(_role_capabilities("admin"), "/")
        prev = None
        for i in nav:
            if prev is None:
                assert i["section_first"] is True, i
            else:
                expected = (i["section"] != prev["section"])
                assert i["section_first"] is expected, \
                    f"item {i['id']} section_first={i['section_first']}, expected={expected}"
            prev = i
    results.append(test("6. section_first is True only at section boundaries",
                        test_section_first_markers))

    # ---------------- Landing-page resolver ----------------

    def test_landing_viewer():
        # Viewer: dashboard read, no run perms -> /dashboard.
        assert get_landing_page(_role_capabilities("viewer")) == "/dashboard"
    results.append(test("7. viewer perms -> /dashboard", test_landing_viewer))

    def test_landing_member():
        # Member has bulk-run -> the substrate-native /requirements surface.
        assert get_landing_page(_role_capabilities("tester")) == "/requirements"
    results.append(test("8. member perms -> /requirements", test_landing_member))

    def test_landing_admin():
        # Admin inherits Member's bulk-run -> /requirements by priority.
        assert get_landing_page(_role_capabilities("admin")) == "/requirements"
    results.append(test("9. admin perms -> /requirements", test_landing_admin))

    def test_landing_preference_honoured():
        # Member can view the test library -> /test-cases preference is reachable.
        assert get_landing_page(_role_capabilities("tester"),
                                preferred="/test-cases") == "/test-cases"
    results.append(test("10. preferred_landing_page honoured when reachable",
                        test_landing_preference_honoured))

    def test_landing_preference_falls_back():
        # Viewer can't run -> a /run preference falls back to the computed page.
        assert get_landing_page(_role_capabilities("viewer"),
                                preferred="/run") == "/dashboard"
    results.append(test("11. preferred page the role can't reach -> computed fallback",
                        test_landing_preference_falls_back))

    # ---------------- End-to-end via Flask test client ----------------

    def test_login_redirects_superadmin_to_root():
        r = _cookie_login("admin@primeqa.io", "changeme123")
        assert r.status_code in (301, 302), f"Expected redirect, got {r.status_code}"
        # the seeded admin@primeqa.io is superadmin -> /
        assert r.headers["Location"].endswith("/"), r.headers["Location"]
    results.append(test("12. Login POST redirects to landing page",
                        test_login_redirects_superadmin_to_root))

    def test_page_renders_with_sidebar():
        _cookie_login("admin@primeqa.io", "changeme123")
        r = client.get("/requirements", follow_redirects=False)
        assert r.status_code == 200, f"/requirements: {r.status_code}"
        html = r.data.decode("utf-8", errors="replace")
        assert 'data-nav-id="my_tickets"' in html, "my_tickets nav item missing"
        assert 'data-nav-id="results"' in html, "results nav item missing"
    results.append(test("13. /requirements renders and includes sidebar nav items",
                        test_page_renders_with_sidebar))

    # ---------------- summary ----------------
    passed = sum(results)
    total = len(results)
    print(f"\n{'='*60}")
    print(f"  {passed}/{total} passed")
    print(f"{'='*60}\n")
    return passed == total


if __name__ == "__main__":
    ok = run_tests()
    sys.exit(0 if ok else 1)
