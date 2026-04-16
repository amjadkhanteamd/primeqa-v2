"""Tests for metadata refresh module.

Uses the real database but mocks Salesforce API responses.
"""

import json
import sys
import os
from unittest.mock import patch, MagicMock

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
env_id = None


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
    return r.get_json()["access_token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


MOCK_SOBJECTS = {
    "sobjects": [
        {"name": "Account", "label": "Account", "keyPrefix": "001",
         "custom": False, "queryable": True, "createable": True,
         "updateable": True, "deletable": True},
        {"name": "Contact", "label": "Contact", "keyPrefix": "003",
         "custom": False, "queryable": True, "createable": True,
         "updateable": True, "deletable": True},
        {"name": "MyCustom__c", "label": "My Custom", "keyPrefix": "a00",
         "custom": True, "queryable": True, "createable": True,
         "updateable": True, "deletable": True},
        {"name": "ApexLog", "label": "Apex Log", "keyPrefix": None,
         "custom": False, "queryable": True, "createable": False,
         "updateable": False, "deletable": False},
    ]
}


def make_describe(object_name, extra_fields=None):
    base_fields = [
        {"name": "Id", "label": "Record ID", "type": "id",
         "nillable": False, "defaultedOnCreate": True, "custom": False,
         "createable": False, "updateable": False, "referenceTo": [],
         "length": 18, "precision": 0, "scale": 0,
         "picklistValues": [], "defaultValue": None},
        {"name": "Name", "label": "Name", "type": "string",
         "nillable": False, "defaultedOnCreate": False, "custom": False,
         "createable": True, "updateable": True, "referenceTo": [],
         "length": 255, "precision": 0, "scale": 0,
         "picklistValues": [], "defaultValue": None},
    ]
    if extra_fields:
        base_fields.extend(extra_fields)
    return {
        "fields": base_fields,
        "recordTypeInfos": [
            {"developerName": "Master", "name": "Master", "active": True,
             "defaultRecordTypeMapping": True},
        ],
    }


MOCK_DESCRIBE_ACCOUNT = make_describe("Account", [
    {"name": "Industry", "label": "Industry", "type": "picklist",
     "nillable": True, "defaultedOnCreate": False, "custom": False,
     "createable": True, "updateable": True, "referenceTo": [],
     "length": 0, "precision": 0, "scale": 0,
     "picklistValues": [{"value": "Tech", "label": "Technology"}],
     "defaultValue": None},
])

MOCK_DESCRIBE_CONTACT = make_describe("Contact", [
    {"name": "AccountId", "label": "Account ID", "type": "reference",
     "nillable": True, "defaultedOnCreate": False, "custom": False,
     "createable": True, "updateable": True, "referenceTo": ["Account"],
     "length": 18, "precision": 0, "scale": 0,
     "picklistValues": [], "defaultValue": None},
])

MOCK_DESCRIBE_CUSTOM = make_describe("MyCustom__c")

MOCK_VRS = {"records": [
    {"Id": "vr1", "ValidationName": "Require_Industry",
     "Active": True,
     "EntityDefinition": {"QualifiedApiName": "Account"},
     "ErrorConditionFormula": "ISBLANK(Industry)",
     "ErrorMessage": "Industry is required"},
], "done": True}

MOCK_FLOWS = {"records": [
    {"Id": "fl1", "ApiName": "Account_After_Update",
     "Label": "Account After Update", "ProcessType": "AutoLaunchedFlow",
     "TriggerType": "RecordAfterSave",
     "TriggerObjectOrEvent": {"QualifiedApiName": "Account"},
     "Status": "Active"},
], "done": True}

MOCK_TRIGGERS = {"records": [
    {"Id": "tr1", "Name": "AccountTrigger",
     "TableEnumOrId": "Account",
     "UsageBeforeInsert": True, "UsageAfterInsert": True,
     "UsageBeforeUpdate": False, "UsageAfterUpdate": True,
     "UsageBeforeDelete": False, "UsageAfterDelete": False},
], "done": True}


def mock_sf_get(url, **kwargs):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()

    if "/sobjects/" in url and "/describe" not in url and "tooling" not in url:
        resp.json.return_value = MOCK_SOBJECTS
    elif "/Account/describe" in url:
        resp.json.return_value = MOCK_DESCRIBE_ACCOUNT
    elif "/Contact/describe" in url:
        resp.json.return_value = MOCK_DESCRIBE_CONTACT
    elif "/MyCustom__c/describe" in url:
        resp.json.return_value = MOCK_DESCRIBE_CUSTOM
    elif "tooling/query" in url:
        q = kwargs.get("params", {}).get("q", "")
        if "ValidationRule" in q:
            resp.json.return_value = MOCK_VRS
        elif "Flow" in q:
            resp.json.return_value = MOCK_FLOWS
        elif "ApexTrigger" in q:
            resp.json.return_value = MOCK_TRIGGERS
        else:
            resp.json.return_value = {"records": [], "done": True}
    else:
        resp.json.return_value = {}

    return resp


def run_tests():
    global admin_token, env_id
    results = []
    print("\n=== Metadata Module Tests ===\n")

    admin_token = login(ADMIN_EMAIL, ADMIN_PASSWORD)

    # Setup: create environment with fake credentials
    r = client.post("/api/environments", headers=auth(admin_token), json={
        "name": "Meta Test Sandbox",
        "env_type": "sandbox",
        "sf_instance_url": "https://test.salesforce.com",
        "sf_api_version": "59.0",
    })
    assert r.status_code == 201, f"Env creation failed: {r.data}"
    env_id = r.get_json()["id"]

    r = client.post(f"/api/environments/{env_id}/credentials",
                    headers=auth(admin_token), json={
        "client_id": "test_client",
        "client_secret": "test_secret",
        "access_token": "fake_access_token",
        "refresh_token": "fake_refresh_token",
    })
    assert r.status_code == 200, f"Cred storage failed: {r.data}"

    # 1. Refresh metadata (mocked SF)
    def test_refresh():
        with patch("primeqa.metadata.service.http_requests.Session") as MockSession:
            mock_session = MagicMock()
            mock_session.get = mock_sf_get
            mock_session.headers = {}
            MockSession.return_value = mock_session

            r = client.post(f"/api/metadata/{env_id}/refresh",
                            headers=auth(admin_token))
            assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.data}"
            data = r.get_json()
            assert data["objects_count"] == 3, f"Expected 3 objects, got {data['objects_count']}"
            assert data["fields_count"] > 0, f"Expected fields, got {data['fields_count']}"
            assert data["vr_count"] == 1
            assert data["flow_count"] == 1
            assert data["trigger_count"] == 1
            assert data["snapshot_hash"] is not None
            assert data["version_label"] == "v1"
    results.append(test("1. Metadata refresh creates version with correct counts", test_refresh))

    # 2. Version status is 'complete'
    def test_version_complete():
        r = client.get(f"/api/metadata/{env_id}/current", headers=auth(admin_token))
        assert r.status_code == 200
        data = r.get_json()
        assert data["status"] == "complete"
        assert data["lifecycle"] == "active"
        assert data["object_count"] == 3
    results.append(test("2. Version status is 'complete' after refresh", test_version_complete))

    # 3. Objects stored correctly in DB
    def test_objects_stored():
        db = SessionLocal()
        try:
            from primeqa.metadata.models import MetaObject, MetaVersion
            from primeqa.core.models import Environment
            env = db.query(Environment).filter(Environment.id == env_id).first()
            mv_id = env.current_meta_version_id
            objects = db.query(MetaObject).filter(MetaObject.meta_version_id == mv_id).all()
            names = {o.api_name for o in objects}
            assert "Account" in names, f"Account not found in {names}"
            assert "Contact" in names
            assert "MyCustom__c" in names
            assert "ApexLog" not in names, "ApexLog should be filtered out"
        finally:
            db.close()
    results.append(test("3. Objects stored correctly (system objects filtered)", test_objects_stored))

    # 4. Fields stored correctly
    def test_fields_stored():
        db = SessionLocal()
        try:
            from primeqa.metadata.models import MetaField, MetaObject, MetaVersion
            from primeqa.core.models import Environment
            env = db.query(Environment).filter(Environment.id == env_id).first()
            mv_id = env.current_meta_version_id
            acc = db.query(MetaObject).filter(
                MetaObject.meta_version_id == mv_id, MetaObject.api_name == "Account"
            ).first()
            fields = db.query(MetaField).filter(
                MetaField.meta_version_id == mv_id, MetaField.meta_object_id == acc.id
            ).all()
            field_names = {f.api_name for f in fields}
            assert "Id" in field_names
            assert "Name" in field_names
            assert "Industry" in field_names
        finally:
            db.close()
    results.append(test("4. Fields stored correctly for Account", test_fields_stored))

    # 5. Second refresh detects changes
    def test_diff_detection():
        modified_sobjects = {
            "sobjects": MOCK_SOBJECTS["sobjects"] + [
                {"name": "Opportunity", "label": "Opportunity", "keyPrefix": "006",
                 "custom": False, "queryable": True, "createable": True,
                 "updateable": True, "deletable": True},
            ]
        }

        def mock_sf_get_v2(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            if "/sobjects/" in url and "/describe" not in url and "tooling" not in url:
                resp.json.return_value = modified_sobjects
            elif "/Opportunity/describe" in url:
                resp.json.return_value = make_describe("Opportunity")
            else:
                return mock_sf_get(url, **kwargs)
            return resp

        with patch("primeqa.metadata.service.http_requests.Session") as MockSession:
            mock_session = MagicMock()
            mock_session.get = mock_sf_get_v2
            mock_session.headers = {}
            MockSession.return_value = mock_session

            r = client.post(f"/api/metadata/{env_id}/refresh",
                            headers=auth(admin_token))
            assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.data}"
            data = r.get_json()
            assert data["version_label"] == "v2"
            assert data["objects_count"] == 4
            assert data["changes_detected"] == True
    results.append(test("5. Second refresh detects changes", test_diff_detection))

    # 6. Diff endpoint returns differences
    def test_diff_endpoint():
        r = client.get(f"/api/metadata/{env_id}/diff", headers=auth(admin_token))
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.data}"
        data = r.get_json()
        assert "fields" in data
        field_diff = data["fields"]
        added_objects = {f["object"] for f in field_diff.get("added", [])}
        assert "Opportunity" in added_objects, f"Expected Opportunity in added, got {added_objects}"
    results.append(test("6. Diff endpoint returns field differences", test_diff_endpoint))

    # 7. Archival keeps only N versions
    def test_archival():
        db = SessionLocal()
        try:
            from primeqa.metadata.models import MetaVersion
            from primeqa.metadata.repository import MetadataRepository
            repo = MetadataRepository(db)
            archived = repo.archive_old_versions(env_id, keep_count=1)
            assert archived == 1, f"Expected 1 archived, got {archived}"

            active = db.query(MetaVersion).filter(
                MetaVersion.environment_id == env_id,
                MetaVersion.lifecycle == "active",
                MetaVersion.status == "complete",
            ).count()
            assert active == 1, f"Expected 1 active version, got {active}"
        finally:
            db.close()
    results.append(test("7. Archival keeps only latest N versions", test_archival))

    # 8. Impact analysis (with mock test case)
    def test_impact_analysis():
        db = SessionLocal()
        try:
            from primeqa.metadata.models import MetaVersion
            from primeqa.metadata.repository import MetadataRepository
            from primeqa.core.repository import EnvironmentRepository
            from primeqa.metadata.service import MetadataService

            repo = MetadataRepository(db)
            env_repo = EnvironmentRepository(db)

            versions = db.query(MetaVersion).filter(
                MetaVersion.environment_id == env_id,
                MetaVersion.status == "complete",
            ).order_by(MetaVersion.started_at.desc()).limit(2).all()

            if len(versions) >= 2:
                svc = MetadataService(repo, env_repo)
                count = svc.run_impact_analysis(env_id, versions[0].id, versions[1].id)
                assert count >= 0, f"Impact analysis returned {count}"
            else:
                pass
        finally:
            db.close()
    results.append(test("8. Impact analysis runs without error", test_impact_analysis))

    # 9. Current version endpoint
    def test_current_endpoint():
        r = client.get(f"/api/metadata/{env_id}/current", headers=auth(admin_token))
        assert r.status_code == 200
        data = r.get_json()
        assert "version_id" in data
        assert "object_count" in data
        assert data["status"] == "complete"
    results.append(test("9. Current version endpoint returns summary", test_current_endpoint))

    # 10. Impacts endpoint
    def test_impacts_endpoint():
        r = client.get(f"/api/metadata/{env_id}/impacts", headers=auth(admin_token))
        assert r.status_code == 200
        data = r.get_json()
        assert isinstance(data, list)
    results.append(test("10. Impacts endpoint returns list", test_impacts_endpoint))

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
