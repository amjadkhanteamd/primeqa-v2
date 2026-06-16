"""Tester's /run page tests (substrate requirement-picker, D-219).

The v1 four-mode /run page + /api/bulk-runs API these tests originally
covered were retired with the v1 engine (D-221 R2); the /run page was
rebuilt as the substrate requirement-picker (D-219). What remains:

  1. /run renders for a Member (tester role — has bulk-run)
  2. /run redirects for a Viewer (viewer role — below Member)
  3. /run excludes production envs (sandbox-only picker)
  4. Landing page: a tester (Member) lands on /requirements

D-245: authorization is the role ladder now (viewer<member<admin<superadmin
via primeqa.core.authz.rank); the deleted permission-set seeding (`_force_perms`)
is gone. Access on /run gates at Tier.MEMBER, so the "no access" case is a
`viewer`-role user (the tier below Member), redirected to `/` by require_tier.

NOTE (D-245): integration test — runs against the live Railway DB + mints JWTs.
Re-run on a real environment to confirm green; it cannot execute in a sandbox
that blocks DB writes / token minting.
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
from primeqa.core.permissions import _role_capabilities
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


def _ensure_user(admin_token, email, password, role):
    """Return a user row with a known password + role.

    Reset-in-place rather than delete-and-recreate — the user may be
    referenced by pipeline_runs.triggered_by FK from earlier happy-path
    test runs, and those rows aren't ours to purge. D-245: role IS the
    authorization tier now — no permission-set seeding.
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
    print("\n=== Run Tests Page (substrate requirement-picker, D-219/D-245) ===\n")

    admin_token = login_api("admin@primeqa.io", "changeme123")
    # tester role -> Member tier (has bulk-run); viewer role -> below Member.
    tester_user = _ensure_user(admin_token, "tester_rt@primeqa.io", "test123", "tester")
    _ensure_user(admin_token, "dev_rt@primeqa.io", "test123", "viewer")

    # --- Page render ---
    def test_run_renders_for_tester():
        login_form("tester_rt@primeqa.io", "test123")
        r = client.get("/run", follow_redirects=False)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        html = r.data.decode("utf-8", "replace")
        assert "Run Tests" in html, "Page title missing"
        # D-219: the substrate page — requirement picker, not the v1 tabs.
        assert ("requirement_keys" in html or "Nothing approved" in html), \
            "Substrate requirement picker (or its empty state) missing"
        assert 'data-mode="sprint"' not in html, "v1 mode tabs should be gone"
    results.append(test("1. /run renders the substrate run page (tester=Member)",
                        test_run_renders_for_tester))

    def test_run_redirects_for_viewer():
        # D-245: /run gates at Tier.MEMBER; a viewer is below it, so
        # require_tier redirects to "/".
        login_form("dev_rt@primeqa.io", "test123")
        r = client.get("/run", follow_redirects=False)
        assert r.status_code in (301, 302), f"Expected redirect, got {r.status_code}"
        assert r.headers["Location"].endswith("/"), r.headers["Location"]
    results.append(test("2. /run redirects a Viewer (below Member tier)",
                        test_run_redirects_for_viewer))

    def test_run_excludes_production_envs():
        # D-219: the env picker offers sandboxes only.
        login_form("tester_rt@primeqa.io", "test123")
        r = client.get("/run")
        html = r.data.decode("utf-8", "replace")
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

    def test_tester_lands_on_requirements():
        # D-218/D-245: a Member (tester) lands on /requirements. Capabilities
        # are role-derived now — feed get_landing_page from _role_capabilities.
        assert get_landing_page(_role_capabilities("tester")) == "/requirements"
    results.append(test("4. Tester (Member) landing page is /requirements (D-218)",
                        test_tester_lands_on_requirements))

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
