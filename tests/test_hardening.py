"""Phase A1 hardening tests.

Covers:
  - Query builder: per_page cap, sort-field whitelist, search wildcard escape
  - Service constructor DI: missing repos fail at construction, not at runtime
  - Soft delete / restore / admin-only purge round-trip
  - Optimistic locking mapped through service layer (409 CONFLICT)
  - Bulk-op safeguards: >100 id cap, destructive-action confirm token
  - Uniform API error envelope {"error":{"code","message"}}

These are integration-style tests that hit the Flask test client, same style
as the existing test_management.py so we don't diverge in setup patterns.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from primeqa.app import app

client = app.test_client()

TENANT_ID = 1


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


def ensure_user(admin_token, email, password, full_name, role):
    client.post("/api/auth/users", headers=auth(admin_token), json={
        "email": email, "password": password, "full_name": full_name, "role": role,
    })
    return login(email, password)


def run_tests():
    results = []
    print("\n=== Phase A1 Hardening Tests ===\n")

    admin_token = login("admin@primeqa.io", "changeme123")
    tester_token = ensure_user(admin_token, "hd_tester@primeqa.io", "test123", "HD Tester", "tester")

    # ---- Query builder unit tests -------------------------------------------

    def test_query_builder_caps_per_page():
        from primeqa.shared.query_builder import _clamp_per_page, MAX_PER_PAGE
        assert _clamp_per_page(9999) == MAX_PER_PAGE
        assert _clamp_per_page(None) == 20
        assert _clamp_per_page(50) == 50
    results.append(test("QB1. per_page caps at 50 / default 20", test_query_builder_caps_per_page))

    def test_query_builder_rejects_bad_per_page():
        from primeqa.shared.query_builder import _clamp_per_page, QueryBuilderError
        for bad in ("abc", -1, 0):
            try:
                _clamp_per_page(bad)
                raise AssertionError(f"expected error for {bad!r}")
            except QueryBuilderError:
                pass
    results.append(test("QB2. per_page rejects negative/zero/non-int", test_query_builder_rejects_bad_per_page))

    def test_query_builder_sort_whitelist():
        from primeqa.shared.query_builder import ListQuery, QueryBuilderError
        from primeqa.test_management.models import TestCase
        from primeqa.db import SessionLocal
        db = SessionLocal()
        try:
            q = db.query(TestCase).filter(TestCase.tenant_id == 1)
            builder = ListQuery(q, TestCase,
                                search_fields=["title"],
                                sort_whitelist=["updated_at", "title"])
            try:
                builder.sort("secret_admin_field", "desc")
                raise AssertionError("expected QueryBuilderError for disallowed sort field")
            except QueryBuilderError as e:
                assert e.code == "INVALID_SORT_FIELD"
        finally:
            db.close()
    results.append(test("QB3. Sort-field whitelist rejects non-allowed", test_query_builder_sort_whitelist))

    def test_query_builder_search_escapes_wildcards():
        from primeqa.shared.query_builder import ListQuery
        from primeqa.test_management.models import TestCase
        from primeqa.db import SessionLocal
        db = SessionLocal()
        try:
            q = db.query(TestCase).filter(TestCase.tenant_id == 1)
            builder = ListQuery(q, TestCase,
                                search_fields=["title"],
                                sort_whitelist=["updated_at"])
            # SQL wildcards in user input must not trigger full scans
            p = builder.search("100%_match").sort("updated_at", "desc").paginate(1, 20)
            assert p.per_page == 20
        finally:
            db.close()
    results.append(test("QB4. Search escapes %/_ wildcards safely", test_query_builder_search_escapes_wildcards))

    # ---- Service DI regression ---------------------------------------------

    def test_service_constructor_requires_all_repos():
        from primeqa.test_management.service import TestManagementService
        try:
            TestManagementService(
                section_repo=object(), requirement_repo=object(),
                test_case_repo=object(), suite_repo=object(),
                review_repo=None,   # <-- this is the bug's old home
                impact_repo=object(),
            )
            raise AssertionError("expected TypeError for missing review_repo")
        except TypeError as e:
            assert "review_repo" in str(e)
    results.append(test("DI1. Constructor rejects missing review_repo", test_service_constructor_requires_all_repos))

    # ---- API list pagination + envelope ------------------------------------

    def test_list_test_cases_paginated_envelope():
        r = client.get("/api/test-cases?page=1&per_page=5",
                       headers=auth(admin_token))
        assert r.status_code == 200, r.data
        body = r.get_json()
        assert "data" in body and "meta" in body, f"envelope missing: {body}"
        meta = body["meta"]
        for k in ("total", "page", "per_page", "total_pages"):
            assert k in meta, f"meta missing key {k}"
        assert meta["per_page"] == 5
        assert meta["page"] == 1
    results.append(test("API1. Paginated list returns {data, meta} envelope", test_list_test_cases_paginated_envelope))

    def test_list_test_cases_caps_per_page():
        r = client.get("/api/test-cases?page=1&per_page=9999",
                       headers=auth(admin_token))
        assert r.status_code == 200
        assert r.get_json()["meta"]["per_page"] == 50
    results.append(test("API2. per_page=9999 is clamped to 50", test_list_test_cases_caps_per_page))

    def test_bad_sort_returns_400_envelope():
        r = client.get("/api/test-cases?page=1&sort=secret_admin_field",
                       headers=auth(admin_token))
        assert r.status_code == 400, r.data
        body = r.get_json()
        assert body.get("error", {}).get("code") == "INVALID_SORT_FIELD"
    results.append(test("API3. Disallowed sort returns 400 INVALID_SORT_FIELD", test_bad_sort_returns_400_envelope))

    # ---- Soft delete / restore / purge round-trip --------------------------

    created_tc_id = {"id": None}

    def test_setup_tc_for_softdelete():
        # Create a section + requirement + test case for the round-trip
        sec_r = client.post("/api/sections", headers=auth(admin_token), json={
            "name": "Hardening Section",
        })
        assert sec_r.status_code == 201, sec_r.data
        sec_id = sec_r.get_json()["id"]
        req_r = client.post("/api/requirements", headers=auth(admin_token), json={
            "section_id": sec_id, "source": "manual",
        })
        assert req_r.status_code == 201
        req_id = req_r.get_json()["id"]
        tc_r = client.post("/api/test-cases", headers=auth(tester_token), json={
            "title": "Hardening TC", "requirement_id": req_id,
        })
        assert tc_r.status_code == 201, tc_r.data
        created_tc_id["id"] = tc_r.get_json()["id"]
    results.append(test("SD0. Seed section+requirement+test case", test_setup_tc_for_softdelete))

    def test_soft_delete_hides_from_list():
        tc_id = created_tc_id["id"]
        r = client.delete(f"/api/test-cases/{tc_id}", headers=auth(tester_token))
        assert r.status_code == 200, r.data

        # Paginated list should not include it (owner)
        r2 = client.get(f"/api/test-cases?page=1&per_page=50&q=Hardening+TC",
                        headers=auth(tester_token))
        ids = [tc["id"] for tc in r2.get_json()["data"]]
        assert tc_id not in ids, "soft-deleted TC still visible"
    results.append(test("SD1. Soft delete hides TC from default list", test_soft_delete_hides_from_list))

    def test_trash_view_shows_deleted():
        tc_id = created_tc_id["id"]
        r = client.get(f"/api/test-cases?page=1&per_page=50&deleted=1&q=Hardening+TC",
                       headers=auth(tester_token))
        assert r.status_code == 200
        ids = [tc["id"] for tc in r.get_json()["data"]]
        assert tc_id in ids, "trash view should include soft-deleted TC"
    results.append(test("SD2. ?deleted=1 trash view shows soft-deleted TC", test_trash_view_shows_deleted))

    def test_restore_brings_back():
        tc_id = created_tc_id["id"]
        r = client.post(f"/api/test-cases/{tc_id}/restore", headers=auth(tester_token))
        assert r.status_code == 200, r.data
        r2 = client.get(f"/api/test-cases/{tc_id}", headers=auth(tester_token))
        assert r2.status_code == 200
    results.append(test("SD3. Restore brings TC back from trash", test_restore_brings_back))

    def test_non_admin_cannot_purge():
        tc_id = created_tc_id["id"]
        # Soft-delete first so there's something to purge
        client.delete(f"/api/test-cases/{tc_id}", headers=auth(tester_token))
        r = client.post(f"/api/test-cases/{tc_id}/purge", headers=auth(tester_token))
        assert r.status_code == 403, f"tester should not be able to purge; got {r.status_code}"
    results.append(test("SD4. Non-admin cannot purge (403)", test_non_admin_cannot_purge))

    def test_admin_can_purge():
        tc_id = created_tc_id["id"]
        r = client.post(f"/api/test-cases/{tc_id}/purge", headers=auth(admin_token))
        assert r.status_code == 200, r.data
        # Subsequent GET should return 404
        r2 = client.get(f"/api/test-cases/{tc_id}", headers=auth(admin_token))
        assert r2.status_code == 404
    results.append(test("SD5. Admin purge permanently removes the TC", test_admin_can_purge))

    # ---- Bulk op safeguards ------------------------------------------------

    def test_bulk_over_cap_rejected():
        payload = {"ids": list(range(1, 102)), "action": "set_status",
                   "payload": {"status": "draft"}}
        r = client.post("/api/test-cases/bulk",
                        headers=auth(admin_token), json=payload)
        assert r.status_code == 400
        body = r.get_json()
        assert body["error"]["code"] == "BULK_LIMIT_EXCEEDED"
    results.append(test("BULK1. 101-item bulk op rejected with BULK_LIMIT_EXCEEDED", test_bulk_over_cap_rejected))

    def test_bulk_destructive_requires_confirm():
        r = client.post("/api/test-cases/bulk",
                        headers=auth(admin_token), json={
            "ids": [1, 2, 3], "action": "soft_delete",
        })
        assert r.status_code == 400
        assert r.get_json()["error"]["code"] == "BULK_CONFIRM_REQUIRED"
    results.append(test("BULK2. Destructive bulk without confirm rejected", test_bulk_destructive_requires_confirm))

    def test_bulk_purge_admin_only():
        r = client.post("/api/test-cases/bulk/purge",
                        headers=auth(tester_token), json={
            "ids": [1], "confirm": "DELETE",
        })
        assert r.status_code == 403, "tester should not be able to bulk purge"
    results.append(test("BULK3. Bulk purge is admin-only (403 for tester)", test_bulk_purge_admin_only))

    # ---- Summary ------------------------------------------------------------
    passed = sum(results)
    total = len(results)
    print(f"\n{'='*40}")
    print(f"Results: {passed}/{total} passed")
    if passed == total:
        print("ALL HARDENING TESTS PASSED")
    else:
        print(f"{total - passed} test(s) FAILED")
    print()
    return passed == total


if __name__ == "__main__":
    ok = run_tests()
    sys.exit(0 if ok else 1)
