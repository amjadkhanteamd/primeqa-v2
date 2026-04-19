"""Tests for the intelligence layer — dependencies, explanations, patterns, causal links, facts."""

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
    from primeqa.metadata.models import MetaVersion, MetaObject, MetaValidationRule, MetaFlow, MetaTrigger
    from primeqa.execution.models import PipelineRun, RunTestResult, RunStepResult
    from primeqa.test_management.models import Section, TestCase, TestCaseVersion

    uid = uuid.uuid4().hex[:6]

    # environments.created_by is NOT NULL since hardening migrations —
    # fixtures must pass a real user id. Use id=1 (the seeded superadmin).
    env = Environment(
        tenant_id=1, name=f"Intel Test {uid}", env_type="sandbox",
        sf_instance_url="https://test.sf.com", sf_api_version="59.0",
        created_by=1,
    )
    db.add(env)
    db.commit()
    db.refresh(env)

    mv = MetaVersion(environment_id=env.id, version_label=f"vi{uid}", status="complete")
    db.add(mv)
    db.commit()
    db.refresh(mv)

    env.current_meta_version_id = mv.id
    db.commit()

    obj = MetaObject(meta_version_id=mv.id, api_name="Opportunity", label="Opportunity")
    db.add(obj)
    db.commit()
    db.refresh(obj)

    vr = MetaValidationRule(
        meta_version_id=mv.id, meta_object_id=obj.id,
        rule_name="RequireAmount",
        error_condition_formula="AND(ISPICKVAL(StageName,'Closed Won'),ISBLANK(Amount))",
        error_message="Amount is required for Closed Won opportunities",
    )
    db.add(vr)

    flow = MetaFlow(
        meta_version_id=mv.id, api_name="OppAfterUpdate",
        label="Opp After Update", flow_type="autolaunched",
        trigger_object="Opportunity", trigger_event="create_or_update",
    )
    db.add(flow)

    trigger = MetaTrigger(
        meta_version_id=mv.id, meta_object_id=obj.id,
        trigger_name="OppTrigger", events="insert,update",
    )
    db.add(trigger)
    db.commit()

    run = PipelineRun(
        tenant_id=1, environment_id=env.id, triggered_by=1,
        run_type="full", source_type="requirements", source_ids=[1],
        cancellation_token=f"cancel-{uid}", status="running",
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    section = db.query(Section).filter(Section.tenant_id == 1).first()
    if not section:
        section = Section(tenant_id=1, name="Intel Section", created_by=1)
        db.add(section)
        db.commit()
        db.refresh(section)

    tc = TestCase(
        tenant_id=1, title=f"Intel Test Case {uid}", owner_id=1, created_by=1,
        section_id=section.id, visibility="shared", status="active",
    )
    db.add(tc)
    db.commit()
    db.refresh(tc)

    tcv = TestCaseVersion(
        test_case_id=tc.id, version_number=1, metadata_version_id=mv.id,
        generation_method="manual", steps=[], referenced_entities=[], created_by=1,
    )
    db.add(tcv)
    db.commit()
    db.refresh(tcv)

    rtr = RunTestResult(
        run_id=run.id, test_case_id=tc.id, test_case_version_id=tcv.id,
        environment_id=env.id, status="failed",
    )
    db.add(rtr)
    db.commit()
    db.refresh(rtr)

    step_ok = RunStepResult(
        run_test_result_id=rtr.id, step_order=1, step_action="create",
        status="passed", execution_state="completed",
        target_object="Opportunity", target_record_id="006TEST01",
        field_diff={"StageName": {"old": None, "new": "Prospecting"}},
        before_state=None, after_state={"StageName": "Prospecting"},
    )
    db.add(step_ok)
    db.commit()
    db.refresh(step_ok)

    step_fail = RunStepResult(
        run_test_result_id=rtr.id, step_order=2, step_action="update",
        status="failed", execution_state="completed",
        target_object="Opportunity", target_record_id="006TEST01",
        error_message="FIELD_CUSTOM_VALIDATION_EXCEPTION: Amount is required for Closed Won opportunities",
        before_state={"StageName": "Prospecting", "Amount": None},
        api_response={"status_code": 400, "body": [{"errorCode": "FIELD_CUSTOM_VALIDATION_EXCEPTION"}]},
    )
    db.add(step_fail)
    db.commit()
    db.refresh(step_fail)

    return db, env, mv, run, rtr, tc, [step_ok, step_fail]


def run_tests():
    results = []
    print("\n=== Intelligence Layer Tests ===\n")

    db, env, mv, run, rtr, tc, steps = setup()
    step_ok, step_fail = steps
    env_id = env.id
    mv_id = mv.id
    run_id = run.id
    rtr_id = rtr.id
    tc_id = tc.id
    step_ok_id = step_ok.id
    step_fail_id = step_fail.id

    from primeqa.intelligence.repository import (
        EntityDependencyRepository, ExplanationRepository,
        FailurePatternRepository, BehaviourFactRepository, StepCausalLinkRepository,
    )
    from primeqa.metadata.repository import MetadataRepository
    from primeqa.intelligence.service import IntelligenceService
    from primeqa.intelligence.models import FailurePattern, ExplanationRequest
    from sqlalchemy import text

    # Clean up patterns/explanations from previous runs to avoid collisions
    db.query(ExplanationRequest).delete()
    db.query(FailurePattern).filter(FailurePattern.tenant_id == 1).delete()
    db.commit()

    dep_repo = EntityDependencyRepository(db)
    expl_repo = ExplanationRepository(db)
    pattern_repo = FailurePatternRepository(db)
    fact_repo = BehaviourFactRepository(db)
    causal_repo = StepCausalLinkRepository(db)
    meta_repo = MetadataRepository(db)

    svc = IntelligenceService(dep_repo, expl_repo, pattern_repo, fact_repo, causal_repo)

    # 1. Extract dependencies from metadata
    def test_extract_deps():
        count = svc.extract_dependencies(mv_id, meta_repo)
        assert count == 3, f"Expected 3 deps (VR + flow + trigger), got {count}"
        deps = svc.get_dependencies(mv_id)
        types = {d["source_type"] for d in deps}
        assert "validation_rule" in types
        assert "flow" in types
        assert "trigger" in types
    results.append(test("1. Extract dependencies from metadata (VR, flow, trigger)", test_extract_deps))

    # 2. Learn dependency from execution
    def test_learn_dep():
        svc.learn_dependency_from_execution(
            mv_id, "Trigger.TaskAutoCreate", "trigger", "Task", "creates",
        )
        deps = svc.get_dependencies(mv_id)
        exec_deps = [d for d in deps if d["discovery_source"] == "execution_trace"]
        assert len(exec_deps) >= 1
        assert exec_deps[0]["confidence"] == 0.85
    results.append(test("2. Learn dependency from execution trace", test_learn_dep))

    # 3. Deterministic VR explanation skips LLM
    def test_deterministic_vr():
        mock_llm = MagicMock()
        svc_with_llm = IntelligenceService(
            dep_repo, expl_repo, pattern_repo, fact_repo, causal_repo, mock_llm,
        )
        vr_context = {
            "validation_rules": [{
                "rule_name": "RequireAmount",
                "error_condition_formula": "AND(ISPICKVAL(StageName,'Closed Won'),ISBLANK(Amount))",
                "error_message": "Amount is required for Closed Won opportunities",
            }],
        }
        explanation = svc_with_llm.explain_failure(
            step_fail_id, rtr_id, 1, env_id,
            {
                "failure_type": "validation_rule",
                "target_object": "Opportunity",
                "error_message": "FIELD_CUSTOM_VALIDATION_EXCEPTION: Amount is required for Closed Won opportunities",
                "test_case_id": tc_id,
            },
            metadata_context=vr_context,
        )
        assert explanation["source"] == "deterministic", f"Expected deterministic, got {explanation['source']}"
        assert "RequireAmount" in explanation["root_cause_entity"]
        mock_llm.messages.create.assert_not_called()
    results.append(test("3. Deterministic VR match skips LLM", test_deterministic_vr))

    # 4. Pattern match skips LLM on second failure
    def test_pattern_match():
        mock_llm = MagicMock()
        svc2 = IntelligenceService(
            dep_repo, expl_repo, pattern_repo, fact_repo, causal_repo, mock_llm,
        )
        explanation = svc2.explain_failure(
            step_fail_id, rtr_id, 1, env_id,
            {
                "failure_type": "validation_rule",
                "target_object": "Opportunity",
                "error_message": "FIELD_CUSTOM_VALIDATION_EXCEPTION: Amount is required for Closed Won opportunities",
                "test_case_id": tc_id,
            },
            metadata_context={"validation_rules": [{
                "rule_name": "RequireAmount",
                "error_message": "Amount is required for Closed Won opportunities",
            }]},
        )
        assert explanation["source"] == "pattern_matched", \
            f"Second call should match pattern, got {explanation['source']}"
        mock_llm.messages.create.assert_not_called()
    results.append(test("4. Pattern match skips LLM on second occurrence", test_pattern_match))

    # 5. Novel failure calls LLM
    def test_llm_fallback():
        mock_llm = MagicMock()
        mock_llm.messages.create.return_value = MagicMock(
            content=[MagicMock(text='{"root_cause": "Unknown system error", "root_cause_entity": "System", "confidence": 0.6}')],
            model="claude-sonnet-4-20250514",
            usage=MagicMock(input_tokens=100, output_tokens=50),
        )
        svc3 = IntelligenceService(
            dep_repo, expl_repo, pattern_repo, fact_repo, causal_repo, mock_llm,
        )
        explanation = svc3.explain_failure(
            step_fail_id, rtr_id, 1, env_id,
            {
                "failure_type": "system_error",
                "target_object": "Account",
                "error_message": "UNKNOWN_EXCEPTION: Something totally new happened",
                "test_case_id": tc_id,
            },
        )
        assert explanation["source"] == "llm_generated", f"Expected llm_generated, got {explanation.get('source')}"
        mock_llm.messages.create.assert_called_once()
    results.append(test("5. Novel failure calls LLM with structured input", test_llm_fallback))

    # 6. LLM result creates new failure pattern
    def test_llm_creates_pattern():
        patterns = svc.list_active_patterns(1, env_id)
        system_patterns = [p for p in patterns if p["failure_type"] == "system_error"]
        assert len(system_patterns) >= 1, "LLM result should have created a pattern"
    results.append(test("6. LLM result creates new failure pattern", test_llm_creates_pattern))

    # 7. Pattern decay
    def test_decay():
        from primeqa.intelligence.models import FailurePattern

        old_time = datetime.now(timezone.utc) - timedelta(days=60)
        active = db.query(FailurePattern).filter(FailurePattern.status == "active").all()
        assert len(active) >= 1, f"Need active patterns, found {len(active)}"
        for p in active:
            p.last_validated_at = old_time
            p.confidence = 1.0
        db.flush()
        db.commit()
        db.expire_all()

        # Verify update took effect
        check = db.query(FailurePattern).filter(FailurePattern.status == "active").first()
        assert check.last_validated_at < datetime.now(timezone.utc) - timedelta(days=7), \
            f"last_validated_at not updated: {check.last_validated_at}"

        decayed = pattern_repo.decay_stale_patterns(decay_days=7, decay_amount=0.8, min_confidence=0.3)
        assert decayed > 0, f"Expected some patterns to decay, got {decayed}"

        db.expire_all()
        dp = db.query(FailurePattern).filter(FailurePattern.status == "decayed").first()
        assert dp is not None, "Should have at least one decayed pattern"
    results.append(test("7. Pattern decay reduces confidence, status becomes decayed", test_decay))

    # 8. Pattern reactivation
    def test_reactivation():
        from primeqa.intelligence.models import FailurePattern
        db.expire_all()
        decayed_p = db.query(FailurePattern).filter(FailurePattern.status == "decayed").first()
        assert decayed_p is not None, "Need a decayed pattern from test 7"

        pattern_repo.upsert_pattern(
            decayed_p.tenant_id, decayed_p.environment_id,
            decayed_p.pattern_signature, decayed_p.failure_type,
        )

        db.expire_all()
        refreshed = db.query(FailurePattern).filter(FailurePattern.id == decayed_p.id).first()
        assert refreshed.status == "active", f"Expected active, got {refreshed.status}"
        assert refreshed.confidence == 1.0, f"Expected confidence 1.0, got {refreshed.confidence}"
    results.append(test("8. Pattern reactivation on new match", test_reactivation))

    # 9. Causal link detection
    def test_causal_links():
        db4 = SessionLocal()
        from primeqa.execution.models import RunStepResult
        steps_db = db4.query(RunStepResult).filter(
            RunStepResult.run_test_result_id == rtr_id,
        ).order_by(RunStepResult.step_order).all()

        causal_repo4 = StepCausalLinkRepository(db4)
        svc4 = IntelligenceService(
            EntityDependencyRepository(db4), ExplanationRepository(db4),
            FailurePatternRepository(db4), BehaviourFactRepository(db4), causal_repo4,
        )
        count = svc4.detect_causal_links(rtr_id, steps_db)
        assert count >= 1, f"Expected at least 1 causal link, got {count}"

        links = svc4.get_causal_links(rtr_id)
        assert len(links) >= 1
        assert links[0]["link_type"] in ("state_mutation", "validation_block", "data_dependency")
        db4.close()
    results.append(test("9. Causal link detection between steps", test_causal_links))

    # 10. Behaviour fact seeding
    def test_seed_facts():
        db5 = SessionLocal()
        fact_repo5 = BehaviourFactRepository(db5)
        svc5 = IntelligenceService(
            EntityDependencyRepository(db5), ExplanationRepository(db5),
            FailurePatternRepository(db5), fact_repo5, StepCausalLinkRepository(db5),
        )
        count = svc5.seed_facts(1, env_id)
        assert count >= 5, f"Expected >= 5 seeded facts, got {count}"

        facts = svc5.list_facts(1, env_id)
        assert len(facts) >= 5
        entity_refs = {f["entity_ref"] for f in facts}
        assert "Opportunity.Stage" in entity_refs
        assert "Lead.Convert" in entity_refs
        db5.close()
    results.append(test("10. Behaviour fact seeding", test_seed_facts))

    # 11. Dependency graph for specific object
    def test_dep_graph():
        deps = svc.get_dependencies(mv_id, "Opportunity")
        assert len(deps) >= 1
        for d in deps:
            assert "Opportunity" in d["source_entity"] or "Opportunity" in d["target_entity"]
    results.append(test("11. Dependency graph for specific object", test_dep_graph))

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
