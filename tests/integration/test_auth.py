"""Integration tests for the auth module.

Tests against the real Railway PostgreSQL database using the seeded admin user.
"""

import json
import sys
import os
import uuid

# wave-0: moved under tests/integration/ so pytest collects it (testpaths).
# One extra dirname keeps the repo root on sys.path for `python tests/integration/…` too.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv
load_dotenv()

from primeqa.app import app

client = app.test_client()

ADMIN_EMAIL = "admin@primeqa.io"
ADMIN_PASSWORD = "changeme123"
TENANT_ID = 1

# TEST-4 (wave-0): a per-run unique suffix so two concurrent runs never collide
# on the fixture emails. Previously the suite used fixed emails (tester@,
# userN@, overflow@primeqa.io) against the shared Railway tenant_1 — two runs
# raced the SAME rows and the SAME 20-user cap, making the suite order-dependent
# and flaky. Every fixture email now carries this suffix, and teardown deletes
# only THIS run's users (never a concurrent run's).
RUN = uuid.uuid4().hex[:8]
TESTER_EMAIL = f"tester-{RUN}@primeqa.io"

admin_tokens = {}
tester_tokens = {}
created_user_ids = []


def _run(name, fn):
    try:
        fn()
        print(f"  PASS  {name}")
        return True
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        return False
    except Exception as e:
        print(f"  ERROR {name}: {type(e).__name__}: {e}")
        return False


def run_tests():
    results = []
    print("\n=== Auth Module Tests ===\n")

    # Setup: clear any of THIS run's fixture users (the -{RUN}@primeqa.io
    # suffix) in case a prior invocation reused this process. A fresh RUN
    # normally makes this a no-op; tests 8+ create these and depend on them
    # not pre-existing for this run's suffix.
    _teardown_auth_test_users()

    # 1. Login with seeded admin
    def test_admin_login():
        r = client.post("/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD,
            "tenant_id": TENANT_ID,
        })
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.data}"
        data = r.get_json()
        assert "access_token" in data, "Missing access_token"
        assert "refresh_token" in data, "Missing refresh_token"
        assert data["user"]["email"] == ADMIN_EMAIL
        # admin@primeqa.io was promoted to `superadmin` in migration 017
        # — keep the assertion loose so it works either way.
        assert data["user"]["role"] in ("admin", "superadmin"), data["user"]["role"]
        admin_tokens["access"] = data["access_token"]
        admin_tokens["refresh"] = data["refresh_token"]
    results.append(_run("1. Admin login returns access_token and refresh_token", test_admin_login))

    # 2. Login with wrong password
    def test_bad_password():
        r = client.post("/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": "wrongpassword",
            "tenant_id": TENANT_ID,
        })
        assert r.status_code == 401, f"Expected 401, got {r.status_code}"
    results.append(_run("2. Login with wrong password returns 401", test_bad_password))

    # 3. /api/auth/me with valid token
    def test_me_valid():
        r = client.get("/api/auth/me", headers={
            "Authorization": f"Bearer {admin_tokens['access']}"
        })
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.data}"
        data = r.get_json()
        assert data["email"] == ADMIN_EMAIL
        assert data["role"] in ("admin", "superadmin"), data["role"]
    results.append(_run("3. GET /api/auth/me with valid token returns user info", test_me_valid))

    # 4. /api/auth/me with invalid token
    def test_me_invalid():
        r = client.get("/api/auth/me", headers={
            "Authorization": "Bearer invalidtoken123"
        })
        assert r.status_code == 401, f"Expected 401, got {r.status_code}"
    results.append(_run("4. GET /api/auth/me with invalid token returns 401", test_me_invalid))

    # 5. /api/auth/me without token
    def test_me_no_token():
        r = client.get("/api/auth/me")
        assert r.status_code == 401, f"Expected 401, got {r.status_code}"
    results.append(_run("5. GET /api/auth/me without token returns 401", test_me_no_token))

    # 6. Token refresh
    def test_refresh():
        r = client.post("/api/auth/refresh", json={
            "refresh_token": admin_tokens["refresh"],
        })
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.data}"
        data = r.get_json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["refresh_token"] != admin_tokens["refresh"], "New refresh token should differ"
        admin_tokens["access"] = data["access_token"]
        admin_tokens["refresh"] = data["refresh_token"]
    results.append(_run("6. Token refresh returns new tokens", test_refresh))

    # 7. Old refresh token is revoked after rotation
    def test_old_refresh_revoked():
        old_refresh = admin_tokens["refresh"]
        r = client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        data = r.get_json()
        admin_tokens["access"] = data["access_token"]
        admin_tokens["refresh"] = data["refresh_token"]

        r2 = client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
        assert r2.status_code == 401, f"Expected 401 for reused refresh token, got {r2.status_code}"
    results.append(_run("7. Old refresh token is revoked after rotation", test_old_refresh_revoked))

    # 8. Create a tester user (admin only)
    def test_create_tester():
        r = client.post("/api/auth/users", headers={
            "Authorization": f"Bearer {admin_tokens['access']}"
        }, json={
            "email": TESTER_EMAIL,
            "password": "tester123",
            "full_name": "Test User",
            "role": "tester",
        })
        assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.data}"
        data = r.get_json()
        assert data["role"] == "tester"
        assert data["email"] == TESTER_EMAIL
        created_user_ids.append(data["id"])
    results.append(_run("8. Admin can create a tester user", test_create_tester))

    # 9. Tester login
    def test_tester_login():
        r = client.post("/api/auth/login", json={
            "email": TESTER_EMAIL,
            "password": "tester123",
            "tenant_id": TENANT_ID,
        })
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        data = r.get_json()
        assert data["user"]["role"] == "tester"
        tester_tokens["access"] = data["access_token"]
        tester_tokens["refresh"] = data["refresh_token"]
    results.append(_run("9. Tester can log in", test_tester_login))

    # 10. Tester blocked from admin endpoints
    def test_tester_blocked():
        r = client.get("/api/auth/users", headers={
            "Authorization": f"Bearer {tester_tokens['access']}"
        })
        assert r.status_code == 403, f"Expected 403, got {r.status_code}"
    results.append(_run("10. Tester blocked from admin-only GET /api/auth/users", test_tester_blocked))

    # 11. Tester blocked from creating users
    def test_tester_cant_create():
        r = client.post("/api/auth/users", headers={
            "Authorization": f"Bearer {tester_tokens['access']}"
        }, json={
            "email": "hacker@evil.com",
            "password": "hack123",
            "full_name": "Hacker",
            "role": "admin",
        })
        assert r.status_code == 403, f"Expected 403, got {r.status_code}"
    results.append(_run("11. Tester blocked from POST /api/auth/users", test_tester_cant_create))

    # 12. Admin can list users
    def test_list_users():
        r = client.get("/api/auth/users", headers={
            "Authorization": f"Bearer {admin_tokens['access']}"
        })
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        data = r.get_json()
        assert len(data) >= 2, f"Expected at least 2 users, got {len(data)}"
    results.append(_run("12. Admin can list users", test_list_users))

    # 13. Duplicate email rejected
    def test_duplicate_email():
        r = client.post("/api/auth/users", headers={
            "Authorization": f"Bearer {admin_tokens['access']}"
        }, json={
            "email": TESTER_EMAIL,
            "password": "dup123",
            "full_name": "Duplicate",
            "role": "tester",
        })
        assert r.status_code == 409, f"Expected 409, got {r.status_code}"
    results.append(_run("13. Duplicate email rejected", test_duplicate_email))

    # 14. user-cap enforcement — derived from the tenant's ACTUAL current count
    # (no clean-tenant assumption). Create uuid-suffixed users until the cap is
    # hit; every create must be a 201 (under cap) or a 409 'maximum' (at cap),
    # and the cap MUST be reached within a generous bound. Robust to whatever
    # the tenant's pre-existing active-user count is, and to concurrent runs.
    def test_user_limit():
        hit_cap = False
        for i in range(60):  # generous upper bound, larger than any real cap
            r = client.post("/api/auth/users", headers={
                "Authorization": f"Bearer {admin_tokens['access']}"
            }, json={
                "email": f"user{i}-{RUN}@primeqa.io",
                "password": "pass123",
                "full_name": f"User {i}",
                "role": "viewer",
            })
            if r.status_code == 201:
                created_user_ids.append(r.get_json()["id"])
                continue
            # First non-201 must be the cap rejection (envelope:
            # {"error": {"code": ..., "message": ...}}).
            assert r.status_code == 409, \
                f"create #{i} expected 201 or 409, got {r.status_code}: {r.data[:200]}"
            err = r.get_json().get("error", {})
            msg = err.get("message", "") if isinstance(err, dict) else str(err)
            assert "maximum" in msg.lower(), \
                f"Expected user-cap 'maximum' error, got: {r.data}"
            hit_cap = True
            break
        assert hit_cap, "user cap was never enforced after 60 creates"
    results.append(_run("14. user cap enforced (derived from live count)", test_user_limit))

    # 15. Logout revokes all tokens
    def test_logout():
        r = client.post("/api/auth/logout", headers={
            "Authorization": f"Bearer {tester_tokens['access']}"
        })
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"

        r2 = client.post("/api/auth/refresh", json={
            "refresh_token": tester_tokens["refresh"],
        })
        assert r2.status_code == 401, f"Expected 401 after logout, got {r2.status_code}"
    results.append(_run("15. Logout revokes refresh tokens", test_logout))

    # Teardown — delete all test-created user artifacts so future runs
    # don't saturate the 20-user tenant cap. Without this, the DB
    # accumulates userN@primeqa.io rows on every run and eventually
    # every test after #14 fails with TENANT_CAP.
    _teardown_auth_test_users()

    # Summary
    passed = sum(results)
    total = len(results)
    print(f"\n{'='*40}")
    print(f"Results: {passed}/{total} passed")
    if passed == total:
        print("ALL TESTS PASSED")
    else:
        print(f"{total - passed} test(s) FAILED")
    print()

    return passed == total


def _teardown_auth_test_users():
    """Delete THIS run's fixture users (all carry the ``-{RUN}@primeqa.io``
    suffix: tester-, userN-, overflow-) so they don't saturate the 20-user cap.

    Scoped to the current RUN suffix — a concurrent run's users are NEVER
    touched (the wave-0 TEST-4 race fix; the old broad regex deleted a
    concurrent run's rows mid-flight).

    Idempotent; raw SQL so it doesn't need the app's session. Also used as
    setup cleanup at the top of run_tests().

    NOTE (residual): a run that CRASHES before teardown leaves its ~cap-worth
    of ``-{oldRUN}@primeqa.io`` users behind. The cap test above tolerates that
    (it derives from the live count), but a periodic ops sweep of stale
    ``%-________@primeqa.io`` test users is the real answer — out of scope here.
    """
    try:
        import os
        import psycopg2
        url = os.environ.get("DATABASE_URL")
        if not url:
            return
        like = f"%-{RUN}@primeqa.io"
        conn = psycopg2.connect(url)
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM refresh_tokens WHERE user_id IN ("
                    "SELECT id FROM users WHERE email LIKE %s)",
                    (like,))
                cur.execute(
                    "DELETE FROM users WHERE email LIKE %s", (like,))
        conn.close()
    except Exception as e:
        # Best-effort — don't fail the test run if it errors.
        print(f"  (teardown warning: {e})")


def test_auth_suite():
    # wave-0: pytest entry point for the auth invariant suite (login, token
    # rotation, /me gating, role gating, user-cap). The sub-checks are nested
    # inside run_tests(), so this single collected test is how the suite runs +
    # fails under pytest. Assertions unchanged; the TEST-4 race fixes (per-run
    # uuid emails + live-count-derived cap) are already applied above.
    assert run_tests()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
