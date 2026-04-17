"""Integration tests for test management module."""

import json
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from primeqa.app import app

client = app.test_client()

TENANT_ID = 1
admin_token = None
tester_token = None
ba_token = None
viewer_token = None

section_id = None
child_section_id = None
requirement_id = None
tc_id = None
tc_version_id = None
suite_id = None
review_id = None
meta_version_id = None


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


def login(email, password):
    r = client.post("/api/auth/login", json={
        "email": email, "password": password, "tenant_id": TENANT_ID,
    })
    data = r.get_json()
    assert "access_token" in data, f"Login failed for {email}: {r.data}"
    return data["access_token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def ensure_user(email, password, full_name, role):
    r = client.post("/api/auth/users", headers=auth(admin_token), json={
        "email": email, "password": password, "full_name": full_name, "role": role,
    })
    return login(email, password)


def setup_meta_version():
    """Create a minimal meta_version for test_case_versions FK."""
    import uuid
    from primeqa.db import SessionLocal
    from primeqa.metadata.models import MetaVersion
    from primeqa.core.models import Environment
    db = SessionLocal()
    try:
        env = db.query(Environment).first()
        if not env:
            from primeqa.core.models import Environment as E
            env = E(tenant_id=1, name="Test", env_type="sandbox",
                    sf_instance_url="https://test.sf.com", sf_api_version="59.0")
            db.add(env)
            db.commit()
            db.refresh(env)
        # Unique label per run to avoid colliding with prior test runs' rows.
        label = f"tm{uuid.uuid4().hex[:6]}"
        mv = MetaVersion(environment_id=env.id, version_label=label, status="complete")
        db.add(mv)
        db.commit()
        db.refresh(mv)
        return mv.id
    finally:
        db.close()


def run_tests():
    global admin_token, tester_token, ba_token, viewer_token
    global section_id, child_section_id, requirement_id
    global tc_id, tc_version_id, suite_id, review_id, meta_version_id
    results = []
    print("\n=== Test Management Tests ===\n")

    admin_token = login("admin@primeqa.io", "changeme123")
    tester_token = ensure_user("tm_tester@primeqa.io", "test123", "TM Tester", "tester")
    ba_token = ensure_user("tm_ba@primeqa.io", "ba123", "TM BA", "ba")
    viewer_token = ensure_user("tm_viewer@primeqa.io", "view123", "TM Viewer", "viewer")
    meta_version_id = setup_meta_version()

    # --- Sections ---

    def test_create_section():
        global section_id
        r = client.post("/api/sections", headers=auth(admin_token), json={
            "name": "Regression Tests", "description": "Main regression folder",
        })
        assert r.status_code == 201, f"Got {r.status_code}: {r.data}"
        section_id = r.get_json()["id"]
    results.append(test("1. Create section", test_create_section))

    def test_create_child_section():
        global child_section_id
        r = client.post("/api/sections", headers=auth(admin_token), json={
            "name": "Account Tests", "parent_id": section_id, "position": 1,
        })
        assert r.status_code == 201
        child_section_id = r.get_json()["id"]
    results.append(test("2. Create nested child section", test_create_child_section))

    def test_section_tree():
        r = client.get("/api/sections", headers=auth(admin_token))
        assert r.status_code == 200
        tree = r.get_json()
        root = [s for s in tree if s["id"] == section_id][0]
        assert len(root["children"]) == 1
        assert root["children"][0]["name"] == "Account Tests"
    results.append(test("3. Section tree returns nested structure", test_section_tree))

    # --- Requirements ---

    def test_create_requirement():
        global requirement_id
        r = client.post("/api/requirements", headers=auth(admin_token), json={
            "section_id": section_id, "source": "manual",
            "acceptance_criteria": "Account name must be unique",
        })
        assert r.status_code == 201, f"Got {r.status_code}: {r.data}"
        requirement_id = r.get_json()["id"]
    results.append(test("4. Create manual requirement", test_create_requirement))

    def test_jira_import():
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "key": "SQ-207",
            "fields": {
                "summary": "Create Account validation",
                "description": "As a user, I want account name validation",
            },
        }
        with patch("primeqa.test_management.service.http_requests.get", return_value=mock_resp):
            r = client.post("/api/requirements/import-jira", headers=auth(admin_token), json={
                "section_id": section_id,
                "jira_base_url": "https://jira.example.com",
                "jira_key": "SQ-207",
            })
            assert r.status_code == 201, f"Got {r.status_code}: {r.data}"
            data = r.get_json()
            assert data["source"] == "jira"
            assert data["jira_key"] == "SQ-207"
            assert data["jira_summary"] == "Create Account validation"
    results.append(test("5. Import Jira requirement (mocked)", test_jira_import))

    def test_jira_sync_stale():
        from primeqa.db import SessionLocal
        from primeqa.test_management.models import Requirement
        db = SessionLocal()
        jira_req = db.query(Requirement).filter(Requirement.jira_key == "SQ-207").first()
        db.close()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "key": "SQ-207",
            "fields": {
                "summary": "Create Account validation — UPDATED",
                "description": "Updated description with new requirements",
            },
        }
        with patch("primeqa.test_management.service.http_requests.get", return_value=mock_resp):
            r = client.post(f"/api/requirements/{jira_req.id}/sync",
                            headers=auth(admin_token), json={
                "jira_base_url": "https://jira.example.com",
            })
            assert r.status_code == 200, f"Got {r.status_code}: {r.data}"
            data = r.get_json()
            assert data["changed"] == True
            assert data["requirement"]["is_stale"] == True
            assert data["requirement"]["jira_version"] == 1
    results.append(test("6. Jira sync detects changes and marks stale", test_jira_sync_stale))

    # --- Test Cases ---

    def test_create_test_case():
        global tc_id
        r = client.post("/api/test-cases", headers=auth(tester_token), json={
            "title": "Create Account with required fields",
            "requirement_id": requirement_id,
        })
        assert r.status_code == 201, f"Got {r.status_code}: {r.data}"
        data = r.get_json()
        assert data["visibility"] == "private"
        assert data["status"] == "draft"
        tc_id = data["id"]
    results.append(test("7. Create test case (private by default)", test_create_test_case))

    def test_private_visibility():
        r = client.get(f"/api/test-cases/{tc_id}", headers=auth(viewer_token))
        assert r.status_code == 404, f"Viewer should not see private TC, got {r.status_code}"
    results.append(test("8. Viewer cannot see private test case", test_private_visibility))

    def test_owner_sees_private():
        r = client.get(f"/api/test-cases/{tc_id}", headers=auth(tester_token))
        assert r.status_code == 200
    results.append(test("9. Owner can see own private test case", test_owner_sees_private))

    def test_share():
        r = client.post(f"/api/test-cases/{tc_id}/share", headers=auth(tester_token))
        assert r.status_code == 200
        assert r.get_json()["visibility"] == "shared"
    results.append(test("10. Share test case changes visibility", test_share))

    def test_shared_visible():
        r = client.get(f"/api/test-cases/{tc_id}", headers=auth(viewer_token))
        assert r.status_code == 200, f"Viewer should see shared TC, got {r.status_code}"
    results.append(test("11. Shared test case visible to viewer", test_shared_visible))

    def test_optimistic_concurrency():
        r1 = client.get(f"/api/test-cases/{tc_id}", headers=auth(admin_token))
        current_version = r1.get_json()["version"]

        r2 = client.patch(f"/api/test-cases/{tc_id}", headers=auth(admin_token), json={
            "title": "Updated title", "expected_version": current_version,
        })
        assert r2.status_code == 200

        r3 = client.patch(f"/api/test-cases/{tc_id}", headers=auth(admin_token), json={
            "title": "Conflict title", "expected_version": current_version,
        })
        assert r3.status_code == 409, f"Expected 409, got {r3.status_code}: {r3.data}"
    results.append(test("12. Optimistic concurrency returns 409 on conflict", test_optimistic_concurrency))

    # --- Versions ---

    def test_create_version():
        global tc_version_id
        r = client.post(f"/api/test-cases/{tc_id}/versions",
                        headers=auth(tester_token), json={
            "metadata_version_id": meta_version_id,
            "generation_method": "manual",
            "steps": [
                {"step_order": 1, "action": "create", "target_object": "Account",
                 "field_values": {"Name": "Test Account"}, "expected_result": "Created"},
            ],
            "referenced_entities": ["Account.Name"],
        })
        assert r.status_code == 201, f"Got {r.status_code}: {r.data}"
        data = r.get_json()
        assert data["version_number"] == 1
        tc_version_id = data["id"]
    results.append(test("13. Create test case version", test_create_version))

    def test_create_second_version():
        r = client.post(f"/api/test-cases/{tc_id}/versions",
                        headers=auth(tester_token), json={
            "metadata_version_id": meta_version_id,
            "generation_method": "regenerated",
            "steps": [
                {"step_order": 1, "action": "create", "target_object": "Account",
                 "field_values": {"Name": "Test Account v2"}, "expected_result": "Created"},
            ],
            "referenced_entities": ["Account.Name", "Account.Industry"],
        })
        assert r.status_code == 201
        assert r.get_json()["version_number"] == 2
    results.append(test("14. Second version preserves first", test_create_second_version))

    def test_list_versions():
        r = client.get(f"/api/test-cases/{tc_id}/versions", headers=auth(tester_token))
        assert r.status_code == 200
        versions = r.get_json()
        assert len(versions) == 2
        assert versions[0]["version_number"] == 2
        assert versions[1]["version_number"] == 1
    results.append(test("15. List versions returns both (newest first)", test_list_versions))

    # --- Suites ---

    def test_create_suite():
        global suite_id
        r = client.post("/api/suites", headers=auth(admin_token), json={
            "name": "Smoke Suite", "suite_type": "smoke",
            "description": "Quick smoke tests",
        })
        assert r.status_code == 201
        suite_id = r.get_json()["id"]
    results.append(test("16. Create test suite", test_create_suite))

    def test_add_to_suite():
        r = client.post(f"/api/suites/{suite_id}/test-cases",
                        headers=auth(admin_token), json={
            "test_case_id": tc_id, "position": 1,
        })
        assert r.status_code == 200
    results.append(test("17. Add test case to suite", test_add_to_suite))

    def test_suite_test_cases():
        r = client.get(f"/api/suites/{suite_id}/test-cases", headers=auth(admin_token))
        assert r.status_code == 200
        data = r.get_json()
        assert len(data) == 1
        assert data[0]["test_case_id"] == tc_id
    results.append(test("18. Get suite test cases", test_suite_test_cases))

    def test_remove_from_suite():
        r = client.delete(f"/api/suites/{suite_id}/test-cases/{tc_id}",
                          headers=auth(admin_token))
        assert r.status_code == 200
        r2 = client.get(f"/api/suites/{suite_id}/test-cases", headers=auth(admin_token))
        assert len(r2.get_json()) == 0
    results.append(test("19. Remove test case from suite", test_remove_from_suite))

    # --- BA Reviews ---

    def test_assign_review():
        global review_id
        from primeqa.db import SessionLocal
        from primeqa.core.models import User
        db = SessionLocal()
        ba_user = db.query(User).filter(User.email == "tm_ba@primeqa.io").first()
        ba_id = ba_user.id
        db.close()

        r = client.post("/api/reviews", headers=auth(admin_token), json={
            "test_case_version_id": tc_version_id,
            "assigned_to": ba_id,
        })
        assert r.status_code == 201, f"Got {r.status_code}: {r.data}"
        data = r.get_json()
        assert data["status"] == "pending"
        review_id = data["id"]
    results.append(test("20. Assign review to BA", test_assign_review))

    def test_ba_review_queue():
        r = client.get("/api/reviews/my-queue", headers=auth(ba_token))
        assert r.status_code == 200
        data = r.get_json()
        assert len(data) >= 1
        assert data[0]["status"] == "pending"
    results.append(test("21. BA sees pending review in queue", test_ba_review_queue))

    def test_ba_approve():
        r = client.patch(f"/api/reviews/{review_id}", headers=auth(ba_token), json={
            "status": "approved", "feedback": "Looks good",
        })
        assert r.status_code == 200, f"Got {r.status_code}: {r.data}"
        data = r.get_json()
        assert data["status"] == "approved"

        r2 = client.get(f"/api/test-cases/{tc_id}", headers=auth(admin_token))
        assert r2.get_json()["status"] == "approved"
    results.append(test("22. BA approval changes test case status to approved", test_ba_approve))

    def test_viewer_cant_review():
        r = client.get("/api/reviews", headers=auth(viewer_token))
        assert r.status_code == 403
    results.append(test("23. Viewer cannot access reviews", test_viewer_cant_review))

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
