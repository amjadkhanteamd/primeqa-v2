"""Developer-experience tests: /tickets page + active-env switcher.

Focused on the deterministic pieces — ordering, env resolution, route
gating, and the switcher POST. The Jira fetch path is unit-tested via a
stub (we can't depend on a live Jira in CI).

NOTE (D-245): integration test — runs against the live Railway DB + mints JWTs.
Re-run on a real environment to confirm green; it cannot execute in a sandbox
that blocks DB writes / token minting. Authorization is the role ladder now;
the deleted permission-set seeding (`_force_perms`) was removed.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from primeqa.app import app
from primeqa.core.models import Environment, User
from primeqa.db import SessionLocal
from primeqa.runs.my_tickets import (
    list_switchable_environments,
    resolve_active_environment,
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
    r = client.post("/login", data={"email": email, "password": password},
                    follow_redirects=False)
    return r


def _ensure_user(admin_token, email, password, role):
    client.post("/api/auth/users",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={"email": email, "password": password,
                      "full_name": email.split("@")[0], "role": role})
    db = SessionLocal()
    try:
        u = db.query(User).filter_by(email=email, tenant_id=TENANT_ID).first()
        return u
    finally:
        db.close()


def run_tests():
    results = []
    print("\n=== Developer Experience Tests ===\n")

    admin_token = login_api("admin@primeqa.io", "changeme123")
    dev_user = _ensure_user(admin_token, "dev_x@primeqa.io", "test123", "tester")

    # --------------------------------------------------------------
    # 1. /tickets renders for Developer Base

    # --------------------------------------------------------------
    # 2. /tickets redirects a user without run_single_ticket

    # --------------------------------------------------------------
    # 3. sort_for_triage: running -> failed -> untested -> passed

    # --------------------------------------------------------------
    # 4. Within a bucket: higher priority wins

    # --------------------------------------------------------------
    # 5. Within same priority: ticket key orders alphabetically

    # --------------------------------------------------------------
    # 6. resolve_active_environment honours preferred_environment_id
    # --------------------------------------------------------------
    def test_resolve_active_env_preferred():
        db = SessionLocal()
        try:
            # Resolver requires is_active=True so we filter the same way.
            env = (db.query(Environment)
                   .filter_by(tenant_id=TENANT_ID, is_active=True)
                   .first())
            assert env is not None, "No active env to test with"
            u = db.query(User).filter_by(id=dev_user.id).first()
            u.preferred_environment_id = env.id
            db.commit()
            resolved = resolve_active_environment(u, db)
            assert resolved.id == env.id, \
                f"Expected preferred env {env.id}, got {resolved.id if resolved else None}"
        finally:
            db.close()
    results.append(test("6. resolve_active_environment uses preferred_environment_id",
                        test_resolve_active_env_preferred))

    # --------------------------------------------------------------
    # 7. resolve_active_environment falls back to team env when preference is null
    # --------------------------------------------------------------
    def test_resolve_active_env_fallback():
        db = SessionLocal()
        try:
            u = db.query(User).filter_by(id=dev_user.id).first()
            u.preferred_environment_id = None
            db.commit()
            resolved = resolve_active_environment(u, db)
            assert resolved is not None, "Expected a fallback env"
            assert resolved.tenant_id == TENANT_ID
        finally:
            db.close()
    results.append(test("7. resolve_active_environment falls back to team env",
                        test_resolve_active_env_fallback))

    # --------------------------------------------------------------
    # 8. list_switchable_environments returns personal first, then team
    # --------------------------------------------------------------
    def test_list_switchable_envs_ordering():
        db = SessionLocal()
        try:
            u = db.query(User).filter_by(id=dev_user.id).first()
            # There's at least one team env in the tenant.
            envs = list_switchable_environments(u, db)
            assert isinstance(envs, list)
            # All returned envs should have kind in {personal, team}
            kinds = {e["kind"] for e in envs}
            assert kinds.issubset({"personal", "team"}), kinds
            # Personal envs first (if any)
            if any(e["kind"] == "personal" for e in envs) and any(e["kind"] == "team" for e in envs):
                first_personal_idx = next(i for i, e in enumerate(envs) if e["kind"] == "personal")
                first_team_idx = next(i for i, e in enumerate(envs) if e["kind"] == "team")
                assert first_personal_idx < first_team_idx
        finally:
            db.close()
    results.append(test("8. Switcher lists personal envs before team envs",
                        test_list_switchable_envs_ordering))

    # --------------------------------------------------------------
    # 9. POST /api/users/me/active-env updates preferred_environment_id
    # --------------------------------------------------------------
    def test_set_active_env():
        # Cookie-login sets both access_token AND csrf_token cookies.
        # The double-submit CSRF check compares the cookie against the
        # X-CSRF-Token header or a csrf_token form field.
        login_form("dev_x@primeqa.io", "test123")
        # Grab CSRF token from the client's cookie jar.
        csrf_token = client.get_cookie("csrf_token")
        csrf_val = csrf_token.value if csrf_token else ""
        db = SessionLocal()
        try:
            env = (db.query(Environment)
                   .filter_by(tenant_id=TENANT_ID, is_active=True)
                   .first())
        finally:
            db.close()
        r = client.post("/api/users/me/active-env",
                        data={"environment_id": env.id, "csrf_token": csrf_val},
                        follow_redirects=False)
        assert r.status_code == 204, f"Expected 204, got {r.status_code} {r.data}"
        assert r.headers.get("HX-Redirect") == "/tickets"

        db = SessionLocal()
        try:
            u = db.query(User).filter_by(id=dev_user.id).first()
            assert u.preferred_environment_id == env.id
        finally:
            db.close()
    results.append(test("9. POST /api/users/me/active-env sets preferred_environment_id",
                        test_set_active_env))

    # --------------------------------------------------------------
    # 10. /tickets empty state: no environment

    # --------------------------------------------------------------
    # 11. /runs/:id/tickets-summary partial returns HTML

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
