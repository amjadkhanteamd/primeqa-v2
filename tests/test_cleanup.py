"""Tests for the cleanup engine — reverse deletion, retry, production safety."""

import sys
import os
import uuid
from unittest.mock import MagicMock
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from primeqa.app import app
from primeqa.db import SessionLocal


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


def setup():
    db = SessionLocal()
    from primeqa.core.models import Environment
    from primeqa.execution.models import PipelineRun, RunStepResult, RunTestResult, RunCreatedEntity

    env = db.query(Environment).filter(Environment.name == "Cleanup Test Env").first()
    if not env:
        env = Environment(
            tenant_id=1, name="Cleanup Test Env", env_type="sandbox",
            sf_instance_url="https://test.sf.com", sf_api_version="59.0",
            cleanup_mandatory=False,
        )
        db.add(env)
        db.commit()
        db.refresh(env)

    run = PipelineRun(
        tenant_id=1, environment_id=env.id, triggered_by=1,
        run_type="full", source_type="requirements", source_ids=[1],
        cancellation_token="cleanup-test", status="running",
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    from primeqa.test_management.models import Section, TestCase, TestCaseVersion
    from primeqa.metadata.models import MetaVersion

    mv = MetaVersion(environment_id=env.id, version_label=f"vc{uuid.uuid4().hex[:6]}", status="complete")
    db.add(mv)
    db.commit()
    db.refresh(mv)

    section = db.query(Section).filter(Section.tenant_id == 1).first()
    if not section:
        section = Section(tenant_id=1, name="Cleanup Section", created_by=1)
        db.add(section)
        db.commit()
        db.refresh(section)

    tc = TestCase(tenant_id=1, title="Cleanup Test", owner_id=1, created_by=1,
                  section_id=section.id, visibility="shared", status="active")
    db.add(tc)
    db.commit()
    db.refresh(tc)

    tcv = TestCaseVersion(test_case_id=tc.id, version_number=1,
                          metadata_version_id=mv.id, generation_method="manual",
                          steps=[], referenced_entities=[], created_by=1)
    db.add(tcv)
    db.commit()
    db.refresh(tcv)

    rtr = RunTestResult(run_id=run.id, test_case_id=tc.id,
                        test_case_version_id=tcv.id, environment_id=env.id,
                        status="passed")
    db.add(rtr)
    db.commit()
    db.refresh(rtr)

    rsr = RunStepResult(run_test_result_id=rtr.id, step_order=1,
                        step_action="create", status="passed",
                        execution_state="completed", target_object="Account")
    db.add(rsr)
    db.commit()
    db.refresh(rsr)

    now = datetime.now(timezone.utc)
    parent = RunCreatedEntity(
        run_id=run.id, run_step_result_id=rsr.id,
        entity_type="Account", sf_record_id="001PARENT01",
        creation_source="direct", logical_identifier="parent_account",
        primeqa_idempotency_key=f"{run.id}_1_Account_parent",
        created_at=now - timedelta(seconds=10),
    )
    db.add(parent)
    db.commit()
    db.refresh(parent)

    child = RunCreatedEntity(
        run_id=run.id, run_step_result_id=rsr.id,
        entity_type="Contact", sf_record_id="003CHILD01",
        creation_source="direct", logical_identifier="child_contact",
        primeqa_idempotency_key=f"{run.id}_2_Contact_child",
        created_at=now - timedelta(seconds=5),
    )
    db.add(child)
    db.commit()
    db.refresh(child)

    trigger_entity = RunCreatedEntity(
        run_id=run.id, run_step_result_id=rsr.id,
        entity_type="Task", sf_record_id="00TTRIGGER01",
        creation_source="trigger", logical_identifier="triggered_task",
        parent_entity_id=parent.id,
        created_at=now - timedelta(seconds=3),
    )
    db.add(trigger_entity)
    db.commit()
    db.refresh(trigger_entity)

    return db, env, run, [parent, child, trigger_entity]


def make_mock_sf(dependency_fail_ids=None, already_deleted_ids=None):
    dependency_fail_ids = dependency_fail_ids or set()
    already_deleted_ids = already_deleted_ids or set()

    def mock_delete(sobject, record_id):
        if record_id in already_deleted_ids:
            return {
                "api_request": {"method": "DELETE"},
                "api_response": {"status_code": 400,
                    "body": [{"errorCode": "ENTITY_IS_DELETED", "message": "entity is deleted"}]},
                "success": False,
            }
        if record_id in dependency_fail_ids:
            dependency_fail_ids.discard(record_id)
            return {
                "api_request": {"method": "DELETE"},
                "api_response": {"status_code": 400,
                    "body": [{"errorCode": "DELETE_FAILED", "message": "related records exist"}]},
                "success": False,
            }
        return {
            "api_request": {"method": "DELETE"},
            "api_response": {"status_code": 204, "body": None},
            "success": True,
        }

    sf = MagicMock()
    sf.delete_record = MagicMock(side_effect=mock_delete)
    sf.query.return_value = {
        "success": True,
        "api_response": {"status_code": 200, "body": {"records": []}},
    }
    return sf


def run_tests():
    results = []
    print("\n=== Cleanup Engine Tests ===\n")

    db, env, run, entities = setup()
    parent, child, trigger_entity = entities
    run_id = run.id
    parent_id = parent.id
    env_id = env.id

    from primeqa.execution.repository import RunCreatedEntityRepository
    from primeqa.execution.cleanup import CleanupEngine, CleanupAttemptRepository

    entity_repo = RunCreatedEntityRepository(db)
    cleanup_repo = CleanupAttemptRepository(db)

    # 1. Reverse-order deletion
    def test_reverse_order():
        sf = make_mock_sf()
        engine = CleanupEngine(entity_repo, cleanup_repo, sf)
        ordered = engine._build_deletion_order(
            entity_repo.list_entities_for_cleanup(run_id)
        )
        types = [e.entity_type for e in ordered]
        trigger_idx = next(i for i, e in enumerate(ordered) if e.creation_source == "trigger")
        parent_idx = next(i for i, e in enumerate(ordered)
                         if e.entity_type == "Account" and e.creation_source == "direct")
        assert trigger_idx < parent_idx, \
            f"Trigger entity should be before parent: {types}"
    results.append(test("1. Trigger-created entities deleted before parents", test_reverse_order))

    # 2. Clean deletion succeeds
    def test_clean_delete():
        sf = make_mock_sf()
        engine = CleanupEngine(entity_repo, cleanup_repo, sf)
        result = engine.run_cleanup(run_id, env)
        assert result["cleaned"] == 3, f"Expected 3 cleaned, got {result['cleaned']}"
        assert result["failed"] == 0
        assert len(result["orphaned"]) == 0
    results.append(test("2. Clean deletion of all 3 entities", test_clean_delete))

    # 3. Cleanup attempts tracked
    def test_attempt_tracking():
        from primeqa.execution.models import RunCleanupAttempt
        db_at = SessionLocal()
        attempts = db_at.query(RunCleanupAttempt).filter(
            RunCleanupAttempt.run_created_entity_id == parent_id,
        ).all()
        assert len(attempts) >= 1, f"Expected at least 1 attempt, got {len(attempts)}"
        assert attempts[-1].status == "success"
        db_at.close()
    results.append(test("3. Cleanup attempts tracked in DB", test_attempt_tracking))

    # 4. Dependency failure retry
    def test_dependency_retry():
        db2, env2, run2, entities2 = setup()
        parent2, child2, trig2 = entities2
        entity_repo2 = RunCreatedEntityRepository(db2)
        cleanup_repo2 = CleanupAttemptRepository(db2)

        sf = make_mock_sf(dependency_fail_ids={parent2.sf_record_id})
        engine = CleanupEngine(entity_repo2, cleanup_repo2, sf)
        result = engine.run_cleanup(run2.id, env2)

        assert result["cleaned"] == 3, f"Expected 3 cleaned, got {result['cleaned']}"
        assert result["failed"] == 0, f"Expected 0 failed, got {result['failed']}"

        from primeqa.execution.models import RunCleanupAttempt
        attempts = db2.query(RunCleanupAttempt).filter(
            RunCleanupAttempt.run_created_entity_id == parent2.id,
        ).all()
        assert len(attempts) >= 2, f"Expected >= 2 attempts (initial fail + retry), got {len(attempts)}"
        db2.close()
    results.append(test("4. Dependency failure retried in second pass", test_dependency_retry))

    # 5. Already-deleted entities handled
    def test_already_deleted():
        db3, env3, run3, entities3 = setup()
        entity_repo3 = RunCreatedEntityRepository(db3)
        cleanup_repo3 = CleanupAttemptRepository(db3)

        sf = make_mock_sf(already_deleted_ids={entities3[0].sf_record_id})
        engine = CleanupEngine(entity_repo3, cleanup_repo3, sf)
        result = engine.run_cleanup(run3.id, env3)

        assert result["cleaned"] == 3, f"Expected 3 cleaned, got {result['cleaned']}"
        assert result["failed"] == 0
        db3.close()
    results.append(test("5. Already-deleted entities handled as success", test_already_deleted))

    # 6. Production safety — orphaned records
    def test_production_safety():
        db4, env4, run4, entities4 = setup()
        env4.cleanup_mandatory = True
        db4.commit()

        entity_repo4 = RunCreatedEntityRepository(db4)
        cleanup_repo4 = CleanupAttemptRepository(db4)

        def always_fail(sobject, record_id):
            return {
                "api_request": {"method": "DELETE"},
                "api_response": {"status_code": 403, "body": [{"message": "No access"}]},
                "success": False,
            }

        sf = MagicMock()
        sf.delete_record = MagicMock(side_effect=always_fail)
        engine = CleanupEngine(entity_repo4, cleanup_repo4, sf)
        result = engine.run_cleanup(run4.id, env4)

        assert result["failed"] == 3, f"Expected 3 failed, got {result['failed']}"
        assert result["cleanup_mandatory"] == True
        assert len(result["orphaned"]) == 3

        from primeqa.core.models import ActivityLog
        log_entry = db4.query(ActivityLog).filter(
            ActivityLog.action == "cleanup.incomplete",
            ActivityLog.entity_id == run4.id,
        ).first()
        assert log_entry is not None, "Should create activity log for incomplete cleanup"
        db4.close()
    results.append(test("6. Production safety: orphans logged when cleanup_mandatory", test_production_safety))

    # 7. Cleanup status endpoint
    def test_cleanup_status():
        db7 = SessionLocal()
        entity_repo7 = RunCreatedEntityRepository(db7)
        cleanup_repo7 = CleanupAttemptRepository(db7)
        engine = CleanupEngine(entity_repo7, cleanup_repo7)
        status = engine.get_cleanup_status(run_id)
        assert len(status) >= 3, f"Expected >= 3 entities, got {len(status)}"
        for s in status:
            assert "entity_type" in s
            assert "cleanup_status" in s
            assert "attempts" in s
        db7.close()
    results.append(test("7. Cleanup status returns entity details", test_cleanup_status))

    # 8. Orphaned records query
    def test_orphaned_query():
        db5, env5, run5, entities5 = setup()
        entity_repo5 = RunCreatedEntityRepository(db5)
        cleanup_repo5 = CleanupAttemptRepository(db5)
        engine = CleanupEngine(entity_repo5, cleanup_repo5)
        orphaned = engine.get_orphaned_records(env5.id)
        assert isinstance(orphaned, list)
        assert len(orphaned) >= 3
        db5.close()
    results.append(test("8. Orphaned records query works", test_orphaned_query))

    # 9. 3-pass handles cascading deps
    def test_three_pass():
        db6, env6, run6, entities6 = setup()
        entity_repo6 = RunCreatedEntityRepository(db6)
        cleanup_repo6 = CleanupAttemptRepository(db6)

        call_count = {}
        for e in entities6:
            call_count[e.sf_record_id] = 0

        def cascading_fail(sobject, record_id):
            call_count[record_id] = call_count.get(record_id, 0) + 1
            if call_count[record_id] <= 2 and record_id == entities6[0].sf_record_id:
                return {
                    "api_request": {"method": "DELETE"},
                    "api_response": {"status_code": 400,
                        "body": [{"errorCode": "DELETE_FAILED", "message": "related records"}]},
                    "success": False,
                }
            return {
                "api_request": {"method": "DELETE"},
                "api_response": {"status_code": 204, "body": None},
                "success": True,
            }

        sf = MagicMock()
        sf.delete_record = MagicMock(side_effect=cascading_fail)
        engine = CleanupEngine(entity_repo6, cleanup_repo6, sf)
        result = engine.run_cleanup(run6.id, env6)

        assert result["cleaned"] == 3, f"Expected 3 cleaned after 3 passes, got {result['cleaned']}"
        db6.close()
    results.append(test("9. 3-pass cleanup handles cascading dependencies", test_three_pass))

    db.close()

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
