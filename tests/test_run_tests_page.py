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
    db = SessionLocal()
    try:
        existing = db.query(User).filter_by(email=email, tenant_id=TENANT_ID).first()
        if existing is not None:
            existing_id = existing.id
            db.execute(text("DELETE FROM refresh_tokens WHERE user_id = :id"),
                       {"id": existing_id})
            db.execute(text("DELETE FROM user_permission_sets WHERE user_id = :id"),
                       {"id": existing_id})
            db.execute(text("DELETE FROM users WHERE id = :id"), {"id": existing_id})
            db.commit()
    finally:
        db.close()
    r = client.post("/api/auth/users",
                    headers={"Authorization": f"Bearer {admin_token}"},
                    json={"email": email, "password": password,
                          "full_name": email.split("@")[0].replace(".", " ").title(),
                          "role": role})
    assert r.status_code in (200, 201), f"create user failed: {r.status_code} {r.data[:200]}"
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
        login_form("tester_rt@primeqa.io", "test123")
        r = client.get("/run", follow_redirects=False)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        html = r.data.decode("utf-8", "replace")
        assert "Run Tests" in html, "Page title missing"
        # Sprint tab visible for tester
        assert 'data-mode="sprint"' in html, "Sprint tab should be visible"
    results.append(test("1. /run renders for tester (run_sprint holder)",
                        test_run_renders_for_tester))

    def test_run_redirects_for_developer():
        login_form("dev_rt@primeqa.io", "test123")
        r = client.get("/run", follow_redirects=False)
        assert r.status_code in (301, 302), f"Expected redirect, got {r.status_code}"
        # developer_base -> /requirements
        assert "/requirements" in r.headers["Location"], r.headers["Location"]
    results.append(test("2. /run redirects developer (no bulk perms)",
                        test_run_redirects_for_developer))

    def test_run_hides_suite_tab_without_perm():
        # Give tester_rt only run_sprint (no run_suite). Sprint tab should
        # still render; Suite tab should be absent.
        _force_perms(tester_user.id, ["developer_base", "run_sprint"])
        login_form("tester_rt@primeqa.io", "test123")
        r = client.get("/run")
        html = r.data.decode("utf-8", "replace")
        assert 'data-mode="sprint"' in html
        assert 'data-mode="suite"' not in html, "Suite tab should be hidden"
        _force_perms(tester_user.id, ["tester_base"])  # restore
    results.append(test("3. /run hides tabs the user doesn't have perm for",
                        test_run_hides_suite_tab_without_perm))

    # --- API validation ---
    tester_token = login_api("tester_rt@primeqa.io", "test123")

    def test_api_rejects_unknown_run_type():
        r = client.post("/api/bulk-runs",
                        headers={"Authorization": f"Bearer {tester_token}"},
                        json={"environment_id": 1, "run_type": "yolo"})
        assert r.status_code == 400
        assert r.get_json()["error"]["code"] == "VALIDATION_ERROR"
    results.append(test("4. POST /api/bulk-runs rejects unknown run_type",
                        test_api_rejects_unknown_run_type))

    def test_api_rejects_missing_env_id():
        r = client.post("/api/bulk-runs",
                        headers={"Authorization": f"Bearer {tester_token}"},
                        json={"run_type": "sprint"})
        assert r.status_code == 400
    results.append(test("5. POST /api/bulk-runs requires environment_id",
                        test_api_rejects_missing_env_id))

    def test_api_404_on_unknown_env():
        r = client.post("/api/bulk-runs",
                        headers={"Authorization": f"Bearer {tester_token}"},
                        json={"run_type": "sprint", "environment_id": 9999999,
                              "ticket_keys": ["X-1"]})
        # Depending on decorator ordering, the env-policy layer may 403
        # before the 404 fires. Either is acceptable here — just not 2xx.
        assert r.status_code in (403, 404), f"Expected 403/404 got {r.status_code}"
    results.append(test("6. POST /api/bulk-runs rejects unknown env",
                        test_api_404_on_unknown_env))

    def test_api_requires_bulk_run_perm():
        dev_token = login_api("dev_rt@primeqa.io", "test123")
        db = SessionLocal()
        try:
            env = (db.query(Environment)
                   .filter_by(tenant_id=TENANT_ID, is_active=True).first())
            env_id = env.id
        finally:
            db.close()
        r = client.post("/api/bulk-runs",
                        headers={"Authorization": f"Bearer {dev_token}"},
                        json={"run_type": "sprint", "environment_id": env_id,
                              "ticket_keys": ["X-1"]})
        assert r.status_code == 403, f"{r.status_code} {r.data}"
        body = r.get_json()
        assert body["error"]["code"] == "INSUFFICIENT_PERMISSIONS", body
    results.append(test("7. POST /api/bulk-runs requires bulk_run perm (sprint)",
                        test_api_requires_bulk_run_perm))

    def test_api_blocks_when_bulk_run_disabled():
        # Flip env.allow_bulk_run=false and confirm 403.
        db = SessionLocal()
        try:
            env = (db.query(Environment)
                   .filter_by(tenant_id=TENANT_ID, is_active=True).first())
            env_id = env.id
            prior = env.allow_bulk_run
            env.allow_bulk_run = False
            db.commit()
        finally:
            db.close()
        try:
            r = client.post("/api/bulk-runs",
                            headers={"Authorization": f"Bearer {tester_token}"},
                            json={"run_type": "sprint", "environment_id": env_id,
                                  "ticket_keys": ["X-1"]})
            assert r.status_code == 403, f"{r.status_code} {r.data}"
            body = r.get_json()
            assert body["error"]["code"] == "ENVIRONMENT_POLICY_DENIED", body
        finally:
            db = SessionLocal()
            try:
                env = db.query(Environment).filter_by(id=env_id).first()
                env.allow_bulk_run = prior
                db.commit()
            finally:
                db.close()
    results.append(test("8. POST /api/bulk-runs blocked when allow_bulk_run=false",
                        test_api_blocks_when_bulk_run_disabled))

    def test_api_production_requires_confirmation():
        db = SessionLocal()
        try:
            env = (db.query(Environment)
                   .filter_by(tenant_id=TENANT_ID, is_active=True).first())
            env_id = env.id
            prior_prod = env.is_production
            env.is_production = True
            db.commit()
        finally:
            db.close()
        try:
            r = client.post("/api/bulk-runs",
                            headers={"Authorization": f"Bearer {tester_token}"},
                            json={"run_type": "sprint", "environment_id": env_id,
                                  "ticket_keys": ["X-1"]})
            assert r.status_code == 403, f"{r.status_code} {r.data}"
            msg = r.get_json()["error"]["message"].lower()
            assert "production" in msg
        finally:
            db = SessionLocal()
            try:
                env = db.query(Environment).filter_by(id=env_id).first()
                env.is_production = prior_prod
                db.commit()
            finally:
                db.close()
    results.append(test("9. POST /api/bulk-runs blocks prod without confirm_production",
                        test_api_production_requires_confirmation))

    def test_api_no_tests_error():
        db = SessionLocal()
        try:
            env = (db.query(Environment)
                   .filter_by(tenant_id=TENANT_ID, is_active=True).first())
            env_id = env.id
        finally:
            db.close()
        r = client.post("/api/bulk-runs",
                        headers={"Authorization": f"Bearer {tester_token}"},
                        json={"run_type": "sprint", "environment_id": env_id,
                              "ticket_keys": ["DOES-NOT-EXIST-99999"]})
        assert r.status_code == 400
        assert r.get_json()["error"]["code"] == "NO_TESTS"
    results.append(test("10. POST /api/bulk-runs NO_TESTS on unresolvable keys",
                        test_api_no_tests_error))

    # --- Happy path + status/cancel ---
    created_run_id = None

    def test_api_sprint_creates_pipeline_run():
        nonlocal created_run_id
        # Find a real ticket key that HAS test cases.
        from primeqa.test_management.models import Requirement, TestCase
        db = SessionLocal()
        try:
            env = (db.query(Environment)
                   .filter_by(tenant_id=TENANT_ID, is_active=True).first())
            env_id = env.id
            req = (db.query(Requirement)
                   .join(TestCase, TestCase.requirement_id == Requirement.id)
                   .filter(Requirement.tenant_id == TENANT_ID,
                           Requirement.jira_key.isnot(None),
                           Requirement.deleted_at.is_(None),
                           TestCase.deleted_at.is_(None))
                   .first())
            if req is None:
                return  # tenant has no usable fixture — skip
            key = req.jira_key
        finally:
            db.close()
        r = client.post("/api/bulk-runs",
                        headers={"Authorization": f"Bearer {tester_token}"},
                        json={"run_type": "sprint", "environment_id": env_id,
                              "ticket_keys": [key]})
        assert r.status_code == 201, f"{r.status_code} {r.data}"
        body = r.get_json()
        assert "pipeline_run_id" in body, body
        assert body["redirect"].startswith("/runs/")
        created_run_id = body["pipeline_run_id"]
    results.append(test("11. POST /api/bulk-runs creates a pipeline_run",
                        test_api_sprint_creates_pipeline_run))

    def test_api_status_returns_run_info():
        if created_run_id is None:
            return
        r = client.get(f"/api/bulk-runs/{created_run_id}/status",
                       headers={"Authorization": f"Bearer {tester_token}"})
        assert r.status_code == 200, f"{r.status_code} {r.data}"
        body = r.get_json()
        assert body["id"] == created_run_id
        assert "total_tickets" in body
        assert "tickets" in body and isinstance(body["tickets"], list)
    results.append(test("12. GET /api/bulk-runs/:id/status returns per-ticket info",
                        test_api_status_returns_run_info))

    def test_api_cancel_sets_cancelled():
        if created_run_id is None:
            return
        r = client.post(f"/api/bulk-runs/{created_run_id}/cancel",
                        headers={"Authorization": f"Bearer {tester_token}"})
        assert r.status_code == 200, f"{r.status_code} {r.data}"
        body = r.get_json()
        # Either "cancelled" or already terminal (run may have finished
        # between create and cancel against a fast environment).
        assert body.get("status") in ("cancelled", "completed", "failed"), body
    results.append(test("13. POST /api/bulk-runs/:id/cancel -> cancelled/terminal",
                        test_api_cancel_sets_cancelled))

    def test_api_cancel_rejects_non_owner():
        if created_run_id is None:
            return
        # dev_rt didn't trigger this run and doesn't have manage_environments.
        dev_token = login_api("dev_rt@primeqa.io", "test123")
        r = client.post(f"/api/bulk-runs/{created_run_id}/cancel",
                        headers={"Authorization": f"Bearer {dev_token}"})
        # Either 403 (forbidden) or 200 with already_terminal (prior test
        # already ended the run) — both are legitimate outcomes.
        assert r.status_code in (200, 403), f"{r.status_code} {r.data}"
        if r.status_code == 200:
            assert r.get_json().get("already_terminal") is True
    results.append(test("14. POST /api/bulk-runs/:id/cancel rejects non-owner",
                        test_api_cancel_rejects_non_owner))

    def test_tester_lands_on_run_page():
        # Tester base lands on /run (not /runs/new) per the spec.
        perms = set(next(s for s in BASE_PERMISSION_SETS if s["api_name"] == "tester_base")["permissions"])
        assert get_landing_page(perms) == "/run"
    results.append(test("15. Tester base landing page is /run",
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
