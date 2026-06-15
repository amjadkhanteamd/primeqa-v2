"""Integration tests for environment management.

Tests against the real Railway PostgreSQL database.

Idempotent: the two environments this suite creates use a per-run uuid suffix
(so a re-run never collides on the `environments_tenant_name_uk` unique
constraint) and are deleted in a teardown at the end. There is no DELETE
environment API endpoint, so teardown removes the rows (+ their credentials)
directly via the ORM. The credential tests need a real Fernet key
(CREDENTIAL_ENCRYPTION_KEY) which lives on Railway/CI but is commonly absent on
a dev box, so they SKIP rather than FAIL when it is unset.
"""

import sys
import os
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from primeqa.app import app
from primeqa.db import SessionLocal

client = app.test_client()

ADMIN_EMAIL = "admin@primeqa.io"
ADMIN_PASSWORD = "changeme123"
TENANT_ID = 1

# Credential tests need a real Fernet key. Gate them so a missing local key
# shows as SKIP, not a red FAIL (which would look like a regression).
HAVE_ENC_KEY = bool(os.getenv("CREDENTIAL_ENCRYPTION_KEY"))

admin_token = None
tester_token = None
created_env_id = None
created_env_name = None
prod_env_id = None


def test(name, fn):
    try:
        fn()
        print(f"  PASS  {name}")
        return "pass"
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        return "fail"
    except Exception as e:
        print(f"  ERROR {name}: {type(e).__name__}: {e}")
        return "fail"


def skip(name, reason):
    print(f"  SKIP  {name}: {reason}")
    return "skip"


def login(email, password):
    r = client.post("/api/auth/login", json={
        "email": email, "password": password, "tenant_id": TENANT_ID,
    })
    return r.get_json()["access_token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def _err_message(resp):
    """Pull the envelope error message string. json_error returns
    {"error": {"code", "message"}}, so the message lives one level in —
    reading .get("error") gives a dict, not a string."""
    body = resp.get_json() or {}
    err = body.get("error")
    if isinstance(err, dict):
        return err.get("message", "")
    return err or ""


def _cleanup_env(env_id):
    """Delete an environment row + its credentials directly (no DELETE API)."""
    if not env_id:
        return
    db = SessionLocal()
    try:
        from primeqa.core.models import Environment, EnvironmentCredential
        db.query(EnvironmentCredential).filter(
            EnvironmentCredential.environment_id == env_id).delete()
        env = db.query(Environment).filter(Environment.id == env_id).first()
        if env:
            db.delete(env)
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"  (teardown warning: could not delete env {env_id}: {e})")
    finally:
        db.close()


def run_tests():
    global admin_token, tester_token, created_env_id, created_env_name, prod_env_id
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
        global created_env_id, created_env_name
        created_env_name = f"Dev Sandbox {uuid.uuid4().hex[:8]}"
        r = client.post("/api/environments", headers=auth(admin_token), json={
            "name": created_env_name,
            "env_type": "sandbox",
            "sf_instance_url": "https://acme--dev.sandbox.my.salesforce.com",
            "sf_api_version": "59.0",
            "capture_mode": "smart",
            "execution_policy": "full",
        })
        assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.data}"
        data = r.get_json()
        assert data["name"] == created_env_name
        assert data["env_type"] == "sandbox"
        assert data["capture_mode"] == "smart"
        assert data["cleanup_mandatory"] == False
        created_env_id = data["id"]
    results.append(test("1. Admin can create an environment", test_create_env))

    # 2. Production env defaults cleanup_mandatory to True
    def test_production_cleanup():
        global prod_env_id
        r = client.post("/api/environments", headers=auth(admin_token), json={
            "name": f"Production {uuid.uuid4().hex[:8]}",
            "env_type": "production",
            "sf_instance_url": "https://acme.my.salesforce.com",
            "sf_api_version": "59.0",
        })
        assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.data}"
        data = r.get_json()
        assert data["cleanup_mandatory"] == True, f"Expected cleanup_mandatory=True, got {data['cleanup_mandatory']}"
        prod_env_id = data["id"]
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

    # 4. A tester can list environments (200 + a list). The count is
    #    group-scoped (admin + superadmin see all; testers see only envs in
    #    their groups), and this suite doesn't provision a group for the
    #    tester, so we don't assert a minimum count — only that listing is
    #    permitted and returns a list.
    def test_tester_can_list():
        r = client.get("/api/environments", headers=auth(tester_token))
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        data = r.get_json()
        assert isinstance(data, list), f"Expected a list, got {type(data).__name__}"
    results.append(test("4. Tester can list environments (scoped)", test_tester_can_list))

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
            "name": f"Bad Mode {uuid.uuid4().hex[:8]}",
            "env_type": "sandbox",
            "sf_instance_url": "https://test.com",
            "sf_api_version": "59.0",
            "capture_mode": "invalid_mode",
        })
        assert r.status_code == 400, f"Expected 400, got {r.status_code}"
        assert "capture_mode" in _err_message(r).lower()
    results.append(test("6. Invalid capture_mode rejected", test_invalid_capture_mode))

    # 7. Invalid execution_policy rejected
    def test_invalid_exec_policy():
        r = client.post("/api/environments", headers=auth(admin_token), json={
            "name": f"Bad Policy {uuid.uuid4().hex[:8]}",
            "env_type": "sandbox",
            "sf_instance_url": "https://test.com",
            "sf_api_version": "59.0",
            "execution_policy": "yolo",
        })
        assert r.status_code == 400, f"Expected 400, got {r.status_code}"
        assert "execution_policy" in _err_message(r).lower()
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
    results.append(test("8. Store credentials", test_store_credentials)
                   if HAVE_ENC_KEY else
                   skip("8. Store credentials", "CREDENTIAL_ENCRYPTION_KEY not set"))

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
    results.append(test("9. Credentials are stored encrypted in DB", test_credentials_encrypted)
                   if HAVE_ENC_KEY else
                   skip("9. Credentials are stored encrypted in DB", "CREDENTIAL_ENCRYPTION_KEY not set"))

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
    results.append(test("10. Credentials decrypt correctly", test_credentials_decrypt)
                   if HAVE_ENC_KEY else
                   skip("10. Credentials decrypt correctly", "CREDENTIAL_ENCRYPTION_KEY not set"))

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
        assert data["name"] == created_env_name
    results.append(test("13. Get single environment", test_get_env))

    # 14. Tester cannot store credentials
    def test_tester_no_creds():
        r = client.post(f"/api/environments/{created_env_id}/credentials",
                        headers=auth(tester_token), json={
            "client_id": "hack", "client_secret": "hack",
        })
        assert r.status_code == 403, f"Expected 403, got {r.status_code}"
    results.append(test("14. Tester cannot store credentials", test_tester_no_creds))

    # Teardown: remove the two environments this run created (test() never
    # propagates, so the loop always reaches here).
    _cleanup_env(created_env_id)
    _cleanup_env(prod_env_id)

    # Summary. Skips (missing local encryption key) are not failures.
    passed = results.count("pass")
    failed = results.count("fail")
    skipped = results.count("skip")
    total = len(results)
    print(f"\n{'='*40}")
    tail = f", {skipped} skipped" if skipped else ""
    print(f"Results: {passed}/{total} passed{tail}")
    if failed == 0:
        print("ALL TESTS PASSED" + (f" ({skipped} skipped — set CREDENTIAL_ENCRYPTION_KEY to run them)" if skipped else ""))
    else:
        print(f"{failed} test(s) FAILED")
    print()
    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
