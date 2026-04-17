"""R6 tests \u2014 Flake quarantine, rerun-failed, comparison view, notifications stub."""

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
        fn(); print(f"  PASS  {name}"); return True
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}"); return False
    except Exception as e:
        import traceback; print(f"  ERROR {name}: {type(e).__name__}: {e}"); traceback.print_exc(); return False


def run_tests():
    print("\n=== R6 Polish Tests ===\n")
    results = []

    def t_flake_column_exists():
        from primeqa.db import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        try:
            rows = list(db.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='test_cases' AND column_name IN "
                "('is_quarantined','quarantined_at','quarantined_reason')"
            )))
            assert len(rows) == 3
        finally:
            db.close()
    results.append(test("R6-1. Flake quarantine columns on test_cases", t_flake_column_exists))

    def t_lift_quarantine_round_trip():
        from primeqa.db import SessionLocal
        from primeqa.execution.flake import lift_quarantine
        from primeqa.test_management.models import TestCase
        from datetime import datetime, timezone
        db = SessionLocal()
        try:
            tc = db.query(TestCase).filter(
                TestCase.tenant_id == TENANT_ID, TestCase.deleted_at.is_(None),
            ).order_by(TestCase.id.desc()).first()
            tc.is_quarantined = True
            tc.quarantined_at = datetime.now(timezone.utc)
            tc.quarantined_reason = "test-fixture"
            db.commit()
            assert lift_quarantine(db, test_case_id=tc.id, tenant_id=TENANT_ID) is True
            refetch = db.query(TestCase).filter_by(id=tc.id).first()
            assert refetch.is_quarantined is False
        finally:
            db.close()
    results.append(test("R6-2. lift_quarantine restores is_quarantined=False", t_lift_quarantine_round_trip))

    def t_notify_stub_dispatches():
        from primeqa.shared.notifications import Notification, send_email
        ok = send_email(Notification(
            kind="run_failed", subject="hi", body="test",
            recipients=["bob@example.com"],
        ))
        assert ok is True  # log provider \u2192 returns True
        # No recipients \u2192 no-op False
        assert send_email(Notification(
            kind="run_failed", subject="hi", body="body", recipients=[])) is False
    results.append(test("R6-3. send_email stub logs; empty recipients is no-op", t_notify_stub_dispatches))

    def t_rerun_failed_endpoint():
        r = client.post("/api/auth/login", json={
            "email": "admin@primeqa.io", "password": "changeme123", "tenant_id": TENANT_ID,
        })
        tok = r.get_json()["access_token"]
        client.set_cookie("access_token", tok)
        # No failed tests on a clean run \u2192 endpoint still returns 302 (flash + redirect)
        from primeqa.db import SessionLocal
        from primeqa.execution.models import PipelineRun
        db = SessionLocal()
        run = db.query(PipelineRun).filter(PipelineRun.tenant_id == TENANT_ID).order_by(PipelineRun.id.desc()).first()
        db.close()
        r2 = client.post(f"/runs/{run.id}/rerun-failed")
        assert r2.status_code in (302, 303), f"got {r2.status_code}"
    results.append(test("R6-4. /runs/:id/rerun-failed endpoint reachable", t_rerun_failed_endpoint))

    def t_compare_endpoint_renders():
        r = client.post("/api/auth/login", json={
            "email": "admin@primeqa.io", "password": "changeme123", "tenant_id": TENANT_ID,
        })
        tok = r.get_json()["access_token"]
        client.set_cookie("access_token", tok)
        from primeqa.db import SessionLocal
        from primeqa.execution.models import PipelineRun
        db = SessionLocal()
        run = db.query(PipelineRun).filter(PipelineRun.tenant_id == TENANT_ID).order_by(PipelineRun.id.desc()).first()
        db.close()
        r2 = client.get(f"/runs/{run.id}/compare")
        assert r2.status_code in (200, 302)
        if r2.status_code == 200:
            assert b"Compare" in r2.data
    results.append(test("R6-5. /runs/:id/compare renders", t_compare_endpoint_renders))

    passed = sum(results); total = len(results)
    print(f"\n{'='*40}\nResults: {passed}/{total} passed")
    print("ALL R6 TESTS PASSED" if passed == total else f"{total - passed} FAILED")
    return passed == total


if __name__ == "__main__":
    sys.exit(0 if run_tests() else 1)
