"""R5 tests \u2014 Triage, trust bands, orchestrator gating, revert snapshot."""

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
    print("\n=== R5 Agent Fix-and-Rerun Tests ===\n")
    results = []

    def t_schema():
        from primeqa.db import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        try:
            rows = list(db.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='agent_fix_attempts' AND column_name IN "
                "('run_id','test_case_id','failure_class','before_state','after_state',"
                "'auto_applied','rerun_run_id','user_decision','trust_band')"
            )))
            assert len(rows) == 9, f"expected 9, got {[r[0] for r in rows]}"
        finally:
            db.close()
    results.append(test("R5-1. agent_fix_attempts schema", t_schema))

    def t_trust_band_default_thresholds():
        from primeqa.intelligence.agent import trust_band
        assert trust_band(0.90, high=0.85, medium=0.60) == "high"
        assert trust_band(0.70, high=0.85, medium=0.60) == "medium"
        assert trust_band(0.55, high=0.85, medium=0.60) == "low"
        assert trust_band(0.85, high=0.85, medium=0.60) == "high"
    results.append(test("R5-2. Trust-band classification (Q12 default cutoffs)", t_trust_band_default_thresholds))

    def t_classify_taxonomy():
        from primeqa.db import SessionLocal
        from primeqa.intelligence.agent import classify_failure
        db = SessionLocal()
        try:
            r = classify_failure(db, "INVALID_FIELD: no such column 'Frozzle__c' on Account")
            assert r.failure_class == "metadata_drift"
            r2 = classify_failure(db, "Connection timed out after 30s")
            assert r2.failure_class == "transient"
            r3 = classify_failure(db, "DUPLICATE_VALUE: duplicates on Name")
            assert r3.failure_class == "data_drift"
            r4 = classify_failure(db, "some totally unknown failure text")
            assert r4.failure_class == "unknown"
        finally:
            db.close()
    results.append(test("R5-3. Taxonomy-based triage classifies common SF errors", t_classify_taxonomy))

    def t_orchestrator_no_client_still_records():
        """With no LLM client the orchestrator still records a triage-only row."""
        import uuid
        from primeqa.db import SessionLocal
        from primeqa.intelligence.agent import AgentOrchestrator
        from primeqa.intelligence.models import AgentFixAttempt
        from primeqa.execution.models import PipelineRun
        from primeqa.test_management.models import TestCase
        from primeqa.core.models import Environment

        db = SessionLocal()
        try:
            env = db.query(Environment).filter(Environment.tenant_id == TENANT_ID).first()
            tc = db.query(TestCase).filter(
                TestCase.tenant_id == TENANT_ID, TestCase.deleted_at.is_(None),
            ).order_by(TestCase.id.desc()).first()
            run = PipelineRun(
                tenant_id=TENANT_ID, environment_id=env.id, triggered_by=1,
                run_type="execute_only", source_type="test_cases",
                source_ids=[tc.id], cancellation_token=uuid.uuid4().hex,
                status="running", priority="normal",
            )
            db.add(run); db.commit(); db.refresh(run)

            orch = AgentOrchestrator(db, anthropic_client=None)
            decision = orch.handle_failure(
                run_id=run.id, test_case_id=tc.id,
                run_test_result_id=None, run_step_result_id=None,
                tenant_id=TENANT_ID, environment_id=env.id, env_type="sandbox",
                error_message="INVALID_FIELD: no such column 'Frozzle__c'",
                step_definition={"step_order": 1, "action": "create"},
            )
            assert decision is not None
            row = db.query(AgentFixAttempt).filter_by(id=decision.fix_attempt_id).first()
            assert row is not None
            assert row.failure_class == "metadata_drift"
            assert row.auto_applied is False  # no LLM \u2192 no proposal \u2192 gate = 'no_proposal'
            assert decision.gate_reason.startswith("no_proposal") or decision.gate_reason.startswith("gated_")

            # Cleanup
            db.query(AgentFixAttempt).filter_by(id=row.id).delete()
            db.query(PipelineRun).filter_by(id=run.id).delete()
            db.commit()
        finally:
            db.close()
    results.append(test("R5-4. Orchestrator records triage even without LLM", t_orchestrator_no_client_still_records))

    def t_orchestrator_blocks_production():
        """env_type='production' \u2192 never auto-applies even at high confidence."""
        import uuid
        from primeqa.db import SessionLocal
        from primeqa.intelligence.agent import AgentOrchestrator
        from primeqa.intelligence.models import AgentFixAttempt
        from primeqa.execution.models import PipelineRun
        from primeqa.test_management.models import TestCase
        from primeqa.core.models import Environment

        class FakeClient:
            class messages:
                @staticmethod
                def create(**k):
                    class R:
                        content = [type("T", (), {"text":
                            '{"root_cause_summary":"field removed",'
                            '"confidence":0.95,"proposed_fix_type":"edit_step",'
                            '"changes":{"step_order":1,"field_values":{"Name":"Acme"}}}'})()]
                    return R()

        db = SessionLocal()
        try:
            env = db.query(Environment).filter(Environment.tenant_id == TENANT_ID).first()
            tc = db.query(TestCase).filter(
                TestCase.tenant_id == TENANT_ID, TestCase.deleted_at.is_(None),
            ).order_by(TestCase.id.desc()).first()
            run = PipelineRun(
                tenant_id=TENANT_ID, environment_id=env.id, triggered_by=1,
                run_type="execute_only", source_type="test_cases",
                source_ids=[tc.id], cancellation_token=uuid.uuid4().hex,
                status="running", priority="normal",
            )
            db.add(run); db.commit(); db.refresh(run)

            orch = AgentOrchestrator(db, anthropic_client=FakeClient())
            decision = orch.handle_failure(
                run_id=run.id, test_case_id=tc.id,
                run_test_result_id=None, run_step_result_id=None,
                tenant_id=TENANT_ID, environment_id=env.id, env_type="production",
                error_message="INVALID_FIELD: no such column 'Frozzle__c'",
                step_definition={"step_order": 1, "action": "create"},
            )
            assert decision.auto_applied is False
            assert decision.gate_reason == "gated_production"

            row = db.query(AgentFixAttempt).filter_by(id=decision.fix_attempt_id).first()
            db.query(AgentFixAttempt).filter_by(id=row.id).delete()
            db.query(PipelineRun).filter_by(id=run.id).delete()
            db.commit()
        finally:
            db.close()
    results.append(test("R5-5. Production env blocks auto-apply regardless of confidence", t_orchestrator_blocks_production))

    def t_revert_restores_snapshot():
        """revert() overwrites test case state with before_state snapshot (Q8)."""
        import uuid
        from primeqa.db import SessionLocal
        from primeqa.intelligence.agent import AgentOrchestrator
        from primeqa.intelligence.models import AgentFixAttempt
        from primeqa.execution.models import PipelineRun
        from primeqa.test_management.models import TestCase
        from primeqa.core.models import Environment
        db = SessionLocal()
        try:
            env = db.query(Environment).filter(Environment.tenant_id == TENANT_ID).first()
            tc = db.query(TestCase).filter(
                TestCase.tenant_id == TENANT_ID, TestCase.deleted_at.is_(None),
            ).order_by(TestCase.id.desc()).first()
            original_title = tc.title
            original_version_id = tc.current_version_id

            run = PipelineRun(
                tenant_id=TENANT_ID, environment_id=env.id, triggered_by=1,
                run_type="execute_only", source_type="test_cases",
                source_ids=[tc.id], cancellation_token=uuid.uuid4().hex,
                status="running", priority="normal",
            )
            db.add(run); db.commit(); db.refresh(run)

            # Manually plant a fix attempt with a known before_state, then mutate
            # the TC, then revert and confirm.
            fix = AgentFixAttempt(
                run_id=run.id, test_case_id=tc.id,
                failure_class="metadata_drift", confidence=0.95,
                trust_band="high", proposed_fix_type="edit_step",
                before_state={
                    "test_case": {"id": tc.id, "title": original_title,
                                  "status": tc.status, "visibility": tc.visibility,
                                  "version": tc.version,
                                  "current_version_id": original_version_id},
                    "current_version": None,
                },
                auto_applied=True,
            )
            db.add(fix); db.commit(); db.refresh(fix)

            # Mutate the TC
            tc.title = "AGENT MUTATED TITLE"
            db.commit()

            # Revert
            orch = AgentOrchestrator(db)
            assert orch.revert(fix.id, TENANT_ID, 1) is True
            db.expire_all()
            refetch = db.query(TestCase).filter_by(id=tc.id).first()
            assert refetch.title == original_title

            # Cleanup
            db.query(AgentFixAttempt).filter_by(id=fix.id).delete()
            db.query(PipelineRun).filter_by(id=run.id).delete()
            db.commit()
        finally:
            db.close()
    results.append(test("R5-6. Revert restores before-state snapshot", t_revert_restores_snapshot))

    def t_release_status_respects_agent_verdict_flag():
        from primeqa.db import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        try:
            rows = list(db.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='release_decisions' AND column_name='agent_verdict_counts'"
            )))
            assert rows, "column should exist from R2 migration 019"
        finally:
            db.close()
    results.append(test("R5-7. release_decisions.agent_verdict_counts column present", t_release_status_respects_agent_verdict_flag))

    passed = sum(results); total = len(results)
    print(f"\n{'='*40}\nResults: {passed}/{total} passed")
    print("ALL R5 TESTS PASSED" if passed == total else f"{total - passed} FAILED")
    return passed == total


if __name__ == "__main__":
    sys.exit(0 if run_tests() else 1)
