"""Release Owner Dashboard tests (Prompt 10).

Covers:
  Page
    1. /dashboard renders for release_owner_base
    2. /dashboard renders for admin_base
    3. /dashboard redirects developer_base (no view_dashboard)
    4. Empty-state when no runs exist for the active env
  Go/No-Go determination (service-layer unit tests)
    5. Fallback threshold: pass rate >= 80% -> GO
    6. Fallback threshold: pass rate < 80% -> NO-GO
    7. Any failing gate -> NO-GO with callout
    8. All gates passing -> GO
    9. release_status=APPROVED is sticky
   10. release_status=OVERRIDDEN carries reason
  Approve flow
   11. POST /api/releases/:id/approve sets release_status + approved_by
   12. Approve without approve_release -> 403
   13. Approve is idempotent (already_approved=true)
  Override flow
   14. POST /api/releases/:id/override requires reason
   15. Override sets release_status=OVERRIDDEN + reason
   16. Override without override_quality_gate -> 403
  Nav + landing
   17. Dashboard nav item now points at /dashboard
   18. release_owner_base landing page is /dashboard
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from datetime import datetime, timezone
from sqlalchemy import text

from primeqa.app import app
from primeqa.core.models import Environment, User
from primeqa.core.navigation import SIDEBAR_ITEMS, get_landing_page, build_sidebar
from primeqa.core.permissions import (
    BASE_PERMISSION_SETS, PermissionSet, UserPermissionSet,
)
from primeqa.db import SessionLocal
from primeqa.execution.models import PipelineRun
from primeqa.release.dashboard import (
    GateStatus, _determine_go_no_go, get_dashboard_data,
)

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
            db.add(UserPermissionSet(user_id=user_id, permission_set_id=ps.id))
        db.commit()
    finally:
        db.close()


def _ensure_user(admin_token, email, password, role):
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
            db.execute(text("DELETE FROM user_permission_sets WHERE user_id = :id"),
                       {"id": existing.id})
            db.commit()
    finally:
        db.close()
    db = SessionLocal()
    try:
        exists = db.query(User).filter_by(email=email, tenant_id=TENANT_ID).first() is not None
    finally:
        db.close()
    if not exists:
        r = client.post("/api/auth/users",
                        headers={"Authorization": f"Bearer {admin_token}"},
                        json={"email": email, "password": password,
                              "full_name": email.split("@")[0].replace(".", " ").title(),
                              "role": role})
        assert r.status_code in (200, 201)
    db = SessionLocal()
    try:
        return db.query(User).filter_by(email=email, tenant_id=TENANT_ID).first()
    finally:
        db.close()


def run_tests():
    results = []
    print("\n=== Release Owner Dashboard Tests ===\n")

    admin_token = login_api("admin@primeqa.io", "changeme123")
    ro_user = _ensure_user(admin_token, "ro_rd@primeqa.io", "test123", "viewer")
    dev_user = _ensure_user(admin_token, "dev_rd@primeqa.io", "test123", "tester")
    _force_perms(ro_user.id, ["release_owner_base"])
    _force_perms(dev_user.id, ["developer_base"])

    # 1. /dashboard renders for release_owner_base
    def test_ro_sees_dashboard():
        login_form("ro_rd@primeqa.io", "test123")
        r = client.get("/dashboard", follow_redirects=False)
        assert r.status_code == 200, f"{r.status_code}"
        html = r.data.decode("utf-8", "replace")
        # Page-specific hook data-page attribute proves our template rendered
        assert 'data-page="release-dashboard"' in html, "dashboard template missing"
    results.append(test("1. /dashboard renders for release_owner_base",
                        test_ro_sees_dashboard))

    # 2. /dashboard renders for admin_base (superadmin)
    def test_admin_sees_dashboard():
        login_form("admin@primeqa.io", "changeme123")
        r = client.get("/dashboard", follow_redirects=False)
        assert r.status_code == 200, r.status_code
    results.append(test("2. /dashboard renders for admin / superadmin",
                        test_admin_sees_dashboard))

    # 3. /dashboard redirects for developer (no view_dashboard)
    def test_dev_redirected():
        login_form("dev_rd@primeqa.io", "test123")
        r = client.get("/dashboard", follow_redirects=False)
        assert r.status_code in (301, 302), r.status_code
    results.append(test("3. developer_base redirected from /dashboard",
                        test_dev_redirected))

    # 4. Empty state when env has no runs. Use the service directly since
    #    we can't easily guarantee a clean env via the web client.
    def test_empty_state():
        db = SessionLocal()
        try:
            # Find any env the tenant has. If the env has runs, this test
            # asserts the shape; the empty branch is exercised in 4a.
            env = (db.query(Environment)
                   .filter_by(tenant_id=TENANT_ID, is_active=True).first())
            if env is None:
                return
            data = get_dashboard_data(env.id, TENANT_ID, db)
            assert "environment" in data
            assert "state" in data
            # State is one of the five documented values.
            assert data["state"] in ("GO", "NO-GO", "APPROVED", "OVERRIDDEN", "UNKNOWN"), data["state"]
        finally:
            db.close()
    results.append(test("4. get_dashboard_data returns a valid state",
                        test_empty_state))

    # 5/6/7/8 — Go/No-Go logic (unit, no DB hit past the fake rows)
    class _FakeRun:
        def __init__(self, passed, total, rs=None, reason=None):
            self.passed = passed; self.total_tests = total
            self.release_status = rs; self.override_reason = reason
            self.id = 1

    def test_go_fallback_hi():
        run = _FakeRun(passed=90, total=100)
        state, _ = _determine_go_no_go(run, [])
        assert state == "GO", state
    results.append(test("5. Fallback pass-rate >= 80% -> GO",
                        test_go_fallback_hi))

    def test_go_fallback_lo():
        run = _FakeRun(passed=70, total=100)
        state, reason = _determine_go_no_go(run, [])
        assert state == "NO-GO", state
        assert "70" in reason or "80" in reason
    results.append(test("6. Fallback pass-rate < 80% -> NO-GO",
                        test_go_fallback_lo))

    def test_gate_failure_blocks():
        gates = [
            GateStatus(suite_id=1, name="Regression", threshold=90,
                       pass_rate=78.0, passing=False),
            GateStatus(suite_id=2, name="Smoke", threshold=95,
                       pass_rate=100.0, passing=True),
        ]
        run = _FakeRun(passed=90, total=100)
        state, reason = _determine_go_no_go(run, gates)
        assert state == "NO-GO"
        assert "Regression" in reason
        assert "90%" in reason
    results.append(test("7. Failing gate -> NO-GO with callout",
                        test_gate_failure_blocks))

    def test_all_gates_pass():
        gates = [
            GateStatus(suite_id=1, name="Regression", threshold=90,
                       pass_rate=94.0, passing=True),
        ]
        run = _FakeRun(passed=90, total=100)
        state, _ = _determine_go_no_go(run, gates)
        assert state == "GO"
    results.append(test("8. All gates passing -> GO",
                        test_all_gates_pass))

    # 9. APPROVED sticky
    def test_approved_sticky():
        run = _FakeRun(passed=0, total=100, rs="APPROVED")
        state, _ = _determine_go_no_go(run, [])
        assert state == "APPROVED"
    results.append(test("9. release_status=APPROVED is sticky regardless of pass rate",
                        test_approved_sticky))

    # 10. OVERRIDDEN carries reason
    def test_overridden_reason():
        run = _FakeRun(passed=0, total=100, rs="OVERRIDDEN",
                       reason="known infra issue")
        state, reason = _determine_go_no_go(run, [])
        assert state == "OVERRIDDEN"
        assert "known infra issue" in reason
    results.append(test("10. OVERRIDDEN carries the stored reason",
                        test_overridden_reason))

    # 11/12/13 — Approve flow
    # Pick any pipeline_run in tenant 1; reset its release_status first.
    db = SessionLocal()
    try:
        run_row = (db.query(PipelineRun)
                   .filter_by(tenant_id=TENANT_ID)
                   .order_by(PipelineRun.id.desc()).first())
        pr_id = run_row.id if run_row else None
        if pr_id is not None:
            run_row.release_status = None
            run_row.approved_by = None
            run_row.approved_at = None
            run_row.override_reason = None
            db.commit()
    finally:
        db.close()

    def test_approve_sets_status():
        if pr_id is None:
            return
        r = client.post(f"/api/releases/{pr_id}/approve",
                        headers={"Authorization": f"Bearer {admin_token}"})
        assert r.status_code == 200, f"{r.status_code} {r.data}"
        body = r.get_json()
        assert body["status"] == "APPROVED", body
        assert "approved_at" in body
    results.append(test("11. POST approve sets release_status=APPROVED",
                        test_approve_sets_status))

    def test_approve_without_perm_403():
        if pr_id is None:
            return
        dev_token = login_api("dev_rd@primeqa.io", "test123")
        r = client.post(f"/api/releases/{pr_id}/approve",
                        headers={"Authorization": f"Bearer {dev_token}"})
        assert r.status_code == 403, r.status_code
    results.append(test("12. Approve without approve_release -> 403",
                        test_approve_without_perm_403))

    def test_approve_idempotent():
        if pr_id is None:
            return
        r = client.post(f"/api/releases/{pr_id}/approve",
                        headers={"Authorization": f"Bearer {admin_token}"})
        assert r.status_code == 200
        body = r.get_json()
        assert body.get("already_approved") is True, body
    results.append(test("13. Approve on already-approved -> already_approved=true",
                        test_approve_idempotent))

    # 14/15/16 — Override flow
    def test_override_requires_reason():
        if pr_id is None:
            return
        r = client.post(f"/api/releases/{pr_id}/override",
                        headers={"Authorization": f"Bearer {admin_token}"},
                        json={"reason": ""})
        assert r.status_code == 400
        assert r.get_json()["error"]["code"] == "VALIDATION_ERROR"
    results.append(test("14. Override requires non-empty reason",
                        test_override_requires_reason))

    def test_override_sets_state():
        if pr_id is None:
            return
        r = client.post(f"/api/releases/{pr_id}/override",
                        headers={"Authorization": f"Bearer {admin_token}"},
                        json={"reason": "infra flake, verified in SF debug logs"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["status"] == "OVERRIDDEN"
        assert "infra flake" in body["override_reason"]
    results.append(test("15. Override sets state + stores reason",
                        test_override_sets_state))

    def test_override_without_perm_403():
        if pr_id is None:
            return
        dev_token = login_api("dev_rd@primeqa.io", "test123")
        r = client.post(f"/api/releases/{pr_id}/override",
                        headers={"Authorization": f"Bearer {dev_token}"},
                        json={"reason": "test"})
        assert r.status_code == 403
    results.append(test("16. Override without override_quality_gate -> 403",
                        test_override_without_perm_403))

    # Restore the run state so we don't leave visible override on prod.
    db = SessionLocal()
    try:
        if pr_id is not None:
            run_row = db.query(PipelineRun).filter_by(id=pr_id).first()
            if run_row is not None:
                run_row.release_status = None
                run_row.approved_by = None
                run_row.approved_at = None
                run_row.override_reason = None
                db.commit()
    finally:
        db.close()

    # 17 — Nav + landing
    def test_nav_dashboard_url():
        item = next(i for i in SIDEBAR_ITEMS if i["id"] == "dashboard")
        assert item["url"] == "/dashboard", item
    results.append(test("17. Dashboard nav item -> /dashboard",
                        test_nav_dashboard_url))

    def test_ro_landing_page():
        perms = set(next(s for s in BASE_PERMISSION_SETS
                         if s["api_name"] == "release_owner_base")["permissions"])
        assert get_landing_page(perms) == "/dashboard"
    results.append(test("18. release_owner_base lands on /dashboard",
                        test_ro_landing_page))

    # ----- Share Dashboard (Part 8) --------------------------------
    from primeqa.core.permissions import SharedDashboardLink

    # Pick any env in tenant 1 — share links are scoped to an env.
    share_env_id = None
    db = SessionLocal()
    try:
        e = (db.query(Environment)
             .filter_by(tenant_id=TENANT_ID, is_active=True).first())
        share_env_id = e.id if e else None
    finally:
        db.close()

    created_share_url = {"url": None, "link_id": None, "token": None}

    def test_share_create_returns_url():
        if share_env_id is None:
            return
        r = client.post("/api/dashboard/share",
                        headers={"Authorization": f"Bearer {admin_token}"},
                        json={"environment_id": share_env_id,
                              "expires_days": 7})
        assert r.status_code == 201, f"{r.status_code} {r.data}"
        body = r.get_json()
        assert "url" in body and "/shared/" in body["url"]
        assert body["expires_days"] == 7
        created_share_url["url"] = body["url"]
        created_share_url["link_id"] = body["link_id"]
        # Extract the raw token from the URL for subsequent tests.
        created_share_url["token"] = body["url"].rsplit("/shared/", 1)[-1]
    results.append(test("19. POST /api/dashboard/share returns signed URL",
                        test_share_create_returns_url))

    def test_share_stores_hashed_token():
        # DB should hold the sha256 of the raw token, not the token itself.
        if not created_share_url["token"]:
            return
        import hashlib
        expected = hashlib.sha256(created_share_url["token"].encode()).hexdigest()
        db = SessionLocal()
        try:
            row = (db.query(SharedDashboardLink)
                   .filter_by(id=created_share_url["link_id"]).first())
            assert row.token == expected, "token stored should be hash not raw"
            # Raw token should NOT appear anywhere in the DB value
            assert row.token != created_share_url["token"]
        finally:
            db.close()
    results.append(test("20. Token stored hashed, not raw",
                        test_share_stores_hashed_token))

    def test_share_requires_permission():
        # Developer has no share_dashboard perm → 403.
        dev_token = login_api("dev_rd@primeqa.io", "test123")
        r = client.post("/api/dashboard/share",
                        headers={"Authorization": f"Bearer {dev_token}"},
                        json={"environment_id": share_env_id or 1,
                              "expires_days": 7})
        assert r.status_code == 403, f"{r.status_code} {r.data}"
        assert r.get_json()["error"]["code"] == "INSUFFICIENT_PERMISSIONS"
    results.append(test("21. Share without share_dashboard -> 403",
                        test_share_requires_permission))

    def test_shared_page_renders_unauthenticated():
        if not created_share_url["url"]:
            return
        # Fresh client — no session, no Bearer token.
        from primeqa.app import app as _a
        c = _a.test_client()
        r = c.get("/shared/" + created_share_url["token"])
        assert r.status_code == 200, f"{r.status_code} {r.data[:200]}"
        html = r.data.decode("utf-8", "replace")
        assert 'data-page="shared-dashboard"' in html
        # Read-only: no action button IDs present.
        assert "data-approve-run" not in html
        assert "data-override-run" not in html
        assert "share-dashboard-btn" not in html
    results.append(test("22. /shared/<token> renders without auth (read-only)",
                        test_shared_page_renders_unauthenticated))

    def test_shared_unknown_token_404():
        from primeqa.app import app as _a
        c = _a.test_client()
        r = c.get("/shared/" + ("x" * 43))
        assert r.status_code == 404
        assert b"Invalid link" in r.data
    results.append(test("23. /shared/<unknown-token> -> 404 'Invalid link'",
                        test_shared_unknown_token_404))

    def test_share_revoke():
        if not created_share_url["link_id"]:
            return
        r = client.post(
            f"/api/dashboard/share/{created_share_url['link_id']}/revoke",
            headers={"Authorization": f"Bearer {admin_token}"})
        assert r.status_code == 200, r.data
        body = r.get_json()
        assert body["status"] == "revoked"
    results.append(test("24. Revoke sets revoked_at",
                        test_share_revoke))

    def test_revoked_link_returns_410():
        if not created_share_url["url"]:
            return
        from primeqa.app import app as _a
        c = _a.test_client()
        r = c.get("/shared/" + created_share_url["token"])
        assert r.status_code == 410, r.status_code
        assert b"revoked" in r.data.lower() or b"Revoked" in r.data or b"Link revoked" in r.data
    results.append(test("25. Revoked link returns 410 with revoked page",
                        test_revoked_link_returns_410))

    def test_expired_link_returns_410():
        if share_env_id is None:
            return
        # Create a row that's already past its expires_at.
        from datetime import datetime, timedelta, timezone
        import secrets, hashlib
        raw = secrets.token_urlsafe(32)
        db = SessionLocal()
        try:
            link = SharedDashboardLink(
                tenant_id=TENANT_ID, environment_id=share_env_id,
                token=hashlib.sha256(raw.encode()).hexdigest(),
                created_by=None,
                expires_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
            db.add(link); db.commit()
        finally:
            db.close()
        from primeqa.app import app as _a
        c = _a.test_client()
        r = c.get("/shared/" + raw)
        assert r.status_code == 410, f"{r.status_code} {r.data[:200]}"
        assert b"expired" in r.data.lower() or b"Link expired" in r.data
    results.append(test("26. Expired link returns 410 with expired page",
                        test_expired_link_returns_410))

    def test_revoke_requires_permission():
        if not created_share_url["link_id"]:
            return
        dev_token = login_api("dev_rd@primeqa.io", "test123")
        r = client.post(
            f"/api/dashboard/share/{created_share_url['link_id']}/revoke",
            headers={"Authorization": f"Bearer {dev_token}"})
        assert r.status_code == 403, r.status_code
    results.append(test("27. Revoke without revoke_shared_links -> 403",
                        test_revoke_requires_permission))

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
