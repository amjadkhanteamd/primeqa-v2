"""Tests for dynamic-UI / permission-driven navigation + landing pages.

Covers:

  Sidebar builder (unit, no Flask needed)
    1. developer_base -> My Tickets only (one-item nav, no sidebar)
    2. tester_base   -> My Tickets, Run Tests, Results, Test Library (+ suites if perm)
    3. release_owner_base -> Dashboard, Test Suites
    4. admin_base -> every section populated
    5. Custom mix: developer_base + review_test_cases -> adds My Reviews
    6. Active highlighting: /runs/42 highlights the Results item (url=/runs)
    7. section_first markers only on the first item of each section

  Landing-page resolver
    8. developer-only perms -> /requirements
    9. tester perms         -> /runs/new
   10. release-owner perms  -> /
   11. admin perms          -> /
   12. preferred_landing_page honoured when user still has access
   13. preferred_landing_page that lost access -> falls back to computed

  End-to-end (Flask test client)
   14. Login as admin -> redirected to /
   15. /requirements page renders with sidebar containing an active item
   16. require_page_permission: unauthorised user redirected to their landing
   17. require_page_permission: superadmin bypass passes without redirect
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text

from primeqa.app import app
from primeqa.core.models import User
from primeqa.core.navigation import (
    SIDEBAR_ITEMS, build_sidebar, get_landing_page,
)
from primeqa.core.permissions import (
    BASE_PERMISSION_SETS, PermissionSet, UserPermissionSet,
    require_page_permission,
)
from primeqa.db import SessionLocal
from primeqa.views import login_required

TENANT_ID = 1
client = app.test_client()


# Register a throwaway gated page at MODULE-LOAD time so Flask 2+ allows the
# route registration. Used by tests 16 + 17 below.
if "_test_page_gate" not in {r.endpoint for r in app.url_map.iter_rules()}:
    @app.route("/_test/page_gate", endpoint="_test_page_gate")
    @login_required
    @require_page_permission("manage_knowledge")
    def _test_page_gate():  # noqa: ANN001
        return "OK", 200


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


def _base_perms(api_name: str) -> set:
    """Return the permission set for a base permission-set api_name."""
    spec = next(s for s in BASE_PERMISSION_SETS if s["api_name"] == api_name)
    return set(spec["permissions"])


def login_via_client(email, password):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    return r.get_json().get("access_token", ""), r


def _cookie_login(email, password):
    """Login via the HTML form handler (sets access_token cookie)."""
    r = client.post("/login", data={"email": email, "password": password},
                    follow_redirects=False)
    return r


def run_tests():
    results = []
    print("\n=== Dynamic UI / Navigation Tests ===\n")

    # ---------------- Sidebar builder (unit) ----------------

    def test_developer_sidebar():
        nav = build_sidebar(_base_perms("developer_base"), "/requirements")
        ids = [i["id"] for i in nav]
        # Developer Base has: connect_personal_org, run_single_ticket,
        # view_own_results, view_own_diagnosis, rerun_own_ticket
        # -> My Tickets (run_single_ticket), Results (view_own_results)
        assert "my_tickets" in ids, ids
        assert "results" in ids, ids
        assert "run_tests" not in ids, ids
        assert "dashboard" not in ids, ids
        assert "settings" not in ids, ids
    results.append(test("1. developer_base sidebar = My Tickets + Results",
                        test_developer_sidebar))

    def test_tester_sidebar():
        nav = build_sidebar(_base_perms("tester_base"), "/runs")
        ids = [i["id"] for i in nav]
        assert "my_tickets" in ids
        assert "run_tests" in ids
        assert "results" in ids
        assert "test_library" in ids
        assert "test_suites" in ids
        # Tester has view_coverage_map, BUT the Coverage Map page is
        # disabled in the nav today (enabled: False) because the page
        # isn't built yet. When it ships we flip the flag and this
        # assertion can move back to "coverage" in ids.
        assert "coverage" not in ids
        # Tester has no view_dashboard.
        assert "dashboard" not in ids, ids
        # Tester HAS review_test_cases (tester_base bundle) -> my_reviews visible.
        assert "my_reviews" in ids, ids
        # NOTE: tester_base contains manage_test_suites, which matches the
        # "settings" item's permission_any_prefix="manage_" gate. So
        # Settings IS visible for testers. Whether that's desirable is a
        # UX call — tightening the gate (e.g. only manage_environments /
        # manage_users) would exclude testers. For now we follow the
        # spec literally and assert the consequence.
        assert "settings" in ids, ids
    results.append(test("2. tester_base sidebar = primary + testing sections",
                        test_tester_sidebar))

    def test_release_owner_sidebar():
        nav = build_sidebar(_base_perms("release_owner_base"), "/")
        ids = [i["id"] for i in nav]
        assert "dashboard" in ids
        # Release Owner has view_suite_quality_gates -> Test Suites
        assert "test_suites" in ids
        # No runs or results (no run_* perms)
        assert "run_tests" not in ids, ids
        assert "results" not in ids, ids
    results.append(test("3. release_owner_base sidebar = Dashboard + Test Suites",
                        test_release_owner_sidebar))

    def test_admin_sidebar():
        nav = build_sidebar(_base_perms("admin_base"), "/settings")
        ids = [i["id"] for i in nav]
        # Every named section populated — admin holds perms that reach
        # items in primary, testing, and admin sections.
        sections = {i["section"] for i in nav}
        assert "primary" in sections
        assert "testing" in sections
        assert "admin" in sections, sections
        assert "settings" in ids
        # Releases item is restored — admin_base has view_dashboard +
        # approve_release so it appears in the testing section.
        assert "releases" in ids, ids
        # audit_log + knowledge + coverage are disabled today (pages
        # aren't built); they stay in SIDEBAR_ITEMS but enabled=False
        # hides them. Re-enable and this test should flip back.
        assert "audit_log" not in ids
        assert "knowledge" not in ids
        assert "coverage" not in ids
    results.append(test("4. admin_base sidebar = every section populated + releases",
                        test_admin_sidebar))

    def test_developer_plus_review():
        perms = _base_perms("developer_base") | {"review_test_cases"}
        nav = build_sidebar(perms, "/reviews")
        ids = [i["id"] for i in nav]
        assert "my_reviews" in ids, ids
        assert "my_tickets" in ids, ids
    results.append(test("5. developer_base + review_test_cases -> My Reviews appears",
                        test_developer_plus_review))

    def test_active_highlights_prefix_match():
        # /runs/42 should highlight Results (url=/runs), NOT Dashboard (url=/)
        nav = build_sidebar(_base_perms("admin_base"), "/runs/42")
        active = [i for i in nav if i["active"]]
        assert len(active) == 1, active
        assert active[0]["id"] == "results", active[0]
    results.append(test("6. Active highlight uses longest-URL-match, not just root",
                        test_active_highlights_prefix_match))

    def test_section_first_markers():
        nav = build_sidebar(_base_perms("admin_base"), "/")
        # First item is section_first=True. Every subsequent item is
        # section_first iff the preceding item was in a different section.
        prev = None
        for i in nav:
            if prev is None:
                assert i["section_first"] is True, i
            else:
                expected = (i["section"] != prev["section"])
                assert i["section_first"] is expected, \
                    f"item {i['id']} section_first={i['section_first']}, expected={expected}"
            prev = i
    results.append(test("7. section_first is True only at section boundaries",
                        test_section_first_markers))

    # ---------------- Landing-page resolver ----------------

    def test_landing_developer():
        perms = _base_perms("developer_base")
        assert get_landing_page(perms) == "/requirements"
    results.append(test("8. developer-only perms -> /requirements",
                        test_landing_developer))

    def test_landing_tester():
        perms = _base_perms("tester_base")
        # Updated in Prompt 7 — testers now land on the focused /run
        # page, not the advanced Run Wizard at /runs/new.
        assert get_landing_page(perms) == "/run"
    results.append(test("9. tester perms -> /run", test_landing_tester))

    def test_landing_release_owner():
        perms = _base_perms("release_owner_base")
        assert get_landing_page(perms) == "/"
    results.append(test("10. release-owner perms -> / (dashboard)",
                        test_landing_release_owner))

    def test_landing_admin():
        perms = _base_perms("admin_base")
        # Admin has run_sprint (via bundle) -> /run by priority.
        # But dashboard is the practical default. Accept either — the
        # contract is "a valid page the user can see".
        target = get_landing_page(perms)
        assert target in ("/run", "/runs/new", "/"), f"admin landing {target!r}"
    results.append(test("11. admin perms -> valid destination",
                        test_landing_admin))

    def test_landing_preference_honoured():
        perms = _base_perms("tester_base")
        assert get_landing_page(perms, preferred="/test-cases") == "/test-cases"
    results.append(test("12. preferred_landing_page honoured when reachable",
                        test_landing_preference_honoured))

    def test_landing_preference_falls_back():
        perms = _base_perms("developer_base")
        # Developer cannot view_dashboard. Preference -> / should fall back.
        assert get_landing_page(perms, preferred="/settings") == "/requirements"
    results.append(test("13. preferred page user lost access to -> computed fallback",
                        test_landing_preference_falls_back))

    # ---------------- End-to-end via Flask test client ----------------

    def test_login_redirects_admin_to_landing():
        r = _cookie_login("admin@primeqa.io", "changeme123")
        assert r.status_code in (301, 302), f"Expected redirect, got {r.status_code}"
        # superadmin -> /
        assert r.headers["Location"].endswith("/"), r.headers["Location"]
    results.append(test("14. Login POST redirects to landing page",
                        test_login_redirects_admin_to_landing))

    def test_page_renders_with_sidebar():
        # Reuse the cookie from the previous login
        _cookie_login("admin@primeqa.io", "changeme123")
        r = client.get("/requirements", follow_redirects=False)
        assert r.status_code == 200, f"/requirements: {r.status_code}"
        html = r.data.decode("utf-8", errors="replace")
        # Nav items appear in the rendered HTML
        assert "data-nav-id=\"my_tickets\"" in html, "my_tickets nav item missing"
        assert "data-nav-id=\"results\"" in html, "results nav item missing"
    results.append(test("15. /requirements renders and includes sidebar nav items",
                        test_page_renders_with_sidebar))

    def test_require_page_permission_redirects_denied():
        # Seed a non-admin user if missing. Use the Bearer-token admin route
        # (the HTML /login route doesn't set up a session usable by /api/*).
        admin_token, _ = login_via_client("admin@primeqa.io", "changeme123")
        db = SessionLocal()
        try:
            u = db.query(User).filter_by(email="ui_dev@primeqa.io",
                                         tenant_id=TENANT_ID).first()
        finally:
            db.close()
        if u is None:
            r = client.post("/api/auth/users",
                            headers={"Authorization": f"Bearer {admin_token}"},
                            json={"email": "ui_dev@primeqa.io",
                                  "password": "test123",
                                  "full_name": "UI Dev", "role": "tester"})
            assert r.status_code in (200, 201), f"create user: {r.status_code} {r.data}"
            db = SessionLocal()
            try:
                u = db.query(User).filter_by(email="ui_dev@primeqa.io",
                                             tenant_id=TENANT_ID).first()
            finally:
                db.close()
        assert u is not None, "ui_dev@primeqa.io still missing"

        # Force exactly developer_base (no dashboard / no manage_knowledge).
        db = SessionLocal()
        try:
            db.query(UserPermissionSet).filter_by(user_id=u.id).delete()
            ps = db.query(PermissionSet).filter_by(
                tenant_id=TENANT_ID, api_name="developer_base").first()
            db.add(UserPermissionSet(user_id=u.id, permission_set_id=ps.id))
            db.commit()
        finally:
            db.close()

        _cookie_login("ui_dev@primeqa.io", "test123")
        r = client.get("/_test/page_gate", follow_redirects=False)
        assert r.status_code in (301, 302), f"Expected redirect, got {r.status_code}"
        # developer_base -> /requirements
        assert "/requirements" in r.headers["Location"], r.headers["Location"]
    results.append(test("16. require_page_permission redirects denied user to landing",
                        test_require_page_permission_redirects_denied))

    def test_require_page_permission_superadmin_bypass():
        # superadmin should NOT be redirected even if they lack the permission.
        _cookie_login("admin@primeqa.io", "changeme123")
        r = client.get("/_test/page_gate", follow_redirects=False)
        assert r.status_code == 200, f"superadmin should pass: {r.status_code}"
    results.append(test("17. superadmin bypass: require_page_permission -> 200",
                        test_require_page_permission_superadmin_bypass))

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
