"""Reliability fixes (Prompt 15).

Three fixes in one commit — one test section each:

  Fix 1: Transaction wrap around generate_test_plan
    1. Successful batch creation: batch + TCs + versions + reviews
       all present after commit (IDs assigned via flush, not commit)
    2. Mid-batch exception: rollback leaves zero orphan rows — no
       batch, no TCs, no versions, no reviews with the aborted
       batch's signature
    3. A simulated DB commit failure propagates the exception to the
       caller (i.e. the async generation worker can mark the job
       failed without leaving a poisoned session)

  Fix 2: Negative test result interpretation
    4. compute_negative_counts returns {0, 0} when the run has no
       step results
    5. compute_negative_counts counts expected_fail_verified
       → expected_failures
    6. compute_negative_counts counts expected_fail_unverified
       → unexpected_passes
    7. get_dashboard_data surfaces both counts under ticket_counts

  Fix 3: Verify step comparison capture
    8. _execute_verify with all fields matching → success, no
       comparison_details / assertion_summary emitted
    9. _execute_verify with N mismatches → success=False,
       comparison_details has N {field, expected, actual} rows,
       assertion_summary reads '{n} of {m} fields mismatched: ...'
   10. StepExecutor.execute_step surfaces assertion_summary to
       error_message (NOT str(body)) and writes comparison_details
       onto the RunStepResult row
"""

import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text

from primeqa.app import app
from primeqa.db import SessionLocal
from primeqa.core.models import Environment
from primeqa.execution.executor import StepExecutor
from primeqa.execution.idempotency import IdempotencyManager
from primeqa.execution.models import (
    PipelineRun, RunStepResult, RunTestResult,
)
from primeqa.execution.repository import (
    RunCreatedEntityRepository, RunStepResultRepository,
    RunTestResultRepository,
)
from primeqa.release.dashboard import compute_negative_counts, get_dashboard_data
from primeqa.test_management.models import (
    BAReview, GenerationBatch, Requirement, Section, TestCase,
    TestCaseVersion,
)
from primeqa.test_management.service import TestManagementService
from primeqa.test_management.repository import (
    BAReviewRepository, MetadataImpactRepository, RequirementRepository,
    SectionRepository, TestCaseRepository, TestSuiteRepository,
)
from primeqa.core.repository import ActivityLogRepository


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


# =============================================================================
# Fixtures
# =============================================================================

def _pick_env_and_section(db):
    """Grab any active env with a metadata version and any section."""
    env = (db.query(Environment)
           .filter(Environment.tenant_id == TENANT_ID,
                   Environment.is_active.is_(True),
                   Environment.current_meta_version_id.isnot(None))
           .first())
    section = (db.query(Section)
               .filter(Section.tenant_id == TENANT_ID,
                       Section.deleted_at.is_(None))
               .first())
    return env, section


def _mk_requirement(db, section_id, tag):
    """Make a fresh requirement we can own for this test."""
    req = Requirement(
        tenant_id=TENANT_ID,
        section_id=section_id,
        source="manual",
        jira_key=f"RELIABILITY-{tag}-{int(datetime.now(timezone.utc).timestamp())}",
        jira_summary=f"Reliability fix test requirement {tag}",
        jira_description="synthetic requirement — safe to delete",
        created_by=1,
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    return req


def _mk_service(db) -> TestManagementService:
    """Build a TestManagementService bound to the given session."""
    return TestManagementService(
        section_repo=SectionRepository(db),
        requirement_repo=RequirementRepository(db),
        test_case_repo=TestCaseRepository(db),
        suite_repo=TestSuiteRepository(db),
        review_repo=BAReviewRepository(db),
        impact_repo=MetadataImpactRepository(db),
        activity_repo=ActivityLogRepository(db),
    )


def _cleanup_batch(db, req_id):
    """Delete every row generated under this requirement for a clean assertion.

    Called in a finally block even after a raised exception, so it first
    rolls back any in-flight transaction on the session. Uses a fresh
    session for the teardown to avoid fighting with poisoned state.
    """
    try:
        db.rollback()
    except Exception:
        pass
    # Use a dedicated session: callers may still use `db` after this.
    teardown = SessionLocal()
    try:
        tc_ids = [t.id for t in teardown.query(TestCase).filter(
            TestCase.requirement_id == req_id).all()]
        if tc_ids:
            # Break the test_cases.current_version_id FK before we drop
            # versions. current_version_id is a nullable FK without
            # ondelete; explicit NULL-out is required here.
            teardown.execute(text(
                "UPDATE test_cases SET current_version_id = NULL "
                "WHERE id = ANY(:ids)"), {"ids": tc_ids})
            teardown.commit()
            version_ids = [v.id for v in teardown.query(TestCaseVersion).filter(
                TestCaseVersion.test_case_id.in_(tc_ids)).all()]
            if version_ids:
                teardown.query(BAReview).filter(
                    BAReview.test_case_version_id.in_(version_ids)).delete(
                    synchronize_session=False)
                teardown.commit()
            teardown.query(TestCaseVersion).filter(
                TestCaseVersion.test_case_id.in_(tc_ids)).delete(
                synchronize_session=False)
            teardown.commit()
            teardown.query(TestCase).filter(
                TestCase.id.in_(tc_ids)).delete(synchronize_session=False)
            teardown.commit()
        teardown.query(GenerationBatch).filter(
            GenerationBatch.requirement_id == req_id).delete(
            synchronize_session=False)
        teardown.query(Requirement).filter_by(id=req_id).delete(
            synchronize_session=False)
        teardown.commit()
    finally:
        teardown.close()


def _plan_payload():
    """Canonical LLM-plan shape that the service expects from the
    TestCaseGenerator. Two TCs so we can prove mid-batch failure
    actually aborts the second one."""
    return {
        "test_cases": [
            {
                "title": "Positive: create Opportunity with Prospecting",
                "coverage_type": "positive",
                "description": "baseline create",
                "confidence_score": 0.85,
                "steps": [
                    {"step_order": 1, "action": "create",
                     "target_object": "Opportunity",
                     "field_values": {"StageName": "Prospecting",
                                      "CloseDate": "2026-12-31",
                                      "Amount": 1000},
                     "state_ref": "$opp_id",
                     "expected_result": "Created"},
                ],
                "expected_results": ["created"],
                "preconditions": [],
                "referenced_entities": ["Opportunity.StageName"],
            },
            {
                "title": "Negative: reject invalid stage",
                "coverage_type": "negative_validation",
                "description": "invalid stage",
                "confidence_score": 0.75,
                "steps": [
                    {"step_order": 1, "action": "create",
                     "target_object": "Opportunity",
                     "field_values": {"StageName": "NotReal",
                                      "CloseDate": "2026-12-31",
                                      "Amount": 1000},
                     "state_ref": "$opp_id",
                     "expected_result": "Rejected",
                     "expect_fail": True},
                ],
                "expected_results": ["rejected"],
                "preconditions": [],
                "referenced_entities": ["Opportunity.StageName"],
            },
        ],
        "explanation": "Covers positive create + negative rejection.",
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "cost_usd": 0.01,
        "model_used": "claude-sonnet-4-20250514",
        "usage_log_id": None,
        "usage_log_ids": [],
    }


# =============================================================================
# Fix 1: Transaction wrap around generate_test_plan
# =============================================================================

def _run_fix1_tests(results):
    print("\n--- Fix 1: generate_test_plan transaction wrap ---\n")

    # Test 1: successful batch creates every row
    def test_success_atomic():
        db = SessionLocal()
        env, section = _pick_env_and_section(db)
        if env is None or section is None:
            print("    SKIP: no env / section fixture in tenant 1")
            db.close()
            return
        req = _mk_requirement(db, section.id, "f1success")
        req_id = req.id
        try:
            svc = _mk_service(db)
            plan = _plan_payload()
            with patch("primeqa.intelligence.generation.TestCaseGenerator") as gen_cls:
                gen_inst = MagicMock()
                gen_inst.generate_plan.return_value = plan
                gen_cls.return_value = gen_inst
                with patch.object(svc, "_store_validation_report"):
                    env_repo = MagicMock()
                    env_repo.get_environment.return_value = env
                    conn_repo = MagicMock()
                    conn_repo.get_connection_decrypted.return_value = {
                        "config": {"api_key": "sk-test",
                                   "model": "claude-sonnet-4-20250514"}}
                    metadata_repo = MagicMock()
                    with patch("primeqa.intelligence.validator.TestCaseValidator") as vcls:
                        v = MagicMock()
                        v._obj_by_name = {}
                        v._fields_by_obj = {}
                        v.validate.return_value = {
                            "status": "clean", "issues": [],
                            "summary": {"critical": 0}}
                        vcls.return_value = v
                        with patch("primeqa.intelligence.linter.GenerationLinter") as lcls:
                            linter = MagicMock()
                            lint_result = MagicMock()
                            lint_result.fixes_applied = []
                            lint_result.warnings = []
                            lint_result.blocked = []
                            lint_result.summary_dict.return_value = None
                            linter.lint.return_value = lint_result
                            lcls.return_value = linter
                            result = svc.generate_test_plan(
                                tenant_id=TENANT_ID,
                                requirement_id=req_id,
                                environment_id=env.id,
                                created_by=1,
                                env_repo=env_repo,
                                conn_repo=conn_repo,
                                metadata_repo=metadata_repo,
                            )
            # Assertions: 2 TCs, 2 versions, batch present
            batch_id = result["generation_batch_id"]
            assert len(result["test_cases"]) == 2, \
                f"Expected 2 TCs, got {len(result['test_cases'])}"

            db2 = SessionLocal()
            try:
                b = db2.query(GenerationBatch).filter_by(id=batch_id).first()
                assert b is not None, "batch not persisted"
                tcs = db2.query(TestCase).filter_by(
                    generation_batch_id=batch_id).all()
                assert len(tcs) == 2, f"Expected 2 TCs persisted, got {len(tcs)}"
                for tc in tcs:
                    assert tc.current_version_id is not None, \
                        f"TC-{tc.id} has no current_version_id"
                    tcv = db2.query(TestCaseVersion).filter_by(
                        id=tc.current_version_id).first()
                    assert tcv is not None, "version missing"
                    assert tcv.test_case_id == tc.id, \
                        "version not linked back to TC"
            finally:
                db2.close()
        finally:
            _cleanup_batch(db, req_id)
            db.close()
    results.append(test("1. Successful batch: 2 TCs + 2 versions committed atomically",
                        test_success_atomic))

    # Test 2: mid-batch exception leaves zero rows
    def test_rollback_on_mid_batch_failure():
        db = SessionLocal()
        env, section = _pick_env_and_section(db)
        if env is None or section is None:
            print("    SKIP: no env / section fixture in tenant 1")
            db.close()
            return
        req = _mk_requirement(db, section.id, "f1fail")
        req_id = req.id
        try:
            svc = _mk_service(db)
            plan = _plan_payload()
            call_count = {"n": 0}

            def fail_second_validate(*a, **k):
                call_count["n"] += 1
                if call_count["n"] >= 2:
                    raise RuntimeError("simulated mid-batch failure")
                return {"status": "clean", "issues": [],
                        "summary": {"critical": 0}}

            with patch("primeqa.intelligence.generation.TestCaseGenerator") as gen_cls:
                gen_inst = MagicMock()
                gen_inst.generate_plan.return_value = plan
                gen_cls.return_value = gen_inst
                env_repo = MagicMock()
                env_repo.get_environment.return_value = env
                conn_repo = MagicMock()
                conn_repo.get_connection_decrypted.return_value = {
                    "config": {"api_key": "sk-test",
                               "model": "claude-sonnet-4-20250514"}}
                metadata_repo = MagicMock()
                with patch("primeqa.intelligence.validator.TestCaseValidator") as vcls:
                    v = MagicMock()
                    v._obj_by_name = {}
                    v._fields_by_obj = {}
                    v.validate.side_effect = fail_second_validate
                    vcls.return_value = v
                    with patch("primeqa.intelligence.linter.GenerationLinter") as lcls:
                        linter = MagicMock()
                        lint_result = MagicMock()
                        lint_result.fixes_applied = []
                        lint_result.warnings = []
                        lint_result.blocked = []
                        lint_result.summary_dict.return_value = None
                        linter.lint.return_value = lint_result
                        lcls.return_value = linter
                        with patch.object(svc, "_store_validation_report"):
                            try:
                                svc.generate_test_plan(
                                    tenant_id=TENANT_ID,
                                    requirement_id=req_id,
                                    environment_id=env.id,
                                    created_by=1,
                                    env_repo=env_repo,
                                    conn_repo=conn_repo,
                                    metadata_repo=metadata_repo,
                                )
                            except RuntimeError as e:
                                assert "simulated" in str(e), e

            # The service's session has uncommitted flushed rows from
            # the aborted loop. Rollback first to clear the pending
            # state, then issue a fresh connection via the engine so
            # the query sees only COMMITTED data (not the same
            # session's identity-map leftovers). scoped_session means
            # SessionLocal() from the same thread returns `db` — we
            # bypass that with engine.connect() here.
            db.rollback()
            from primeqa.db import engine
            with engine.connect() as conn:
                leaked_tc_count = conn.execute(text(
                    "SELECT COUNT(*) FROM test_cases "
                    "WHERE requirement_id = :rid"
                ), {"rid": req_id}).scalar()
                assert leaked_tc_count == 0, \
                    f"Rollback failed — {leaked_tc_count} orphan TCs present"

                leaked_batch_count = conn.execute(text(
                    "SELECT COUNT(*) FROM generation_batches "
                    "WHERE requirement_id = :rid"
                ), {"rid": req_id}).scalar()
                assert leaked_batch_count == 0, \
                    f"Rollback failed — {leaked_batch_count} orphan batches"

                leaked_reviews = conn.execute(text(
                    "SELECT COUNT(*) FROM ba_reviews br "
                    "JOIN test_case_versions tcv ON tcv.id = br.test_case_version_id "
                    "JOIN test_cases tc ON tc.id = tcv.test_case_id "
                    "WHERE tc.requirement_id = :rid"
                ), {"rid": req_id}).scalar()
                assert leaked_reviews == 0, \
                    f"Rollback failed — {leaked_reviews} orphan reviews"
        finally:
            _cleanup_batch(db, req_id)
            db.close()
    results.append(test(
        "2. Mid-batch exception rolls entire transaction back (0 orphans)",
        test_rollback_on_mid_batch_failure))

    # Test 3: the service re-raises — caller (worker) can mark job failed
    def test_exception_propagates_to_caller():
        db = SessionLocal()
        env, section = _pick_env_and_section(db)
        if env is None or section is None:
            print("    SKIP: no env / section fixture in tenant 1")
            db.close()
            return
        req = _mk_requirement(db, section.id, "f1raise")
        req_id = req.id
        try:
            svc = _mk_service(db)
            plan = _plan_payload()
            # Single-TC plan so we can patch the final commit specifically.
            plan["test_cases"] = plan["test_cases"][:1]

            with patch("primeqa.intelligence.generation.TestCaseGenerator") as gen_cls:
                gen_inst = MagicMock()
                gen_inst.generate_plan.return_value = plan
                gen_cls.return_value = gen_inst
                env_repo = MagicMock()
                env_repo.get_environment.return_value = env
                conn_repo = MagicMock()
                conn_repo.get_connection_decrypted.return_value = {
                    "config": {"api_key": "sk-test",
                               "model": "claude-sonnet-4-20250514"}}
                metadata_repo = MagicMock()
                with patch("primeqa.intelligence.validator.TestCaseValidator") as vcls:
                    v = MagicMock()
                    v._obj_by_name = {}
                    v._fields_by_obj = {}
                    v.validate.return_value = {
                        "status": "clean", "issues": [],
                        "summary": {"critical": 0}}
                    vcls.return_value = v
                    with patch("primeqa.intelligence.linter.GenerationLinter") as lcls:
                        linter = MagicMock()
                        lint_result = MagicMock()
                        lint_result.fixes_applied = []
                        lint_result.warnings = []
                        lint_result.blocked = []
                        lint_result.summary_dict.return_value = None
                        linter.lint.return_value = lint_result
                        lcls.return_value = linter
                        with patch.object(svc, "_store_validation_report"):
                            # Poison the final commit — the per-loop
                            # flush()es all succeeded, but the big
                            # batch-terminating commit blows up.
                            original_commit = svc.test_case_repo.db.commit
                            commit_count = {"n": 0}

                            def boom_on_final_commit():
                                commit_count["n"] += 1
                                # The method does 1 commit (the final
                                # one post-loop). That's the one to
                                # explode.
                                raise RuntimeError("DB goes boom")

                            svc.test_case_repo.db.commit = boom_on_final_commit
                            raised = False
                            try:
                                svc.generate_test_plan(
                                    tenant_id=TENANT_ID,
                                    requirement_id=req_id,
                                    environment_id=env.id,
                                    created_by=1,
                                    env_repo=env_repo,
                                    conn_repo=conn_repo,
                                    metadata_repo=metadata_repo,
                                )
                            except RuntimeError as e:
                                assert "boom" in str(e), e
                                raised = True
                            svc.test_case_repo.db.commit = original_commit
                            assert raised, "exception did NOT propagate to caller"
        finally:
            _cleanup_batch(db, req_id)
            db.close()
    results.append(test(
        "3. Final-commit exception propagates so worker can mark job failed",
        test_exception_propagates_to_caller))


# =============================================================================
# Fix 2: Negative test result interpretation
# =============================================================================

def _insert_step_results_for_counts(db, run_id, tc_id, tcv_id, env_id,
                                    failure_classes):
    """Create one RunTestResult + N RunStepResults with the given
    failure_class values. Returns the RunTestResult for follow-up.
    """
    rtr = RunTestResult(
        run_id=run_id, test_case_id=tc_id,
        test_case_version_id=tcv_id, environment_id=env_id,
        status="passed", passed_steps=0, total_steps=len(failure_classes),
    )
    db.add(rtr); db.commit(); db.refresh(rtr)
    for i, fc in enumerate(failure_classes):
        rsr = RunStepResult(
            run_test_result_id=rtr.id,
            step_order=i + 1,
            step_action="create",
            status="passed",
            execution_state="completed",
            failure_class=fc,
        )
        db.add(rsr)
    db.commit()
    return rtr


def _run_fix2_tests(results):
    print("\n--- Fix 2: negative test result interpretation ---\n")

    def _setup_run():
        db = SessionLocal()
        env, section = _pick_env_and_section(db)
        if env is None:
            db.close()
            return None, None
        run = PipelineRun(
            tenant_id=TENANT_ID, environment_id=env.id, triggered_by=1,
            run_type="full", source_type="test_cases", source_ids=[1],
            cancellation_token=f"negct-{int(datetime.now(timezone.utc).timestamp())}",
            status="completed",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )
        db.add(run); db.commit(); db.refresh(run)
        return db, run

    def _teardown_run(db, run):
        if run is None:
            db.close()
            return
        # step results cascade via run_test_results ondelete CASCADE
        db.query(RunTestResult).filter_by(run_id=run.id).delete(
            synchronize_session=False)
        db.query(PipelineRun).filter_by(id=run.id).delete(
            synchronize_session=False)
        db.commit()
        db.close()

    # Test 4: run with no step results
    def test_empty_counts():
        db, run = _setup_run()
        if db is None:
            print("    SKIP: no env fixture in tenant 1")
            return
        try:
            counts = compute_negative_counts(db, run.id)
            assert counts == {"expected_failures": 0,
                              "unexpected_passes": 0}, counts
        finally:
            _teardown_run(db, run)
    results.append(test("4. compute_negative_counts returns 0/0 for empty run",
                        test_empty_counts))

    # Test 5: expected_fail_verified → expected_failures
    def test_count_verified():
        db, run = _setup_run()
        if db is None:
            print("    SKIP: no env fixture in tenant 1")
            return
        try:
            env, section = _pick_env_and_section(db)
            tc = (db.query(TestCase).filter(
                TestCase.tenant_id == TENANT_ID).first())
            if tc is None or tc.current_version_id is None:
                print("    SKIP: no TC+version fixture in tenant 1")
                return
            _insert_step_results_for_counts(
                db, run.id, tc.id, tc.current_version_id, env.id,
                ["expected_fail_verified", "expected_fail_verified",
                 "expected_fail_verified"])
            counts = compute_negative_counts(db, run.id)
            assert counts["expected_failures"] == 3, counts
            assert counts["unexpected_passes"] == 0, counts
        finally:
            _teardown_run(db, run)
    results.append(test(
        "5. expected_fail_verified steps counted as expected_failures",
        test_count_verified))

    # Test 6: expected_fail_unverified → unexpected_passes
    def test_count_unverified():
        db, run = _setup_run()
        if db is None:
            print("    SKIP: no env fixture in tenant 1")
            return
        try:
            env, section = _pick_env_and_section(db)
            tc = (db.query(TestCase).filter(
                TestCase.tenant_id == TENANT_ID).first())
            if tc is None or tc.current_version_id is None:
                print("    SKIP: no TC+version fixture in tenant 1")
                return
            _insert_step_results_for_counts(
                db, run.id, tc.id, tc.current_version_id, env.id,
                ["expected_fail_unverified", "expected_fail_verified"])
            counts = compute_negative_counts(db, run.id)
            assert counts["expected_failures"] == 1, counts
            assert counts["unexpected_passes"] == 1, counts
        finally:
            _teardown_run(db, run)
    results.append(test(
        "6. expected_fail_unverified steps counted as unexpected_passes",
        test_count_unverified))

    # Test 7: get_dashboard_data plumbs the counts onto ticket_counts
    def test_dashboard_surfaces_counts():
        db = SessionLocal()
        try:
            env, _ = _pick_env_and_section(db)
            if env is None:
                print("    SKIP: no env fixture in tenant 1")
                return
            data = get_dashboard_data(env.id, TENANT_ID, db)
            assert "ticket_counts" in data
            tc_counts = data["ticket_counts"]
            # New keys from Fix 2 must always be present (even zero)
            assert "expected_failures" in tc_counts, list(tc_counts.keys())
            assert "unexpected_passes" in tc_counts, list(tc_counts.keys())
            assert isinstance(tc_counts["expected_failures"], int)
            assert isinstance(tc_counts["unexpected_passes"], int)
        finally:
            db.close()
    results.append(test(
        "7. get_dashboard_data plumbs expected_failures + unexpected_passes "
        "into ticket_counts",
        test_dashboard_surfaces_counts))


# =============================================================================
# Fix 3: Verify step comparison capture
# =============================================================================

def _setup_exec_context():
    db = SessionLocal()
    from primeqa.metadata.models import MetaVersion
    from primeqa.test_management.models import Section as _Section

    env = db.query(Environment).filter_by(
        name="ReliabilityFix Env", tenant_id=TENANT_ID).first()
    if env is None:
        env = Environment(
            tenant_id=TENANT_ID, name="ReliabilityFix Env", env_type="sandbox",
            sf_instance_url="https://test.sf.com", sf_api_version="59.0",
            capture_mode="smart", created_by=1,
        )
        db.add(env); db.commit(); db.refresh(env)

    mv = MetaVersion(
        environment_id=env.id,
        version_label=f"v-rel-{int(datetime.now(timezone.utc).timestamp())}",
        status="complete")
    db.add(mv); db.commit(); db.refresh(mv)

    run = PipelineRun(
        tenant_id=TENANT_ID, environment_id=env.id, triggered_by=1,
        run_type="full", source_type="test_cases", source_ids=[1],
        cancellation_token=f"rel-{int(datetime.now(timezone.utc).timestamp())}",
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    db.add(run); db.commit(); db.refresh(run)

    section = db.query(_Section).filter(
        _Section.tenant_id == TENANT_ID).first()
    if section is None:
        section = _Section(tenant_id=TENANT_ID, name="Reliability Section",
                           created_by=1)
        db.add(section); db.commit(); db.refresh(section)

    tc = TestCase(
        tenant_id=TENANT_ID, title="Verify Fix TC", owner_id=1, created_by=1,
        section_id=section.id, visibility="shared", status="active",
    )
    db.add(tc); db.commit(); db.refresh(tc)

    tcv = TestCaseVersion(
        test_case_id=tc.id, version_number=1,
        metadata_version_id=mv.id, generation_method="manual",
        steps=[], created_by=1,
    )
    db.add(tcv); db.commit(); db.refresh(tcv)
    tc.current_version_id = tcv.id
    db.commit()
    return db, env, run, tc, tcv


def _run_fix3_tests(results):
    print("\n--- Fix 3: verify-step comparison capture ---\n")

    db, env, run, tc, tcv = _setup_exec_context()

    test_result_repo = RunTestResultRepository(db)
    step_result_repo = RunStepResultRepository(db)
    entity_repo = RunCreatedEntityRepository(db)

    def _mk_executor(sf_mock):
        return StepExecutor(sf_mock, run.id, "minimal",
                            step_result_repo, entity_repo,
                            IdempotencyManager(entity_repo, sf_mock))

    # Test 8: all matching → no comparison_details in the response body
    def test_verify_all_match():
        sf = MagicMock()
        sf.get_record.return_value = {
            "api_request": {"method": "GET",
                            "url": "/sobjects/Case/500AA"},
            "api_response": {"status_code": 200, "body": {
                "Id": "500AA", "Status": "Closed", "IsEscalated": False,
                "attributes": {"type": "Case"},
            }},
            "success": True, "record_id": "500AA",
        }
        sf.record_exists.return_value = True
        ex = _mk_executor(sf)
        # Direct call to _execute_verify — unit-level
        result = ex._execute_verify(
            "Case", "500AA",
            {"Status": "Closed", "IsEscalated": False})
        assert result["success"] is True, "clean verify should pass"
        body = result["api_response"]["body"]
        assert "comparison_details" not in body, \
            "comparison_details should be absent on clean verify"
        assert "assertion_summary" not in body, \
            "assertion_summary should be absent on clean verify"
    results.append(test(
        "8. Verify step with all-matching fields → success, no comparison_details",
        test_verify_all_match))

    # Test 9: mismatches → structured comparison_details + assertion_summary
    def test_verify_mismatch_structure():
        sf = MagicMock()
        sf.get_record.return_value = {
            "api_request": {"method": "GET",
                            "url": "/sobjects/Case/500BB"},
            "api_response": {"status_code": 200, "body": {
                "Id": "500BB", "Status": "New", "IsEscalated": True,
                "attributes": {"type": "Case"},
            }},
            "success": True, "record_id": "500BB",
        }
        sf.record_exists.return_value = True
        ex = _mk_executor(sf)
        result = ex._execute_verify(
            "Case", "500BB",
            {"Status": "Closed", "IsEscalated": False})
        assert result["success"] is False, "mismatches should fail"
        body = result["api_response"]["body"]
        # Legacy list-of-strings kept for back-compat
        assert "assertion_failures" in body
        assert len(body["assertion_failures"]) == 2
        # Structured summary
        assert "assertion_summary" in body
        assert "2 of 2 fields mismatched" in body["assertion_summary"], \
            body["assertion_summary"]
        # Structured per-field payload
        assert "comparison_details" in body
        mismatches = body["comparison_details"]["mismatches"]
        assert len(mismatches) == 2
        fields = {m["field"] for m in mismatches}
        assert fields == {"Status", "IsEscalated"}, fields
        for m in mismatches:
            assert "expected" in m and "actual" in m
            if m["field"] == "Status":
                assert m["expected"] == "Closed"
                assert m["actual"] == "New"
            elif m["field"] == "IsEscalated":
                assert m["expected"] is False
                assert m["actual"] is True
    results.append(test(
        "9. Verify step with mismatches → structured comparison_details + "
        "assertion_summary",
        test_verify_mismatch_structure))

    # Test 10: execute_step surfaces clean error_message + writes
    # comparison_details to the DB row
    def test_execute_step_surfaces_cleanly():
        sf = MagicMock()
        sf.get_record.return_value = {
            "api_request": {"method": "GET",
                            "url": "/sobjects/Case/500CC"},
            "api_response": {"status_code": 200, "body": {
                "Id": "500CC", "Status": "New",
                "attributes": {"type": "Case"},
            }},
            "success": True, "record_id": "500CC",
        }
        sf.record_exists.return_value = True
        ex = _mk_executor(sf)
        rtr = test_result_repo.create_result(
            run_id=run.id, test_case_id=tc.id,
            test_case_version_id=tcv.id, environment_id=env.id,
        )
        step, status = ex.execute_step(rtr.id, {
            "step_order": 1, "action": "verify",
            "target_object": "Case",
            "record_ref": "500CC",
            "assertions": {"Status": "Closed"},
            "expected_result": "Verified closure",
        })
        assert status == "failed", f"mismatch should yield failed, got {status}"
        # Clean error_message — NOT str(body)
        assert step.error_message is not None
        assert "1 of 1 fields mismatched" in step.error_message, \
            step.error_message
        assert "Status" in step.error_message
        # comparison_details column written
        assert step.comparison_details is not None, \
            "comparison_details JSONB should be populated"
        mismatches = step.comparison_details["mismatches"]
        assert len(mismatches) == 1
        m = mismatches[0]
        assert m["field"] == "Status"
        assert m["expected"] == "Closed"
        assert m["actual"] == "New"
    results.append(test(
        "10. execute_step writes comparison_details + clean error_message "
        "(NOT str(body))",
        test_execute_step_surfaces_cleanly))

    # Cleanup run/env rows we inserted
    try:
        # RunStepResult rows cascade from RunTestResult
        db.query(RunTestResult).filter_by(run_id=run.id).delete(
            synchronize_session=False)
        db.query(PipelineRun).filter_by(id=run.id).delete(
            synchronize_session=False)
        db.commit()
    finally:
        db.close()


# =============================================================================
# Runner
# =============================================================================

def run_tests():
    results = []
    print("\n=== Reliability Fixes Tests (Prompt 15) ===")

    _run_fix1_tests(results)
    _run_fix2_tests(results)
    _run_fix3_tests(results)

    passed = sum(1 for r in results if r)
    total = len(results)
    print(f"\n{passed}/{total} tests passed\n")
    return passed == total


if __name__ == "__main__":
    with app.app_context():
        ok = run_tests()
    sys.exit(0 if ok else 1)
