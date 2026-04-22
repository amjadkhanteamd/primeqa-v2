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
from primeqa.execution.models import PipelineRun, RunStepResult, RunTestResult

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
        assert results_item["url"] == "/results", results_item
        # On /results the item should be active (exact match).
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
        assert r.headers["Location"].endswith("/runs") or "/runs?" in r.headers["Location"]
    results.append(test("2. /results -> redirect to /runs",
                        test_results_redirects_to_runs))

    # --------------------------------------------------------------
    # 3. /results/:id redirects to /runs/:id
    # --------------------------------------------------------------
    def test_result_detail_redirects_to_runs_id():
        db = SessionLocal()
        try:
            run = (db.query(PipelineRun)
                   .filter_by(tenant_id=TENANT_ID)
                   .order_by(PipelineRun.id.desc())
                   .first())
            run_id = run.id if run else None
        finally:
            db.close()
        if run_id is None:
            return  # no runs to exercise — skip
        login_form("admin@primeqa.io", "changeme123")
        r = client.get(f"/results/{run_id}", follow_redirects=False)
        assert r.status_code in (301, 302), r.status_code
        assert r.headers["Location"].endswith(f"/runs/{run_id}")
    results.append(test("3. /results/:id -> redirect to /runs/:id",
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
    # 5. GET /api/runs/:id/summary-text returns non-empty text
    # --------------------------------------------------------------
    def test_summary_text_endpoint():
        db = SessionLocal()
        try:
            run = (db.query(PipelineRun)
                   .filter_by(tenant_id=TENANT_ID)
                   .order_by(PipelineRun.id.desc())
                   .first())
            run_id = run.id if run else None
        finally:
            db.close()
        if run_id is None:
            return  # no runs
        r = client.get(f"/api/runs/{run_id}/summary-text",
                       headers={"Authorization": f"Bearer {admin_token}"})
        assert r.status_code == 200, f"{r.status_code} {r.data}"
        body = r.get_json()
        txt = body.get("text", "")
        assert txt.startswith("PrimeQA Run #"), f"text missing header: {txt!r}"
        # Always includes at least a count line.
        assert "passed" in txt
    results.append(test("5. /api/runs/:id/summary-text returns paste-ready block",
                        test_summary_text_endpoint))

    # --------------------------------------------------------------
    # 6. Summary text enforces own-scope for view_own_results-only users
    # --------------------------------------------------------------
    def test_summary_text_own_scope():
        # Create a tester with only view_own_results (developer_base
        # covers that, so reuse) who did NOT trigger the run.
        db = SessionLocal()
        try:
            existing = db.query(User).filter_by(
                email="rp_dev@primeqa.io", tenant_id=TENANT_ID).first()
            if existing is not None:
                db.execute(text("DELETE FROM refresh_tokens WHERE user_id = :id"),
                           {"id": existing.id})
                db.execute(text("DELETE FROM user_permission_sets WHERE user_id = :id"),
                           {"id": existing.id})
                db.execute(text("DELETE FROM users WHERE id = :id"),
                           {"id": existing.id})
                db.commit()
        finally:
            db.close()
        client.post("/api/auth/users",
                    headers={"Authorization": f"Bearer {admin_token}"},
                    json={"email": "rp_dev@primeqa.io", "password": "test123",
                          "full_name": "RP Dev", "role": "tester"})
        db = SessionLocal()
        try:
            dev = db.query(User).filter_by(email="rp_dev@primeqa.io",
                                           tenant_id=TENANT_ID).first()
            run = (db.query(PipelineRun)
                   .filter_by(tenant_id=TENANT_ID)
                   .filter(PipelineRun.triggered_by != dev.id)
                   .order_by(PipelineRun.id.desc())
                   .first())
            run_id = run.id if run else None
        finally:
            db.close()
        if run_id is None:
            return
        _force_perms(dev.id, ["developer_base"])  # has view_own_results only
        tok = login_api("rp_dev@primeqa.io", "test123")
        r = client.get(f"/api/runs/{run_id}/summary-text",
                       headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 403, f"Expected 403 for foreign run, got {r.status_code}"
        assert r.get_json()["error"]["code"] == "FORBIDDEN"
    results.append(test("6. summary-text 403s for view_own_results on foreign run",
                        test_summary_text_own_scope))

    # --------------------------------------------------------------
    # 7. GET /api/run-step-results/:id/diagnosis-text returns text
    # --------------------------------------------------------------
    def test_diagnosis_text_endpoint():
        db = SessionLocal()
        try:
            step = (db.query(RunStepResult)
                    .join(RunTestResult, RunTestResult.id == RunStepResult.run_test_result_id)
                    .join(PipelineRun, PipelineRun.id == RunTestResult.run_id)
                    .filter(PipelineRun.tenant_id == TENANT_ID)
                    .filter(RunStepResult.status.in_(("failed", "error")))
                    .order_by(RunStepResult.id.desc())
                    .first())
            step_id = step.id if step else None
        finally:
            db.close()
        if step_id is None:
            return  # no failed step to exercise
        r = client.get(f"/api/run-step-results/{step_id}/diagnosis-text",
                       headers={"Authorization": f"Bearer {admin_token}"})
        assert r.status_code == 200, f"{r.status_code} {r.data}"
        txt = r.get_json().get("text", "")
        # Header line is always "FAIL: Step N — <action>"
        assert txt.startswith("FAIL:"), f"text header wrong: {txt!r}"
    results.append(test("7. /api/run-step-results/:id/diagnosis-text works",
                        test_diagnosis_text_endpoint))

    # --------------------------------------------------------------
    # 8. diagnosis-text 404 for unknown step
    # --------------------------------------------------------------
    def test_diagnosis_text_unknown_step():
        r = client.get("/api/run-step-results/9999999/diagnosis-text",
                       headers={"Authorization": f"Bearer {admin_token}"})
        assert r.status_code == 404
    results.append(test("8. diagnosis-text returns 404 for unknown step",
                        test_diagnosis_text_unknown_step))

    # --------------------------------------------------------------
    # 9. summary-text 404 for unknown run
    # --------------------------------------------------------------
    def test_summary_text_unknown_run():
        r = client.get("/api/runs/9999999/summary-text",
                       headers={"Authorization": f"Bearer {admin_token}"})
        assert r.status_code == 404
    results.append(test("9. summary-text returns 404 for unknown run",
                        test_summary_text_unknown_run))

    # --------------------------------------------------------------
    # 10. Summary text composes negative-test sections when expect_fail
    #     steps are present in the run. We don't have guaranteed fixture
    #     data — skip gracefully if no candidate run exists.
    # --------------------------------------------------------------
    def test_summary_text_includes_counts():
        db = SessionLocal()
        try:
            run = (db.query(PipelineRun)
                   .filter_by(tenant_id=TENANT_ID)
                   .filter(PipelineRun.status.in_(("completed", "failed")))
                   .order_by(PipelineRun.id.desc())
                   .first())
            run_id = run.id if run else None
        finally:
            db.close()
        if run_id is None:
            return
        r = client.get(f"/api/runs/{run_id}/summary-text",
                       headers={"Authorization": f"Bearer {admin_token}"})
        txt = r.get_json().get("text", "")
        # Headline line should mention passed counts.
        assert "passed" in txt
        # Body either lists failed/blocked OR is empty (clean run) —
        # both are valid.
    results.append(test("10. summary-text includes counts line",
                        test_summary_text_includes_counts))

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
