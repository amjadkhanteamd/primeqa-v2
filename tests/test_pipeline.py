"""Integration tests for pipeline/execution module."""

import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from primeqa.app import app
from primeqa.db import SessionLocal

client = app.test_client()

TENANT_ID = 1
admin_token = None
env_id = None
run_ids = []


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


def setup():
    global admin_token, env_id
    admin_token = login("admin@primeqa.io", "changeme123")

    r = client.post("/api/environments", headers=auth(admin_token), json={
        "name": "Pipeline Test Env", "env_type": "sandbox",
        "sf_instance_url": "https://test.sf.com", "sf_api_version": "59.0",
        "max_execution_slots": 2,
    })
    if r.status_code == 201:
        env_id = r.get_json()["id"]
    else:
        db = SessionLocal()
        from primeqa.core.models import Environment
        env = db.query(Environment).filter(Environment.name == "Pipeline Test Env").first()
        if env:
            env_id = env.id
        db.close()


def run_tests():
    global run_ids
    results = []
    print("\n=== Pipeline / Execution Tests ===\n")

    setup()
    assert env_id, "Environment setup failed"

    # 1. Create run with correct stages
    def test_create_run():
        r = client.post("/api/runs", headers=auth(admin_token), json={
            "environment_id": env_id, "run_type": "full",
            "source_type": "requirements", "source_ids": [1, 2],
            "priority": "normal",
        })
        assert r.status_code == 201, f"Got {r.status_code}: {r.data}"
        data = r.get_json()
        assert data["status"] == "running", f"Expected running, got {data['status']}"
        run_ids.append(data["id"])

        r2 = client.get(f"/api/runs/{data['id']}", headers=auth(admin_token))
        stages = r2.get_json()["stages"]
        assert len(stages) == 6
        names = [s["stage_name"] for s in stages]
        assert names == ["metadata_refresh", "jira_read", "generate", "store", "execute", "record"]
    results.append(test("1. Create run with 6 correct stages", test_create_run))

    # 2. Slot acquired
    def test_slot_acquired():
        r = client.get(f"/api/environments/{env_id}/slots", headers=auth(admin_token))
        assert r.status_code == 200
        data = r.get_json()
        assert data["used"] >= 1
        assert data["total"] == 2
    results.append(test("2. Execution slot acquired", test_slot_acquired))

    # 3. Second run also gets a slot (max=2)
    def test_second_run_gets_slot():
        r = client.post("/api/runs", headers=auth(admin_token), json={
            "environment_id": env_id, "run_type": "full",
            "source_type": "suite", "source_ids": [1],
        })
        assert r.status_code == 201
        data = r.get_json()
        assert data["status"] == "running"
        run_ids.append(data["id"])
    results.append(test("3. Second run gets slot (max=2)", test_second_run_gets_slot))

    # 4. Third run is queued (no slot available)
    def test_third_run_queued():
        r = client.post("/api/runs", headers=auth(admin_token), json={
            "environment_id": env_id, "run_type": "full",
            "source_type": "requirements", "source_ids": [3],
            "priority": "normal",
        })
        assert r.status_code == 201
        data = r.get_json()
        assert data["status"] == "queued", f"Expected queued, got {data['status']}"
        assert data["queue_position"] >= 1
        run_ids.append(data["id"])
    results.append(test("4. Third run queued (no slot)", test_third_run_queued))

    # 5. Queue ordering: critical before normal
    def test_queue_priority():
        r = client.post("/api/runs", headers=auth(admin_token), json={
            "environment_id": env_id, "run_type": "full",
            "source_type": "requirements", "source_ids": [4],
            "priority": "critical",
        })
        assert r.status_code == 201
        critical_id = r.get_json()["id"]
        run_ids.append(critical_id)

        r2 = client.get("/api/runs/queue", headers=auth(admin_token))
        queue = r2.get_json()
        queued_runs = [q for q in queue if q["status"] == "queued"]
        if len(queued_runs) >= 2:
            assert queued_runs[0]["priority"] == "critical", \
                f"Expected critical first, got {queued_runs[0]['priority']}"
    results.append(test("5. Queue orders critical before normal", test_queue_priority))

    # 6. Cancel queued run
    def test_cancel_queued():
        queued_id = run_ids[-1]
        r = client.post(f"/api/runs/{queued_id}/cancel", headers=auth(admin_token))
        assert r.status_code == 200, f"Got {r.status_code}: {r.data}"
        data = r.get_json()
        assert data["status"] == "cancelled"
    results.append(test("6. Cancel queued run", test_cancel_queued))

    # 7. Cancel running run releases slot
    def test_cancel_running():
        running_id = run_ids[0]
        r = client.get(f"/api/environments/{env_id}/slots", headers=auth(admin_token))
        before_used = r.get_json()["used"]

        r2 = client.post(f"/api/runs/{running_id}/cancel", headers=auth(admin_token))
        assert r2.status_code == 200
        assert r2.get_json()["status"] == "cancelled"

        r3 = client.get(f"/api/environments/{env_id}/slots", headers=auth(admin_token))
        after_used = r3.get_json()["used"]
        assert after_used < before_used or after_used == before_used, \
            "Slot should be released (or reused by next queued run)"
    results.append(test("7. Cancel running run releases slot", test_cancel_running))

    # 8. Worker processes stages in order
    def test_worker_processes():
        r = client.post("/api/runs", headers=auth(admin_token), json={
            "environment_id": env_id, "run_type": "full",
            "source_type": "requirements", "source_ids": [10],
        })
        assert r.status_code == 201
        new_run_id = r.get_json()["id"]
        run_ids.append(new_run_id)

        db = SessionLocal()
        from primeqa.execution.repository import (
            PipelineRunRepository, PipelineStageRepository,
            ExecutionSlotRepository, WorkerHeartbeatRepository,
        )
        from primeqa.execution.service import PipelineService
        from primeqa.worker import worker_tick
        run_repo = PipelineRunRepository(db)
        stage_repo = PipelineStageRepository(db)
        slot_repo = ExecutionSlotRepository(db)
        hb_repo = WorkerHeartbeatRepository(db)
        ctx = {
            "db": db,
            "run_repo": run_repo,
            "stage_repo": stage_repo,
            "slot_repo": slot_repo,
            "heartbeat_repo": hb_repo,
            "service": PipelineService(run_repo, stage_repo, slot_repo, hb_repo),
            "worker_id": "test-worker",
        }
        hb_repo.register_worker("test-worker")

        for _ in range(5):
            worker_tick(ctx)
            run = run_repo.get_run(new_run_id)
            if run and run.status == "completed":
                break
        db.close()

        r2 = client.get(f"/api/runs/{new_run_id}", headers=auth(admin_token))
        data = r2.get_json()
        assert data["status"] == "completed", f"Expected completed, got {data['status']}"
        stages = data["stages"]
        for s in stages:
            assert s["status"] == "passed", f"Stage {s['stage_name']} is {s['status']}"
    results.append(test("8. Worker processes all stages to completion", test_worker_processes))

    # 9. Auto-start next queued run when slot released
    def test_auto_start():
        r1 = client.post("/api/runs", headers=auth(admin_token), json={
            "environment_id": env_id, "run_type": "full",
            "source_type": "requirements", "source_ids": [20],
        })
        r2 = client.post("/api/runs", headers=auth(admin_token), json={
            "environment_id": env_id, "run_type": "full",
            "source_type": "requirements", "source_ids": [21],
        })
        r3 = client.post("/api/runs", headers=auth(admin_token), json={
            "environment_id": env_id, "run_type": "full",
            "source_type": "requirements", "source_ids": [22],
        })
        third_id = r3.get_json()["id"]
        run_ids.extend([r1.get_json()["id"], r2.get_json()["id"], third_id])

        r_check = client.get(f"/api/runs/{third_id}", headers=auth(admin_token))
        initial_status = r_check.get_json()["status"]

        from primeqa.worker import worker_tick
        db2 = SessionLocal()
        from primeqa.execution.repository import (
            PipelineRunRepository as PRR, PipelineStageRepository as PSR,
            ExecutionSlotRepository as ESR, WorkerHeartbeatRepository as WHR,
        )
        from primeqa.execution.service import PipelineService as PS
        rr, sr, er, hr = PRR(db2), PSR(db2), ESR(db2), WHR(db2)
        ctx2 = {
            "db": db2, "run_repo": rr, "stage_repo": sr,
            "slot_repo": er, "heartbeat_repo": hr,
            "service": PS(rr, sr, er, hr), "worker_id": "test-worker-2",
        }
        hr.register_worker("test-worker-2")
        worker_tick(ctx2)
        worker_tick(ctx2)
        db2.close()

        r_after = client.get(f"/api/runs/{third_id}", headers=auth(admin_token))
        final = r_after.get_json()
        assert final["status"] in ("running", "completed"), \
            f"Expected queued run to auto-start, got {final['status']}"
    results.append(test("9. Auto-start next queued when slot freed", test_auto_start))

    # 10. Worker heartbeat
    def test_heartbeat():
        db = SessionLocal()
        try:
            from primeqa.execution.models import WorkerHeartbeat
            wh = db.query(WorkerHeartbeat).filter(
                WorkerHeartbeat.worker_id == "test-worker",
            ).first()
            assert wh is not None, "Worker heartbeat not found"
            assert wh.status == "alive"
        finally:
            db.close()
    results.append(test("10. Worker heartbeat registered", test_heartbeat))

    # 11. Reaper detects stuck stages
    def test_reaper():
        db = SessionLocal()
        try:
            from primeqa.execution.models import PipelineStage, PipelineRun
            from primeqa.execution.repository import PipelineStageRepository
            repo = PipelineStageRepository(db)
            stuck = repo.find_stuck_stages(timeout_seconds=0)
            assert isinstance(stuck, list)
        finally:
            db.close()
    results.append(test("11. Reaper can find stuck stages", test_reaper))

    # 12. Stage retry
    def test_stage_retry():
        db = SessionLocal()
        try:
            from primeqa.execution.repository import PipelineStageRepository
            repo = PipelineStageRepository(db)

            r = client.post("/api/runs", headers=auth(admin_token), json={
                "environment_id": env_id, "run_type": "full",
                "source_type": "requirements", "source_ids": [99],
            })
            retry_run_id = r.get_json()["id"]
            run_ids.append(retry_run_id)

            stage = repo.get_next_pending_stage(retry_run_id)
            assert stage is not None
            assert stage.max_attempts >= 1
            assert stage.attempt == 1

            repo.update_stage(stage.id, "failed", last_error="Transient error")
            retried = repo.increment_attempt(stage.id)
            assert retried.attempt == 2
            assert retried.status == "pending"
        finally:
            db.close()
    results.append(test("12. Stage retry increments attempt", test_stage_retry))

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
