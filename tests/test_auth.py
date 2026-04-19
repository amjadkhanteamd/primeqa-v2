"""Integration tests for the auth module.

Tests against the real Railway PostgreSQL database using the seeded admin user.
"""

import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from primeqa.app import app

client = app.test_client()

ADMIN_EMAIL = "admin@primeqa.io"
ADMIN_PASSWORD = "changeme123"
TENANT_ID = 1

admin_tokens = {}
tester_tokens = {}
created_user_ids = []


def test(name, fn):
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
    results.append(test("1. Admin login returns access_token and refresh_token", test_admin_login))

    # 2. Login with wrong password
    def test_bad_password():
        r = client.post("/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": "wrongpassword",
            "tenant_id": TENANT_ID,
        })
        assert r.status_code == 401, f"Expected 401, got {r.status_code}"
    results.append(test("2. Login with wrong password returns 401", test_bad_password))

    # 3. /api/auth/me with valid token
    def test_me_valid():
        r = client.get("/api/auth/me", headers={
            "Authorization": f"Bearer {admin_tokens['access']}"
        })
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.data}"
        data = r.get_json()
        assert data["email"] == ADMIN_EMAIL
        assert data["role"] in ("admin", "superadmin"), data["role"]
    results.append(test("3. GET /api/auth/me with valid token returns user info", test_me_valid))

    # 4. /api/auth/me with invalid token
    def test_me_invalid():
        r = client.get("/api/auth/me", headers={
            "Authorization": "Bearer invalidtoken123"
        })
        assert r.status_code == 401, f"Expected 401, got {r.status_code}"
    results.append(test("4. GET /api/auth/me with invalid token returns 401", test_me_invalid))

    # 5. /api/auth/me without token
    def test_me_no_token():
        r = client.get("/api/auth/me")
        assert r.status_code == 401, f"Expected 401, got {r.status_code}"
    results.append(test("5. GET /api/auth/me without token returns 401", test_me_no_token))

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
    results.append(test("6. Token refresh returns new tokens", test_refresh))

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
    results.append(test("7. Old refresh token is revoked after rotation", test_old_refresh_revoked))

    # 8. Create a tester user (admin only)
    def test_create_tester():
        r = client.post("/api/auth/users", headers={
            "Authorization": f"Bearer {admin_tokens['access']}"
        }, json={
            "email": "tester@primeqa.io",
            "password": "tester123",
            "full_name": "Test User",
            "role": "tester",
        })
        assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.data}"
        data = r.get_json()
        assert data["role"] == "tester"
        assert data["email"] == "tester@primeqa.io"
        created_user_ids.append(data["id"])
    results.append(test("8. Admin can create a tester user", test_create_tester))

    # 9. Tester login
    def test_tester_login():
        r = client.post("/api/auth/login", json={
            "email": "tester@primeqa.io",
            "password": "tester123",
            "tenant_id": TENANT_ID,
        })
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        data = r.get_json()
        assert data["user"]["role"] == "tester"
        tester_tokens["access"] = data["access_token"]
        tester_tokens["refresh"] = data["refresh_token"]
    results.append(test("9. Tester can log in", test_tester_login))

    # 10. Tester blocked from admin endpoints
    def test_tester_blocked():
        r = client.get("/api/auth/users", headers={
            "Authorization": f"Bearer {tester_tokens['access']}"
        })
        assert r.status_code == 403, f"Expected 403, got {r.status_code}"
    results.append(test("10. Tester blocked from admin-only GET /api/auth/users", test_tester_blocked))

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
    results.append(test("11. Tester blocked from POST /api/auth/users", test_tester_cant_create))

    # 12. Admin can list users
    def test_list_users():
        r = client.get("/api/auth/users", headers={
            "Authorization": f"Bearer {admin_tokens['access']}"
        })
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        data = r.get_json()
        assert len(data) >= 2, f"Expected at least 2 users, got {len(data)}"
    results.append(test("12. Admin can list users", test_list_users))

    # 13. Duplicate email rejected
    def test_duplicate_email():
        r = client.post("/api/auth/users", headers={
            "Authorization": f"Bearer {admin_tokens['access']}"
        }, json={
            "email": "tester@primeqa.io",
            "password": "dup123",
            "full_name": "Duplicate",
            "role": "tester",
        })
        assert r.status_code == 409, f"Expected 409, got {r.status_code}"
    results.append(test("13. Duplicate email rejected", test_duplicate_email))

    # 14. 20-user limit enforcement
    def test_user_limit():
        for i in range(18):
            r = client.post("/api/auth/users", headers={
                "Authorization": f"Bearer {admin_tokens['access']}"
            }, json={
                "email": f"user{i}@primeqa.io",
                "password": "pass123",
                "full_name": f"User {i}",
                "role": "viewer",
            })
            if r.status_code == 201:
                created_user_ids.append(r.get_json()["id"])
            elif r.status_code == 409:
                assert "maximum" in r.get_json().get("error", "").lower(), \
                    f"Expected user limit error, got: {r.data}"
                break

        r = client.post("/api/auth/users", headers={
            "Authorization": f"Bearer {admin_tokens['access']}"
        }, json={
            "email": "overflow@primeqa.io",
            "password": "pass123",
            "full_name": "Overflow",
            "role": "viewer",
        })
        assert r.status_code == 409, f"Expected 409 (limit reached), got {r.status_code}: {r.data}"
        assert "maximum" in r.get_json().get("error", "").lower()
    results.append(test("14. 20-user limit enforced", test_user_limit))

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
    results.append(test("15. Logout revokes refresh tokens", test_logout))

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


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
