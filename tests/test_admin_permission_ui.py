"""Admin permission-management UI tests.

Covers:
  Users list:
    1. /settings/users renders for manage_users holders
    2. /settings/users redirects for users without manage_users
    3. Search filter works
  User detail:
    4. /settings/users/<id> renders with assigned sets + effective perms
    5. Effective permissions grouped by category with attribution
  Permission Sets list:
    6. /settings/permission-sets renders with base + granular + custom sections
    7. Counts (permissions + users) render correctly
  Assignment API:
    8. POST /api/users/<id>/permission-sets adds new set(s)
    9. POST is idempotent (adding same set twice returns 2 requested, 0 added)
   10. POST with non-existent set_id -> 400
   11. POST without manage_users -> 403
   12. DELETE /api/users/<id>/permission-sets/<pset_id> removes the set
   13. DELETE returns 404 for non-assigned set
  Self-protection:
   14. Admin cannot revoke their own admin_base (SELF_ADMIN_REVOKE 400)
   15. POST /api/users/<id>/deactivate blocks self-deactivation (SELF_DEACTIVATE 400)
   16. Admin can deactivate OTHER users via API
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text

from primeqa.app import app
from primeqa.core.models import User
from primeqa.core.permissions import (
    PermissionSet, UserPermissionSet,
    assign_permission_set, revoke_permission_set,
)
from primeqa.db import SessionLocal

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


def login_api(email, password):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    return r.get_json().get("access_token", "")


def login_form(email, password):
    return client.post("/login",
                       data={"email": email, "password": password},
                       follow_redirects=False)


def _force_perms(user_id: int, api_names: list[str]):
    db = SessionLocal()
    try:
        db.query(UserPermissionSet).filter_by(user_id=user_id).delete()
        for name in api_names:
            ps = db.query(PermissionSet).filter_by(
                tenant_id=TENANT_ID, api_name=name).first()
            assert ps is not None, f"PermissionSet {name!r} missing"
            db.add(UserPermissionSet(user_id=user_id, permission_set_id=ps.id))
        db.commit()
    finally:
        db.close()


def _ensure_user(admin_token, email, password, role):
    """Return a user row with a known password + role. Reset-in-place
    rather than delete, because the user may be referenced by
    pipeline_runs.triggered_by FK from earlier test runs.
    """
    import bcrypt
    db = SessionLocal()
    try:
        existing = db.query(User).filter_by(email=email, tenant_id=TENANT_ID).first()
        if existing is not None:
            existing.password_hash = bcrypt.hashpw(
                password.encode("utf-8"), bcrypt.gensalt(rounds=4)
            ).decode("utf-8")
            existing.role = role
            existing.is_active = True
            existing.full_name = email.split("@")[0].replace(".", " ").title()
            db.execute(text("DELETE FROM user_permission_sets WHERE user_id = :id"),
                       {"id": existing.id})
            db.commit()
    finally:
        db.close()
    # New create when the user didn't exist before:
    db = SessionLocal()
    try:
        exists_after = db.query(User).filter_by(
            email=email, tenant_id=TENANT_ID).first() is not None
    finally:
        db.close()
    if not exists_after:
        r = client.post("/api/auth/users",
                        headers={"Authorization": f"Bearer {admin_token}"},
                        json={"email": email, "password": password,
                              "full_name": email.split("@")[0].replace(".", " ").title(),
                              "role": role})
        assert r.status_code in (200, 201), \
            f"failed to create {email}: {r.status_code} {r.data[:200]}"
    # Always return a freshly-loaded, session-bound instance — but since
    # the session closes here, the CALLER should access via .id immediately
    # or re-query. The tests below only use .id so this is fine.
    db = SessionLocal()
    try:
        return db.query(User).filter_by(email=email, tenant_id=TENANT_ID).first()
    finally:
        db.close()


def run_tests():
    results = []
    print("\n=== Admin Permission UI Tests ===\n")

    admin_token = login_api("admin@primeqa.io", "changeme123")
    # Build two throwaway users: one we'll treat as a regular admin
    # target, one that only has developer_base (non-admin).
    admin2 = _ensure_user(admin_token, "admin2@primeqa.io", "test123", "admin")
    dev_u = _ensure_user(admin_token, "adm_dev@primeqa.io", "test123", "tester")
    _force_perms(admin2.id, ["admin_base"])
    _force_perms(dev_u.id, ["developer_base"])

    # ---- /settings/users ----
    def test_list_renders_for_admin():
        login_form("admin@primeqa.io", "changeme123")
        r = client.get("/settings/users", follow_redirects=False)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        html = r.data.decode("utf-8", "replace")
        assert "admin2@primeqa.io" in html, "admin2 should be listed"
        assert "adm_dev@primeqa.io" in html, "dev_u should be listed"
    results.append(test("1. /settings/users renders with users + permission sets",
                        test_list_renders_for_admin))

    def test_list_redirects_without_permission():
        login_form("adm_dev@primeqa.io", "test123")
        r = client.get("/settings/users", follow_redirects=False)
        assert r.status_code in (301, 302), f"Expected redirect, got {r.status_code}"
        assert r.headers["Location"] in ("/", "/requirements"), r.headers["Location"]
    results.append(test("2. /settings/users redirects non-admin to landing page",
                        test_list_redirects_without_permission))

    def test_list_search_filter():
        login_form("admin@primeqa.io", "changeme123")
        r = client.get("/settings/users?search=adm_dev")
        assert r.status_code == 200
        html = r.data.decode("utf-8", "replace")
        assert "adm_dev@primeqa.io" in html
        # admin2 should NOT appear since it doesn't match the search
        assert "admin2@primeqa.io" not in html
    results.append(test("3. /settings/users ?search= filters by email",
                        test_list_search_filter))

    # ---- /settings/users/<id> ----
    def test_detail_shows_assigned_and_effective():
        login_form("admin@primeqa.io", "changeme123")
        r = client.get(f"/settings/users/{dev_u.id}")
        assert r.status_code == 200, r.status_code
        html = r.data.decode("utf-8", "replace")
        # Assigned set appears
        assert "Developer Base" in html, "Developer Base assignment missing"
        # Effective-permissions grouping renders with attribution
        assert "Execution" in html, "Execution category missing"
        assert "run_single_ticket" in html, "run_single_ticket should appear in effective list"
    results.append(test("4. User detail shows assigned sets + effective perms",
                        test_detail_shows_assigned_and_effective))

    def test_detail_groups_and_attributes():
        login_form("admin@primeqa.io", "changeme123")
        # Add an extra granular set so we can verify attribution shows
        # it separately from the base set.
        db = SessionLocal()
        try:
            view_dash = (db.query(PermissionSet)
                         .filter_by(tenant_id=TENANT_ID, api_name="view_dashboard")
                         .first())
            view_dash_id = view_dash.id  # capture before session closes
            assign_permission_set(dev_u.id, view_dash_id, db)
            db.commit()
        finally:
            db.close()
        try:
            r = client.get(f"/settings/users/{dev_u.id}")
            html = r.data.decode("utf-8", "replace")
            assert "Reporting" in html, "Reporting category should now appear"
            assert "view_dashboard" in html
            # Attribution: should say "← View Dashboard" somewhere
            assert "View Dashboard" in html
        finally:
            db = SessionLocal()
            try:
                revoke_permission_set(dev_u.id, view_dash_id, db)
                db.commit()
            finally:
                db.close()
    results.append(test("5. User detail groups by category + shows attribution",
                        test_detail_groups_and_attributes))

    # ---- /settings/permission-sets ----
    def test_permission_sets_page():
        login_form("admin@primeqa.io", "changeme123")
        r = client.get("/settings/permission-sets")
        assert r.status_code == 200, r.status_code
        html = r.data.decode("utf-8", "replace")
        # Base sets rendered
        assert "Developer Base" in html
        assert "Admin Base" in html
        # Granular section present (collapsed by default)
        assert "System Granular Sets" in html
        # Custom sets section present
        assert "Custom Sets" in html
    results.append(test("6. /settings/permission-sets renders all three sections",
                        test_permission_sets_page))

    def test_permission_set_counts():
        login_form("admin@primeqa.io", "changeme123")
        r = client.get("/settings/permission-sets")
        html = r.data.decode("utf-8", "replace")
        # admin_base has 39 perms in the seed
        assert ">39<" in html, "admin_base perm count should appear"
    results.append(test("7. Permission set counts render correctly",
                        test_permission_set_counts))

    # ---- Assign / revoke API ----
    def test_assign_api_adds():
        # Use Bearer (API bypasses CSRF).
        token = login_api("admin@primeqa.io", "changeme123")
        db = SessionLocal()
        try:
            run_sprint = (db.query(PermissionSet)
                          .filter_by(tenant_id=TENANT_ID, api_name="run_sprint")
                          .first())
        finally:
            db.close()
        # Clean slate for dev_u
        _force_perms(dev_u.id, ["developer_base"])
        r = client.post(f"/api/users/{dev_u.id}/permission-sets",
                        headers={"Authorization": f"Bearer {token}"},
                        json={"permission_set_ids": [run_sprint.id]})
        assert r.status_code == 200, f"{r.status_code} {r.data}"
        body = r.get_json()
        assert body["assigned"] == 1, body
    results.append(test("8. POST permission-sets adds new set", test_assign_api_adds))

    def test_assign_api_idempotent():
        token = login_api("admin@primeqa.io", "changeme123")
        db = SessionLocal()
        try:
            run_sprint = (db.query(PermissionSet)
                          .filter_by(tenant_id=TENANT_ID, api_name="run_sprint")
                          .first())
        finally:
            db.close()
        r = client.post(f"/api/users/{dev_u.id}/permission-sets",
                        headers={"Authorization": f"Bearer {token}"},
                        json={"permission_set_ids": [run_sprint.id]})
        body = r.get_json()
        assert r.status_code == 200
        assert body["assigned"] == 0, f"Already assigned, should be 0: {body}"
        assert body["requested"] == 1
    results.append(test("9. POST is idempotent (already-assigned -> 0 added)",
                        test_assign_api_idempotent))

    def test_assign_api_bad_id():
        token = login_api("admin@primeqa.io", "changeme123")
        r = client.post(f"/api/users/{dev_u.id}/permission-sets",
                        headers={"Authorization": f"Bearer {token}"},
                        json={"permission_set_ids": [999999999]})
        assert r.status_code == 400, f"{r.status_code} {r.data}"
        assert r.get_json()["error"]["code"] == "VALIDATION_ERROR"
    results.append(test("10. POST with unknown permission_set_id -> 400",
                        test_assign_api_bad_id))

    def test_assign_api_requires_manage_users():
        # Build a non-admin token holder: login as adm_dev (only developer_base).
        tok = login_api("adm_dev@primeqa.io", "test123")
        r = client.post(f"/api/users/{dev_u.id}/permission-sets",
                        headers={"Authorization": f"Bearer {tok}"},
                        json={"permission_set_ids": [1]})
        assert r.status_code == 403, f"{r.status_code} {r.data}"
        body = r.get_json()
        assert body["error"]["code"] == "INSUFFICIENT_PERMISSIONS", body
    results.append(test("11. POST without manage_users -> 403",
                        test_assign_api_requires_manage_users))

    def test_delete_api_removes():
        token = login_api("admin@primeqa.io", "changeme123")
        db = SessionLocal()
        try:
            run_sprint = (db.query(PermissionSet)
                          .filter_by(tenant_id=TENANT_ID, api_name="run_sprint")
                          .first())
        finally:
            db.close()
        r = client.delete(
            f"/api/users/{dev_u.id}/permission-sets/{run_sprint.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 204, f"{r.status_code} {r.data}"
        # Second delete returns 404
        r2 = client.delete(
            f"/api/users/{dev_u.id}/permission-sets/{run_sprint.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r2.status_code == 404
    results.append(test("12. DELETE revokes assignment, returns 204",
                        test_delete_api_removes))

    def test_delete_returns_404_for_unassigned():
        # Already covered partially by test 12's follow-up. Explicit case:
        token = login_api("admin@primeqa.io", "changeme123")
        db = SessionLocal()
        try:
            # Find any granular set not assigned to dev_u
            ps = (db.query(PermissionSet)
                  .filter_by(tenant_id=TENANT_ID, api_name="view_trends")
                  .first())
            ps_id = ps.id  # capture before session closes
            # Make sure it's NOT assigned (defensive).
            db.query(UserPermissionSet).filter_by(
                user_id=dev_u.id, permission_set_id=ps_id).delete()
            db.commit()
        finally:
            db.close()
        r = client.delete(
            f"/api/users/{dev_u.id}/permission-sets/{ps_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404
    results.append(test("13. DELETE on un-assigned set returns 404",
                        test_delete_returns_404_for_unassigned))

    # ---- Self-protection ----
    def test_cannot_revoke_own_admin():
        # Seeded admin@primeqa.io has role 'superadmin' on this tenant
        # which bypasses self-protect. Use admin2 (non-superadmin admin
        # with admin_base) to exercise the guard.
        # admin2 token:
        tok = login_api("admin2@primeqa.io", "test123")
        db = SessionLocal()
        try:
            admin_base = (db.query(PermissionSet)
                          .filter_by(tenant_id=TENANT_ID, api_name="admin_base")
                          .first())
        finally:
            db.close()
        r = client.delete(
            f"/api/users/{admin2.id}/permission-sets/{admin_base.id}",
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert r.status_code == 400, f"{r.status_code} {r.data}"
        body = r.get_json()
        assert body["error"]["code"] == "SELF_ADMIN_REVOKE", body
    results.append(test("14. Admin cannot revoke own admin_base (SELF_ADMIN_REVOKE)",
                        test_cannot_revoke_own_admin))

    def test_cannot_deactivate_self():
        tok = login_api("admin2@primeqa.io", "test123")
        r = client.post(f"/api/users/{admin2.id}/deactivate",
                        headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 400, f"{r.status_code} {r.data}"
        assert r.get_json()["error"]["code"] == "SELF_DEACTIVATE"
    results.append(test("15. Admin cannot deactivate own account (SELF_DEACTIVATE)",
                        test_cannot_deactivate_self))

    def test_admin_can_deactivate_other():
        tok = login_api("admin@primeqa.io", "changeme123")
        # Deactivate dev_u
        r = client.post(f"/api/users/{dev_u.id}/deactivate",
                        headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 204, f"{r.status_code} {r.data}"
        # Re-activate so subsequent tests (if any) see the user alive.
        r = client.post(f"/api/users/{dev_u.id}/activate",
                        headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 204
    results.append(test("16. Admin can deactivate + reactivate OTHER users",
                        test_admin_can_deactivate_other))

    # --- summary ---
    passed = sum(results)
    total = len(results)
    print(f"\n{'='*60}")
    print(f"  {passed}/{total} passed")
    print(f"{'='*60}\n")
    return passed == total


if __name__ == "__main__":
    ok = run_tests()
    sys.exit(0 if ok else 1)
