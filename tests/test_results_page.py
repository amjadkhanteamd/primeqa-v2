"""Results page tests (Prompt 8).

This phase layered focused additions on top of the existing /runs list
and /runs/:id detail pages rather than rebuild them — the existing
UI already covers scoping, My Runs/All Runs, expect_fail visual
styling, SSE streaming, and rerun flows. New surface:

  - /results and /results/<id> route aliases (nav + landing target
    now points here)
  - GET /api/runs/:id/summary-text — Copy Summary endpoint
  - GET /api/run-step-results/:id/diagnosis-text — Copy Diagnosis
    endpoint

Tests here exercise what's NEW. Existing /runs tests stay green.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text

from primeqa.app import app
from primeqa.core.models import User
from primeqa.core.navigation import build_sidebar, get_landing_page
from primeqa.core.permissions import (
    BASE_PERMISSION_SETS, PermissionSet, UserPermissionSet,
)
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


def _force_perms(user_id: int, api_names: list[str]):
    db = SessionLocal()
    try:
        db.query(UserPermissionSet).filter_by(user_id=user_id).delete()
        for name in api_names:
            ps = db.query(PermissionSet).filter_by(
                tenant_id=TENANT_ID, api_name=name).first()
            db.add(UserPermissionSet(user_id=user_id, permission_set_id=ps.id))
        db.commit()
    finally:
        db.close()


def run_tests():
    results = []
    print("\n=== Results Page Tests ===\n")

    admin_token = login_api("admin@primeqa.io", "changeme123")

    # --------------------------------------------------------------
    # 1. Sidebar: Results nav item points at /results
    # --------------------------------------------------------------
    def test_sidebar_points_at_results():
        perms = set(next(s for s in BASE_PERMISSION_SETS if s["api_name"] == "tester_base")["permissions"])
        items = build_sidebar(perms, "/results")
        results_item = next((i for i in items if i["id"] == "results"), None)
        assert results_item is not None, "Results nav item missing"
        # D-218: Results points at the substrate runs index.
        assert results_item["url"] == "/runs/substrate", results_item
        # On /results (active_also_for) the item should still be active.
        assert results_item["active"] is True
    results.append(test("1. Sidebar Results entry -> /results + active",
                        test_sidebar_points_at_results))

    # --------------------------------------------------------------
    # 2. /results redirects to /runs (route alias)
    # --------------------------------------------------------------
    def test_results_redirects_to_runs():
        login_form("admin@primeqa.io", "changeme123")
        r = client.get("/results", follow_redirects=False)
        assert r.status_code in (301, 302), f"Expected redirect, got {r.status_code}"
        assert (r.headers["Location"].endswith("/runs/substrate")
                or "/runs/substrate?" in r.headers["Location"])
    results.append(test("2. /results -> redirect to /runs/substrate (D-218)",
                        test_results_redirects_to_runs))

    # --------------------------------------------------------------
    # 3. /results/:id redirects to the substrate runs index (D-221 R1)
    # --------------------------------------------------------------
    def test_result_detail_redirects_to_runs_id():
        login_form("admin@primeqa.io", "changeme123")
        r = client.get("/results/12345", follow_redirects=False)
        assert r.status_code in (301, 302), r.status_code
        assert r.headers["Location"].endswith("/runs/substrate")
    results.append(test("3. /results/:id -> redirect to /runs/substrate (D-221)",
                        test_result_detail_redirects_to_runs_id))

    # --------------------------------------------------------------
    # 4. /results preserves query-string on the redirect
    # --------------------------------------------------------------
    def test_results_preserves_querystring():
        login_form("admin@primeqa.io", "changeme123")
        r = client.get("/results?mine=1&status=failed", follow_redirects=False)
        assert r.status_code in (301, 302), r.status_code
        loc = r.headers["Location"]
        assert "mine=1" in loc and "status=failed" in loc, loc
    results.append(test("4. /results preserves query string on redirect",
                        test_results_preserves_querystring))

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
