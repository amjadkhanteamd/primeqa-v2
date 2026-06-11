"""Tester's /run page + /api/bulk-runs API tests.

The /run page is a simplified wrapper over the existing pipeline-run
infrastructure — one click produces one pipeline_run row. The Run
Wizard at /runs/new is the richer mixed-source path; these tests
cover the new focused page + API only.

Covers:
  1. /run renders for tester (has run_sprint)
  2. /run redirects for developer (no bulk perms)
  3. /run hides tabs the user doesn't have perm for
  4. POST /api/bulk-runs rejects unknown run_type
  5. POST /api/bulk-runs rejects missing environment_id
  6. POST /api/bulk-runs 404 on unknown environment
  7. POST /api/bulk-runs requires bulk_run perm for sprint
  8. POST /api/bulk-runs blocks when env.allow_bulk_run=false
  9. POST /api/bulk-runs blocks production without confirm
 10. POST /api/bulk-runs fails with NO_TESTS on unknown ticket keys
 11. POST /api/bulk-runs (sprint) creates a pipeline_run on valid input
 12. GET  /api/bulk-runs/:id/status returns per-ticket payload
 13. POST /api/bulk-runs/:id/cancel sets status = cancelled
 14. POST /api/bulk-runs/:id/cancel rejects non-owner non-admin
 15. Navigation + landing page updated to /run (tester lands there)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text

from primeqa.app import app
from primeqa.core.models import Environment, User
from primeqa.core.navigation import get_landing_page
from primeqa.core.permissions import (
    BASE_PERMISSION_SETS, PermissionSet, UserPermissionSet,
)
from primeqa.db import SessionLocal
from primeqa.runs.bulk import (
    environment_can_bulk_run,
    ticket_keys_to_test_case_ids,
    suite_to_test_case_ids,
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
            assert ps is not None, f"PermissionSet {name!r} missing"
            db.add(UserPermissionSet(user_id=user_id, permission_set_id=ps.id))
        db.commit()
    finally:
        db.close()


def _ensure_user(admin_token, email, password, role):
    """Return a user row with a known password + role.

    Reset-in-place rather than delete-and-recreate — the user may be
    referenced by pipeline_runs.triggered_by FK from earlier happy-path
    test runs, and those rows aren't ours to purge.
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
    # Create path for genuinely-new users.
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
        assert r.status_code in (200, 201), f"create user failed: {r.status_code} {r.data[:200]}"
    # Fresh session-bound instance. Caller should read .id immediately
    # (session closes here); all callers in this test do exactly that.
    db = SessionLocal()
    try:
        return db.query(User).filter_by(email=email, tenant_id=TENANT_ID).first()
    finally:
        db.close()


def run_tests():
    results = []
    print("\n=== Run Tests Page + /api/bulk-runs ===\n")

    admin_token = login_api("admin@primeqa.io", "changeme123")
    tester_user = _ensure_user(admin_token, "tester_rt@primeqa.io", "test123", "tester")
    dev_user = _ensure_user(admin_token, "dev_rt@primeqa.io", "test123", "tester")
    _force_perms(tester_user.id, ["tester_base"])
    _force_perms(dev_user.id, ["developer_base"])

    # --- Page render ---
    def test_run_renders_for_tester():
        # Re-force perms right before the check. Concurrent chain runs
        # can mutate the tester's perms between the top-of-suite setup
        # and this test firing. The contract we're verifying is "a
        # tester_base holder sees /run" — so anchor that at test time.
        _force_perms(tester_user.id, ["tester_base"])
        login_form("tester_rt@primeqa.io", "test123")
        r = client.get("/run", follow_redirects=False)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        html = r.data.decode("utf-8", "replace")
        assert "Run Tests" in html, "Page title missing"
        # D-219: the substrate page — requirement picker, not the v1 tabs.
        assert ("requirement_keys" in html or "Nothing approved" in html), \
            "Substrate requirement picker (or its empty state) missing"
        assert 'data-mode="sprint"' not in html, "v1 mode tabs should be gone"
    results.append(test("1. /run renders the substrate run page",
                        test_run_renders_for_tester))

    def test_run_redirects_for_developer():
        login_form("dev_rt@primeqa.io", "test123")
        r = client.get("/run", follow_redirects=False)
        assert r.status_code in (301, 302), f"Expected redirect, got {r.status_code}"
        # developer_base -> /requirements
        assert "/requirements" in r.headers["Location"], r.headers["Location"]
    results.append(test("2. /run redirects developer (no bulk perms)",
                        test_run_redirects_for_developer))

    def test_run_excludes_production_envs():
        # D-219: the env picker offers sandboxes only.
        _force_perms(tester_user.id, ["tester_base"])
        login_form("tester_rt@primeqa.io", "test123")
        r = client.get("/run")
        html = r.data.decode("utf-8", "replace")
        from primeqa.core.models import Environment
        db = SessionLocal()
        try:
            prods = (db.query(Environment)
                     .filter_by(tenant_id=TENANT_ID, is_production=True)
                     .all())
        finally:
            db.close()
        for p_env in prods:
            assert f'<option value="{p_env.id}"' not in html, \
                f"production env {p_env.id} offered on /run"
    results.append(test("3. /run excludes production environments",
                        test_run_excludes_production_envs))

    # --- (the /api/bulk-runs contract tests retired with the endpoint, D-221 R2) ---

    def test_tester_lands_on_run_page():
        # Tester base lands on /run (not /runs/new) per the spec.
        perms = set(next(s for s in BASE_PERMISSION_SETS if s["api_name"] == "tester_base")["permissions"])
        assert get_landing_page(perms) == "/requirements"
    results.append(test("15. Tester base landing page is /requirements (D-218)",
                        test_tester_lands_on_run_page))

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
