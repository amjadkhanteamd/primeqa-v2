"""Route tests for the S4 execution enqueue endpoint (D-197).

POST /api/s4-execution-jobs + GET /api/s4-execution-jobs/<id>. Mirrors the S3
generation-jobs route tests: real app via `app.test_client()` + a real tenant-1
login + a real env fixture. Uses a RANDOM test_id (no approved recipe) so an
enqueued job is a no-op if the worker claims it (select -> ran=False -> completed,
no org write); created rows are cleaned up. Run: `python tests/test_s4_execution_jobs.py`.
"""
import sys
from uuid import uuid4

from primeqa.app import app
from primeqa.db import SessionLocal
from primeqa.core.models import Environment
from sqlalchemy import text

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


def _nonprod_env_id():
    db = SessionLocal()
    try:
        env = (db.query(Environment)
               .filter_by(tenant_id=TENANT_ID, is_active=True)
               .filter(Environment.is_production.is_(False)).first())
        return env.id if env else None
    finally:
        db.close()


def _cleanup_jobs(test_id: str):
    db = SessionLocal()
    try:
        db.execute(text(
            'DELETE FROM tenant_1.s4_execution_jobs WHERE test_id = CAST(:t AS uuid)'),
            {"t": test_id})
        db.commit()
    finally:
        db.close()


def run_tests():
    results = []
    print("\n=== S4 Execution Enqueue Route Tests ===\n")

    token = login_api("admin@primeqa.io", "changeme123")
    if not token:
        print("  SKIP: could not log in (admin@primeqa.io).")
        return True
    env_id = _nonprod_env_id()
    if env_id is None:
        print("  SKIP: tenant 1 has no active non-production env fixture.")
        return True
    H = {"Authorization": f"Bearer {token}"}

    def test_400_on_missing_fields():
        r = client.post("/api/s4-execution-jobs", headers=H,
                        json={"environment_id": env_id})       # no test_id
        assert r.status_code == 400, f"{r.status_code} {r.data[:200]}"
        assert r.get_json()["error"]["code"] == "BAD_REQUEST"
    results.append(test("400 on missing test_id", test_400_on_missing_fields))

    def test_400_on_bad_uuid():
        r = client.post("/api/s4-execution-jobs", headers=H,
                        json={"test_id": "not-a-uuid", "environment_id": env_id})
        assert r.status_code == 400, f"{r.status_code} {r.data[:200]}"
    results.append(test("400 on malformed test_id", test_400_on_bad_uuid))

    def test_404_on_unknown_env():
        r = client.post("/api/s4-execution-jobs", headers=H,
                        json={"test_id": str(uuid4()), "environment_id": 9_999_999})
        assert r.status_code == 404, f"{r.status_code} {r.data[:200]}"
    results.append(test("404 on unknown environment", test_404_on_unknown_env))

    def test_202_and_status_poll():
        # A random test_id (no approved recipe) → if the live worker claims it, it
        # no-ops (select → ran=False → completed; no org write). Idempotency is
        # proven deterministically offline (test_intake.py) — asserting it here
        # would race the live worker terminalizing the job between two POSTs.
        tid = str(uuid4())
        try:
            r1 = client.post("/api/s4-execution-jobs", headers=H,
                             json={"test_id": tid, "environment_id": env_id})
            assert r1.status_code == 202, f"{r1.status_code} {r1.data[:200]}"
            j1 = r1.get_json()
            assert j1["status"] == "queued", f"status={j1.get('status')}"
            assert isinstance(j1["job_id"], int)
            # status poll (job_id + test_id survive any lifecycle change).
            rs = client.get(f"/api/s4-execution-jobs/{j1['job_id']}", headers=H)
            assert rs.status_code == 200, f"{rs.status_code} {rs.data[:200]}"
            body = rs.get_json()
            assert body["job_id"] == j1["job_id"] and body["test_id"] == tid
        finally:
            _cleanup_jobs(tid)
    results.append(test("202 + status poll", test_202_and_status_poll))

    def test_status_404_unknown_job():
        r = client.get("/api/s4-execution-jobs/999999999", headers=H)
        assert r.status_code == 404, f"{r.status_code} {r.data[:200]}"
    results.append(test("404 on unknown job id (poll)", test_status_404_unknown_job))

    passed = sum(1 for x in results if x)
    print(f"\n{passed}/{len(results)} passed\n")
    return passed == len(results)


if __name__ == "__main__":
    sys.exit(0 if run_tests() else 1)
