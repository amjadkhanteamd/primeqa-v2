"""Tests for the execution engine — step executor, adaptive capture, idempotency."""

import sys
import os
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

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


def setup_db_context():
    """Create all needed DB rows for testing the executor."""
    db = SessionLocal()
    from primeqa.core.models import Environment
    from primeqa.metadata.models import MetaVersion
    from primeqa.execution.models import PipelineRun
    from primeqa.test_management.models import TestCase, TestCaseVersion, Section

    env = db.query(Environment).filter(Environment.name == "Executor Test Env").first()
    if not env:
        env = Environment(
            tenant_id=1, name="Executor Test Env", env_type="sandbox",
            sf_instance_url="https://test.sf.com", sf_api_version="59.0",
            capture_mode="smart",
        )
        db.add(env)
        db.commit()
        db.refresh(env)

    mv = MetaVersion(environment_id=env.id, version_label="v1", status="complete")
    db.add(mv)
    db.commit()
    db.refresh(mv)

    run = PipelineRun(
        tenant_id=1, environment_id=env.id, triggered_by=1,
        run_type="full", source_type="requirements", source_ids=[1],
        cancellation_token="test-cancel-token", status="running",
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    section = db.query(Section).filter(Section.tenant_id == 1).first()
    if not section:
        section = Section(tenant_id=1, name="Test Section", created_by=1)
        db.add(section)
        db.commit()
        db.refresh(section)

    tc = TestCase(
        tenant_id=1, title="Test Opp Create", owner_id=1, created_by=1,
        section_id=section.id, visibility="shared", status="active",
    )
    db.add(tc)
    db.commit()
    db.refresh(tc)

    tcv = TestCaseVersion(
        test_case_id=tc.id, version_number=1,
        metadata_version_id=mv.id, generation_method="manual",
        steps=[
            {"step_order": 1, "action": "create", "target_object": "Opportunity",
             "field_values": {"StageName": "Prospecting", "CloseDate": "2026-12-31", "Amount": 50000},
             "state_ref": "$opp_id", "expected_result": "Created"},
            {"step_order": 2, "action": "update", "target_object": "Opportunity",
             "record_ref": "$opp_id",
             "field_values": {"StageName": "Closed Won"},
             "expected_result": "Updated"},
            {"step_order": 3, "action": "verify", "target_object": "Opportunity",
             "record_ref": "$opp_id",
             "assertions": {"StageName": "Closed Won"},
             "expected_result": "Verified"},
            {"step_order": 4, "action": "query", "target_object": "Opportunity",
             "soql": "SELECT Id, StageName FROM Opportunity WHERE Id = $opp_id",
             "expected_result": "Queried"},
            {"step_order": 5, "action": "delete", "target_object": "Opportunity",
             "record_ref": "$opp_id",
             "expected_result": "Deleted"},
        ],
        referenced_entities=["Opportunity.StageName", "Opportunity.Amount"],
        created_by=1,
    )
    db.add(tcv)
    db.commit()
    db.refresh(tcv)
    tc.current_version_id = tcv.id
    db.commit()

    return db, env, run, tc, tcv, mv


def make_mock_sf():
    """Create a mock SalesforceExecutionClient."""
    sf = MagicMock()
    sf.create_record.return_value = {
        "api_request": {"method": "POST", "url": "/sobjects/Opportunity/", "body": {}},
        "api_response": {"status_code": 201, "body": {"id": "006TEST00001", "success": True}},
        "success": True, "record_id": "006TEST00001",
    }
    sf.update_record.return_value = {
        "api_request": {"method": "PATCH", "url": "/sobjects/Opportunity/006TEST00001", "body": {}},
        "api_response": {"status_code": 204, "body": None},
        "success": True, "record_id": None,
    }
    sf.delete_record.return_value = {
        "api_request": {"method": "DELETE", "url": "/sobjects/Opportunity/006TEST00001", "body": None},
        "api_response": {"status_code": 204, "body": None},
        "success": True, "record_id": None,
    }
    sf.query.return_value = {
        "api_request": {"method": "GET", "url": "/query/", "body": {}},
        "api_response": {"status_code": 200, "body": {
            "totalSize": 1, "records": [{"Id": "006TEST00001", "StageName": "Closed Won"}],
        }},
        "success": True, "record_id": None,
    }
    sf.get_record.return_value = {
        "api_request": {"method": "GET", "url": "/sobjects/Opportunity/006TEST00001", "body": {}},
        "api_response": {"status_code": 200, "body": {
            "Id": "006TEST00001", "StageName": "Closed Won", "Amount": 50000,
            "attributes": {"type": "Opportunity"},
        }},
        "success": True, "record_id": "006TEST00001",
    }
    sf.record_exists.return_value = True
    return sf


def run_tests():
    results = []
    print("\n=== Execution Engine Tests ===\n")

    db, env, run, tc, tcv, mv = setup_db_context()

    from primeqa.execution.repository import (
        RunTestResultRepository, RunStepResultRepository, RunCreatedEntityRepository,
    )
    from primeqa.execution.executor import StepExecutor
    from primeqa.execution.idempotency import IdempotencyManager

    test_result_repo = RunTestResultRepository(db)
    step_result_repo = RunStepResultRepository(db)
    entity_repo = RunCreatedEntityRepository(db)

    sf = make_mock_sf()
    idem = IdempotencyManager(entity_repo, sf)

    rtr = test_result_repo.create_result(
        run_id=run.id, test_case_id=tc.id,
        test_case_version_id=tcv.id, environment_id=env.id,
    )

    # 1. Create step
    def test_create_step():
        executor = StepExecutor(sf, run.id, "smart", step_result_repo, entity_repo, idem)
        step, status = executor.execute_step(rtr.id, tcv.steps[0])
        assert status == "passed", f"Expected passed, got {status}"
        sf.create_record.assert_called()
        call_args = sf.create_record.call_args
        assert "PQA_" in call_args[0][1].get("Name", ""), "Name should have PQA_ prefix"
    results.append(test("1. Create step executes POST with PQA_ naming", test_create_step))

    # 2. State variable resolution
    def test_state_vars():
        executor = StepExecutor(sf, run.id, "smart", step_result_repo, entity_repo, idem)
        executor.execute_step(rtr.id, tcv.steps[0])
        assert "opp_id" in executor.state_vars, f"Expected opp_id in vars, got {executor.state_vars}"
        assert executor.state_vars["opp_id"] == "006TEST00001"
    results.append(test("2. State variable $opp_id resolved from create", test_state_vars))

    # 3. Update step uses resolved ref
    def test_update_step():
        executor = StepExecutor(sf, run.id, "smart", step_result_repo, entity_repo, idem)
        executor.execute_step(rtr.id, tcv.steps[0])
        step, status = executor.execute_step(rtr.id, tcv.steps[1])
        assert status == "passed"
        sf.update_record.assert_called_with("Opportunity", "006TEST00001", {"StageName": "Closed Won"})
    results.append(test("3. Update step uses resolved $opp_id", test_update_step))

    # 4. Verify step asserts field values
    def test_verify_step():
        executor = StepExecutor(sf, run.id, "smart", step_result_repo, entity_repo, idem)
        executor.execute_step(rtr.id, tcv.steps[0])
        step, status = executor.execute_step(rtr.id, tcv.steps[2])
        assert status == "passed"
        sf.get_record.assert_called()
    results.append(test("4. Verify step checks field values", test_verify_step))

    # 5. Query step builds SOQL
    def test_query_step():
        executor = StepExecutor(sf, run.id, "smart", step_result_repo, entity_repo, idem)
        executor.execute_step(rtr.id, tcv.steps[0])
        step, status = executor.execute_step(rtr.id, tcv.steps[3])
        assert status == "passed"
        sf.query.assert_called()
    results.append(test("5. Query step executes SOQL", test_query_step))

    # 6. Delete step
    def test_delete_step():
        executor = StepExecutor(sf, run.id, "smart", step_result_repo, entity_repo, idem)
        executor.execute_step(rtr.id, tcv.steps[0])
        step, status = executor.execute_step(rtr.id, tcv.steps[4])
        assert status == "passed"
        sf.delete_record.assert_called_with("Opportunity", "006TEST00001")
    results.append(test("6. Delete step executes DELETE", test_delete_step))

    # 7. Adaptive capture — smart mode: no capture on clean success
    def test_smart_no_capture():
        sf2 = make_mock_sf()
        executor = StepExecutor(sf2, run.id, "smart", step_result_repo, entity_repo,
                                IdempotencyManager(entity_repo, sf2))
        rtr2 = test_result_repo.create_result(
            run_id=run.id, test_case_id=tc.id,
            test_case_version_id=tcv.id, environment_id=env.id,
        )
        step, status = executor.execute_step(rtr2.id, {
            "step_order": 10, "action": "update", "target_object": "Account",
            "record_ref": "001TEST00001",
            "field_values": {"Description": "Updated"},
        })
        assert status == "passed"
        updated = step_result_repo.list_step_results(rtr2.id)[-1]
        assert updated.before_state is None, "Smart mode should NOT capture on clean non-critical update"
    results.append(test("7. Smart mode: no capture on clean non-critical success", test_smart_no_capture))

    # 8. Adaptive capture — smart mode: capture on critical field
    def test_smart_capture_critical():
        sf3 = make_mock_sf()
        executor = StepExecutor(sf3, run.id, "smart", step_result_repo, entity_repo,
                                IdempotencyManager(entity_repo, sf3))
        rtr3 = test_result_repo.create_result(
            run_id=run.id, test_case_id=tc.id,
            test_case_version_id=tcv.id, environment_id=env.id,
        )
        step, status = executor.execute_step(rtr3.id, {
            "step_order": 11, "action": "update", "target_object": "Opportunity",
            "record_ref": "006TEST00001",
            "field_values": {"StageName": "Closed Won"},
        })
        updated = step_result_repo.list_step_results(rtr3.id)[-1]
        assert updated.after_state is not None, "Smart mode SHOULD capture on critical field (StageName)"
    results.append(test("8. Smart mode: captures on critical field (StageName)", test_smart_capture_critical))

    # 9. Adaptive capture — full mode: always captures
    def test_full_capture():
        sf4 = make_mock_sf()
        sf4.get_record.return_value = {
            "api_request": {}, "api_response": {"status_code": 200, "body": {
                "Id": "001TEST00001", "Description": "Before", "attributes": {"type": "Account"},
            }},
            "success": True, "record_id": "001TEST00001",
        }
        executor = StepExecutor(sf4, run.id, "full", step_result_repo, entity_repo,
                                IdempotencyManager(entity_repo, sf4))
        rtr4 = test_result_repo.create_result(
            run_id=run.id, test_case_id=tc.id,
            test_case_version_id=tcv.id, environment_id=env.id,
        )
        step, status = executor.execute_step(rtr4.id, {
            "step_order": 12, "action": "update", "target_object": "Account",
            "record_ref": "001TEST00001",
            "field_values": {"Description": "After"},
        })
        updated = step_result_repo.list_step_results(rtr4.id)[-1]
        assert updated.before_state is not None, "Full mode should ALWAYS capture before_state"
        assert updated.after_state is not None, "Full mode should ALWAYS capture after_state"
    results.append(test("9. Full mode: always captures before/after", test_full_capture))

    # 10. Minimal mode: only api_request/response
    def test_minimal_capture():
        sf5 = make_mock_sf()
        executor = StepExecutor(sf5, run.id, "minimal", step_result_repo, entity_repo,
                                IdempotencyManager(entity_repo, sf5))
        rtr5 = test_result_repo.create_result(
            run_id=run.id, test_case_id=tc.id,
            test_case_version_id=tcv.id, environment_id=env.id,
        )
        step, status = executor.execute_step(rtr5.id, {
            "step_order": 13, "action": "update", "target_object": "Opportunity",
            "record_ref": "006TEST00001",
            "field_values": {"StageName": "Closed Won"},
        })
        updated = step_result_repo.list_step_results(rtr5.id)[-1]
        assert updated.before_state is None, "Minimal mode should NOT capture before_state"
        assert updated.after_state is None, "Minimal mode should NOT capture after_state"
        assert updated.api_request is not None, "Should still have api_request"
    results.append(test("10. Minimal mode: only api_request/response", test_minimal_capture))

    # 11. Idempotency — reuses existing record
    def test_idempotency_reuse():
        sf6 = make_mock_sf()
        executor = StepExecutor(sf6, run.id, "smart", step_result_repo, entity_repo,
                                IdempotencyManager(entity_repo, sf6))
        rtr6 = test_result_repo.create_result(
            run_id=run.id, test_case_id=tc.id,
            test_case_version_id=tcv.id, environment_id=env.id,
        )
        executor.execute_step(rtr6.id, tcv.steps[0])
        sf6.create_record.reset_mock()

        executor2 = StepExecutor(sf6, run.id, "smart", step_result_repo, entity_repo,
                                 IdempotencyManager(entity_repo, sf6))
        rtr7 = test_result_repo.create_result(
            run_id=run.id, test_case_id=tc.id,
            test_case_version_id=tcv.id, environment_id=env.id,
        )
        step, status = executor2.execute_step(rtr7.id, tcv.steps[0])
        assert status == "passed"
        sf6.create_record.assert_not_called()
    results.append(test("11. Idempotency: reuses existing record on retry", test_idempotency_reuse))

    # 12. Execution state transitions
    def test_execution_states():
        sf7 = make_mock_sf()
        executor = StepExecutor(sf7, run.id, "smart", step_result_repo, entity_repo,
                                IdempotencyManager(entity_repo, sf7))
        rtr8 = test_result_repo.create_result(
            run_id=run.id, test_case_id=tc.id,
            test_case_version_id=tcv.id, environment_id=env.id,
        )
        step, status = executor.execute_step(rtr8.id, {
            "step_order": 20, "action": "update", "target_object": "Account",
            "record_ref": "001TEST00001",
            "field_values": {"Description": "Test"},
        })
        updated = step_result_repo.list_step_results(rtr8.id)[-1]
        assert updated.execution_state == "completed", \
            f"Expected completed, got {updated.execution_state}"
    results.append(test("12. Execution state: not_started → in_progress → completed", test_execution_states))

    # 13. run_test_results row created
    def test_result_row():
        assert rtr is not None
        assert rtr.run_id == run.id
        assert rtr.test_case_id == tc.id
    results.append(test("13. run_test_results row created correctly", test_result_row))

    # 14. run_step_results rows created
    def test_step_rows():
        steps = step_result_repo.list_step_results(rtr.id)
        assert len(steps) > 0, "No step results found"
        for s in steps:
            assert s.step_action in ("create", "update", "query", "verify", "delete")
    results.append(test("14. run_step_results rows created", test_step_rows))

    # 15. Smart mode: capture on failure
    def test_smart_capture_failure():
        sf8 = make_mock_sf()
        sf8.update_record.return_value = {
            "api_request": {"method": "PATCH", "url": "/test", "body": {}},
            "api_response": {"status_code": 400, "body": [{"message": "VR failed"}]},
            "success": False, "record_id": None,
        }
        executor = StepExecutor(sf8, run.id, "smart", step_result_repo, entity_repo,
                                IdempotencyManager(entity_repo, sf8))
        rtr9 = test_result_repo.create_result(
            run_id=run.id, test_case_id=tc.id,
            test_case_version_id=tcv.id, environment_id=env.id,
        )
        step, status = executor.execute_step(rtr9.id, {
            "step_order": 30, "action": "update", "target_object": "Account",
            "record_ref": "001TEST00001",
            "field_values": {"Description": "Bad"},
        })
        assert status == "failed"
        updated = step_result_repo.list_step_results(rtr9.id)[-1]
        assert updated.after_state is not None, "Smart mode should capture on failure"
    results.append(test("15. Smart mode: captures state on failure", test_smart_capture_failure))

    db.close()

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
