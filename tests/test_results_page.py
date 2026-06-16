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

NOTE (D-245): integration test — runs against the live Railway DB + mints JWTs.
Re-run on a real environment to confirm green; it cannot execute in a sandbox
that blocks DB writes / token minting. Nav capabilities are role-derived now
(_role_capabilities), so the deleted permission-set seeding was removed.
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
from primeqa.core.permissions import _role_capabilities
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


def run_tests():
    results = []
    print("\n=== Results Page Tests ===\n")

    admin_token = login_api("admin@primeqa.io", "changeme123")

    # --------------------------------------------------------------
    # 1. Sidebar: Results nav item points at /results
    # --------------------------------------------------------------
    def test_sidebar_points_at_results():
        # D-245: a Member (tester role) holds the run + view-results caps.
        perms = _role_capabilities("tester")
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

    # --------------------------------------------------------------
    # 5-9. D-231 — the failures front door (triage filters + repair drill)
    # --------------------------------------------------------------
    def test_runs_substrate_has_filter_chips():
        login_form("admin@primeqa.io", "changeme123")
        r = client.get("/runs/substrate")
        assert r.status_code == 200, r.status_code
        html = r.get_data(as_text=True)
        assert "Today" in html and "failures" in html, "no Today's-failures chip"
        assert 'border-indigo-600">All' in html, "All chip not active by default"
    results.append(test("5. /runs/substrate renders the triage filter chips (D-231)",
                        test_runs_substrate_has_filter_chips))

    def test_runs_outcome_filter_active():
        login_form("admin@primeqa.io", "changeme123")
        html = client.get("/runs/substrate?outcome=failed").get_data(as_text=True)
        assert 'border-indigo-600">Failed' in html, "Failed chip not active for ?outcome=failed"
        assert ">clear<" in html, "no clear link when filtered"
    results.append(test("6. ?outcome=failed drives the active Failed chip (D-231)",
                        test_runs_outcome_filter_active))

    def test_runs_status_alias_lands():
        # the /results redirect forwards ?status=failed — s4_runs_list must consume it
        login_form("admin@primeqa.io", "changeme123")
        html = client.get("/runs/substrate?status=failed").get_data(as_text=True)
        assert 'border-indigo-600">Failed' in html, "the forwarded ?status=failed did not land"
    results.append(test("7. the /results ?status=failed alias now lands (D-231)",
                        test_runs_status_alias_lands))

    def test_runs_bad_outcome_ignored():
        login_form("admin@primeqa.io", "changeme123")
        r = client.get("/runs/substrate?outcome=garbage")
        assert r.status_code == 200, r.status_code
        assert 'border-indigo-600">All' in r.get_data(as_text=True), "bad outcome not ignored"
    results.append(test("8. a bad ?outcome is ignored, not an error (D-231)",
                        test_runs_bad_outcome_ignored))

    def test_run_detail_surfaces_repair_action():
        from unittest.mock import patch
        login_form("admin@primeqa.io", "changeme123")
        fake = {"available": True, "found": True, "run": {
            "run_id": "11111111-1111-1111-1111-111111111111",
            "claim_test_id": "22222222-2222-2222-2222-222222222222",
            "outcome": "failed", "finished_at": "2026-06-13T10:00:00+00:00",
            "duration_ms": 1200, "environment_id": 59, "steps": [], "error": None,
            "interpretation": {"verdict": "prohibition_not_enforced",
                               "attribution": None, "cause": None, "phrasing": None,
                               "repair_suggestions": []}}}
        with patch("primeqa.intelligence.s4_execution_console.read_run_detail",
                   return_value=fake), \
             patch("primeqa.intelligence.repair_agent.open_proposal_for_run",
                   return_value={"id": 7, "proposal_kind": "rerun"}):
            html = client.get(
                "/runs/11111111-1111-1111-1111-111111111111").get_data(as_text=True)
        assert "A fix is ready for this run" in html, "no inline repair card"
        assert "/runs/substrate/repairs/7" in html, "repair action not wired to decide POST"
    results.append(test("9. run detail surfaces the inline repair action (D-231)",
                        test_run_detail_surfaces_repair_action))

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
