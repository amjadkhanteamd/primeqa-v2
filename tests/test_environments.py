"""Integration tests for environment management.

Tests against the real Railway PostgreSQL database.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from primeqa.app import app
from primeqa.db import SessionLocal

client = app.test_client()

ADMIN_EMAIL = "admin@primeqa.io"
ADMIN_PASSWORD = "changeme123"
TENANT_ID = 1

admin_token = None
tester_token = None
created_env_id = None


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


def login(email, password):
    r = client.post("/api/auth/login", json={
        "email": email, "password": password, "tenant_id": TENANT_ID,
    })
    return r.get_json()["access_token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def run_tests():
    global admin_token, tester_token, created_env_id
    results = []
    print("\n=== Environment Management Tests ===\n")

    # Setup: get admin token, ensure tester exists
    admin_token = login(ADMIN_EMAIL, ADMIN_PASSWORD)

    # Create tester if not exists
    r = client.post("/api/auth/users", headers=auth(admin_token), json={
        "email": "envtester@primeqa.io", "password": "test123",
        "full_name": "Env Tester", "role": "tester",
    })
    if r.status_code == 201:
        pass
    tester_token = login("envtester@primeqa.io", "test123")

    # 1. Admin can create an environment
    def test_create_env():
        global created_env_id
        r = client.post("/api/environments", headers=auth(admin_token), json={
            "name": "Dev Sandbox",
            "env_type": "sandbox",
            "sf_instance_url": "https://acme--dev.sandbox.my.salesforce.com",
            "sf_api_version": "59.0",
            "capture_mode": "smart",
            "execution_policy": "full",
        })
        assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.data}"
        data = r.get_json()
        assert data["name"] == "Dev Sandbox"
        assert data["env_type"] == "sandbox"
        assert data["capture_mode"] == "smart"
        assert data["cleanup_mandatory"] == False
        created_env_id = data["id"]
    results.append(test("1. Admin can create an environment", test_create_env))

    # 2. Production env defaults cleanup_mandatory to True
    def test_production_cleanup():
        r = client.post("/api/environments", headers=auth(admin_token), json={
            "name": "Production",
            "env_type": "production",
            "sf_instance_url": "https://acme.my.salesforce.com",
            "sf_api_version": "59.0",
        })
        assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.data}"
        data = r.get_json()
        assert data["cleanup_mandatory"] == True, f"Expected cleanup_mandatory=True, got {data['cleanup_mandatory']}"
    results.append(test("2. Production env defaults cleanup_mandatory to True", test_production_cleanup))

    # 3. Tester cannot create environments
    def test_tester_blocked():
        r = client.post("/api/environments", headers=auth(tester_token), json={
            "name": "Hacker Env",
            "env_type": "sandbox",
            "sf_instance_url": "https://evil.com",
            "sf_api_version": "59.0",
        })
        assert r.status_code == 403, f"Expected 403, got {r.status_code}"
    results.append(test("3. Tester cannot create environments", test_tester_blocked))

    # 4. All roles can list environments
    def test_tester_can_list():
        r = client.get("/api/environments", headers=auth(tester_token))
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        data = r.get_json()
        assert isinstance(data, list)
        assert len(data) >= 1
    results.append(test("4. Tester can list environments", test_tester_can_list))

    # 5. List environments is tenant-scoped
    def test_tenant_scoped():
        r = client.get("/api/environments", headers=auth(admin_token))
        assert r.status_code == 200
        data = r.get_json()
        for env in data:
            assert env["tenant_id"] == TENANT_ID, f"Got env from tenant {env['tenant_id']}"
    results.append(test("5. List environments is tenant-scoped", test_tenant_scoped))

    # 6. Invalid capture_mode rejected
    def test_invalid_capture_mode():
        r = client.post("/api/environments", headers=auth(admin_token), json={
            "name": "Bad Mode",
            "env_type": "sandbox",
            "sf_instance_url": "https://test.com",
            "sf_api_version": "59.0",
            "capture_mode": "invalid_mode",
        })
        assert r.status_code == 400, f"Expected 400, got {r.status_code}"
        assert "capture_mode" in r.get_json().get("error", "").lower()
    results.append(test("6. Invalid capture_mode rejected", test_invalid_capture_mode))

    # 7. Invalid execution_policy rejected
    def test_invalid_exec_policy():
        r = client.post("/api/environments", headers=auth(admin_token), json={
            "name": "Bad Policy",
            "env_type": "sandbox",
            "sf_instance_url": "https://test.com",
            "sf_api_version": "59.0",
            "execution_policy": "yolo",
        })
        assert r.status_code == 400, f"Expected 400, got {r.status_code}"
        assert "execution_policy" in r.get_json().get("error", "").lower()
    results.append(test("7. Invalid execution_policy rejected", test_invalid_exec_policy))

    # 8. Store credentials (encrypted)
    def test_store_credentials():
        r = client.post(f"/api/environments/{created_env_id}/credentials",
                        headers=auth(admin_token), json={
            "client_id": "my_client_id_123",
            "client_secret": "my_client_secret_456",
            "access_token": "sf_access_token_789",
            "refresh_token": "sf_refresh_token_012",
        })
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.data}"
    results.append(test("8. Store credentials", test_store_credentials))

    # 9. Credentials are stored encrypted in DB (raw query)
    def test_credentials_encrypted():
        db = SessionLocal()
        try:
            from primeqa.core.models import EnvironmentCredential
            cred = db.query(EnvironmentCredential).filter(
                EnvironmentCredential.environment_id == created_env_id
            ).first()
            assert cred is not None, "No credentials found"
            assert cred.client_id != "my_client_id_123", \
                f"client_id is stored as plaintext: {cred.client_id}"
            assert "gAAAAA" in cred.client_id, \
                f"client_id doesn't look like Fernet ciphertext: {cred.client_id[:30]}..."
            assert cred.client_secret != "my_client_secret_456"
            assert cred.access_token != "sf_access_token_789"
        finally:
            db.close()
    results.append(test("9. Credentials are stored encrypted in DB", test_credentials_encrypted))

    # 10. Credentials decrypt correctly
    def test_credentials_decrypt():
        from primeqa.core.crypto import decrypt
        db = SessionLocal()
        try:
            from primeqa.core.models import EnvironmentCredential
            cred = db.query(EnvironmentCredential).filter(
                EnvironmentCredential.environment_id == created_env_id
            ).first()
            assert decrypt(cred.client_id) == "my_client_id_123"
            assert decrypt(cred.client_secret) == "my_client_secret_456"
            assert decrypt(cred.access_token) == "sf_access_token_789"
            assert decrypt(cred.refresh_token) == "sf_refresh_token_012"
        finally:
            db.close()
    results.append(test("10. Credentials decrypt correctly", test_credentials_decrypt))

    # 11. Update environment
    def test_update_env():
        r = client.patch(f"/api/environments/{created_env_id}",
                         headers=auth(admin_token), json={
            "capture_mode": "full",
            "max_execution_slots": 5,
        })
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.data}"
        data = r.get_json()
        assert data["capture_mode"] == "full"
        assert data["max_execution_slots"] == 5
    results.append(test("11. Update environment", test_update_env))

    # 12. Update with invalid capture_mode rejected
    def test_update_invalid():
        r = client.patch(f"/api/environments/{created_env_id}",
                         headers=auth(admin_token), json={
            "capture_mode": "turbo",
        })
        assert r.status_code == 400, f"Expected 400, got {r.status_code}"
    results.append(test("12. Update with invalid capture_mode rejected", test_update_invalid))

    # 13. Get single environment
    def test_get_env():
        r = client.get(f"/api/environments/{created_env_id}",
                       headers=auth(admin_token))
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        data = r.get_json()
        assert data["id"] == created_env_id
        assert data["name"] == "Dev Sandbox"
    results.append(test("13. Get single environment", test_get_env))

    # 14. Tester cannot store credentials
    def test_tester_no_creds():
        r = client.post(f"/api/environments/{created_env_id}/credentials",
                        headers=auth(tester_token), json={
            "client_id": "hack", "client_secret": "hack",
        })
        assert r.status_code == 403, f"Expected 403, got {r.status_code}"
    results.append(test("14. Tester cannot store credentials", test_tester_no_creds))

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
