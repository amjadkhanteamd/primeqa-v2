"""R1 tests \u2014 Run Wizard, Preflight, SSE event bus, schema smoke.

Covers:
  - Wizard source resolution (suite, hand-picked TCs, Jira sprint pass-through)
  - Preflight outcomes (no tests, bad env, soft/hard cap, success path)
  - SSE event bus pub/sub (publish before subscribe vs after)
  - Schema smoke: new columns exist (superadmin role, source_refs, parent_run_id,
    run_step_results log-capture columns)
"""

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


def run_tests():
    results = []
    print("\n=== Run Experience R1 Tests ===\n")

    admin_token = login("admin@primeqa.io", "changeme123")

    # ---- Schema smoke -----------------------------------------------------

    def test_superadmin_role_allowed():
        from primeqa.db import SessionLocal
        from primeqa.core.models import User
        db = SessionLocal()
        try:
            superadmins = db.query(User).filter(User.role == "superadmin").all()
            assert len(superadmins) >= 1, "expected at least one superadmin"
            db.commit()
        finally:
            db.close()
    results.append(test("S1. superadmin role exists + seeded", test_superadmin_role_allowed))

    def test_pipeline_runs_source_refs_column():
        from primeqa.db import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        try:
            rows = list(db.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='pipeline_runs' AND column_name IN ('source_refs','parent_run_id')"
            )))
            names = {r[0] for r in rows}
            assert names == {"source_refs", "parent_run_id"}, f"got {names}"
        finally:
            db.close()
    results.append(test("S2. pipeline_runs has source_refs + parent_run_id", test_pipeline_runs_source_refs_column))

    def test_run_step_results_log_columns():
        from primeqa.db import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        try:
            rows = list(db.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='run_step_results' AND column_name IN "
                "('soql_queries','http_status','timings','failure_class','correlation_id','llm_prompt_sha')"
            )))
            names = {r[0] for r in rows}
            assert len(names) == 6, f"expected 6 log columns, got {names}"
        finally:
            db.close()
    results.append(test("S3. run_step_results has log-capture columns", test_run_step_results_log_columns))

    # ---- Event bus -------------------------------------------------------

    def test_event_bus_pub_sub_basic():
        from primeqa.runs.streams import BUS
        q = BUS.subscribe(999)
        try:
            BUS.publish(999, {"type": "hello", "data": {"x": 1}})
            msg = q.get(timeout=1)
            assert msg["type"] == "hello"
            assert msg["data"]["x"] == 1
        finally:
            BUS.unsubscribe(999, q)
    results.append(test("B1. Event bus delivers pub\u2192sub", test_event_bus_pub_sub_basic))

    def test_event_bus_publish_before_subscribe_drops():
        from primeqa.runs.streams import BUS
        BUS.publish(998, {"type": "early", "data": {}})  # no subscribers \u2192 dropped
        q = BUS.subscribe(998)
        try:
            import queue
            try:
                q.get(timeout=0.2)
                raise AssertionError("unexpected message")
            except queue.Empty:
                pass  # correct
        finally:
            BUS.unsubscribe(998, q)
    results.append(test("B2. Events published before subscribe are dropped", test_event_bus_publish_before_subscribe_drops))

    def test_event_bus_fanout():
        from primeqa.runs.streams import BUS
        q1 = BUS.subscribe(997)
        q2 = BUS.subscribe(997)
        try:
            BUS.publish(997, {"type": "fanout", "data": {}})
            assert q1.get(timeout=1)["type"] == "fanout"
            assert q2.get(timeout=1)["type"] == "fanout"
        finally:
            BUS.unsubscribe(997, q1); BUS.unsubscribe(997, q2)
    results.append(test("B3. Fan-out to multiple subscribers", test_event_bus_fanout))

    # ---- Wizard ----------------------------------------------------------

    def test_wizard_page_renders():
        client.set_cookie("access_token", admin_token)
        r = client.get("/runs/new")
        assert r.status_code == 200, f"got {r.status_code}"
        body = r.data.decode()
        assert "PrimeQA Suites" in body
        assert "Hand-picked test cases" in body
    results.append(test("W1. Wizard page renders", test_wizard_page_renders))

    def test_wizard_resolves_hand_picked_tcs():
        from primeqa.db import SessionLocal
        from primeqa.test_management.models import TestCase
        from primeqa.runs.wizard import RunWizardResolver, WizardSelection
        from primeqa.test_management.repository import (
            TestSuiteRepository, SectionRepository, TestCaseRepository,
            RequirementRepository,
        )
        from primeqa.core.repository import ConnectionRepository
        db = SessionLocal()
        try:
            tc = db.query(TestCase).filter(
                TestCase.tenant_id == TENANT_ID, TestCase.deleted_at.is_(None),
            ).order_by(TestCase.id.desc()).first()
            assert tc, "need at least one test case"
            resolver = RunWizardResolver(
                db,
                suite_repo=TestSuiteRepository(db),
                section_repo=SectionRepository(db),
                tc_repo=TestCaseRepository(db),
                req_repo=RequirementRepository(db),
                connection_repo=ConnectionRepository(db),
            )
            resolved = resolver.resolve(TENANT_ID, WizardSelection(test_case_ids=[tc.id, 999_999_999]))
            assert resolved.test_case_ids == [tc.id]
            assert resolved.source_refs["test_case_ids"] == [tc.id]
            assert any("not found" in w for w in resolved.resolution_warnings)
        finally:
            db.close()
    results.append(test("W2. Wizard resolves hand-picked TCs and drops invalid IDs", test_wizard_resolves_hand_picked_tcs))

    def test_wizard_dedupes_across_sources():
        from primeqa.db import SessionLocal
        from primeqa.test_management.models import TestCase, TestSuite, SuiteTestCase
        from primeqa.runs.wizard import RunWizardResolver, WizardSelection
        from primeqa.test_management.repository import (
            TestSuiteRepository, SectionRepository, TestCaseRepository,
            RequirementRepository,
        )
        from primeqa.core.repository import ConnectionRepository
        db = SessionLocal()
        try:
            tc = db.query(TestCase).filter(
                TestCase.tenant_id == TENANT_ID, TestCase.deleted_at.is_(None),
            ).order_by(TestCase.id.desc()).first()
            # Create a throwaway suite containing that tc
            suite = TestSuite(tenant_id=TENANT_ID, name=f"r1dedup {tc.id}",
                              suite_type="custom", created_by=1)
            db.add(suite); db.commit(); db.refresh(suite)
            db.add(SuiteTestCase(suite_id=suite.id, test_case_id=tc.id, position=0))
            db.commit()

            resolver = RunWizardResolver(
                db,
                suite_repo=TestSuiteRepository(db),
                section_repo=SectionRepository(db),
                tc_repo=TestCaseRepository(db),
                req_repo=RequirementRepository(db),
                connection_repo=ConnectionRepository(db),
            )
            resolved = resolver.resolve(TENANT_ID, WizardSelection(
                test_case_ids=[tc.id], suite_ids=[suite.id],
            ))
            assert resolved.test_case_ids.count(tc.id) == 1, \
                f"expected dedup; got {resolved.test_case_ids}"

            # Cleanup
            db.query(SuiteTestCase).filter(SuiteTestCase.suite_id == suite.id).delete()
            db.query(TestSuite).filter(TestSuite.id == suite.id).delete()
            db.commit()
        finally:
            db.close()
    results.append(test("W3. Wizard deduplicates test_case_ids across sources", test_wizard_dedupes_across_sources))

    # ---- Preflight ------------------------------------------------------

    def test_preflight_blocks_empty_selection():
        from primeqa.db import SessionLocal
        from primeqa.runs.preflight import Preflight
        from primeqa.runs.wizard import ResolvedRun
        from primeqa.core.repository import ConnectionRepository, EnvironmentRepository
        from primeqa.test_management.repository import TestCaseRepository
        from primeqa.metadata.repository import MetadataRepository
        from primeqa.core.models import Environment
        db = SessionLocal()
        try:
            env = db.query(Environment).filter(Environment.tenant_id == TENANT_ID).first()
            pf = Preflight(db,
                env_repo=EnvironmentRepository(db),
                conn_repo=ConnectionRepository(db),
                tc_repo=TestCaseRepository(db),
                meta_repo=MetadataRepository(db))
            rep = pf.check(TENANT_ID, {"role": "admin"}, env.id,
                           ResolvedRun(test_case_ids=[], source_refs={}))
            codes = [b["code"] for b in rep.blockers]
            assert "NO_TESTS_SELECTED" in codes
            # Summary still present
            assert rep.summary["environment"]["name"] == env.name
        finally:
            db.close()
    results.append(test("P1. Preflight blocks on empty selection, keeps summary", test_preflight_blocks_empty_selection))

    def test_preflight_bad_env():
        from primeqa.db import SessionLocal
        from primeqa.runs.preflight import Preflight
        from primeqa.runs.wizard import ResolvedRun
        from primeqa.core.repository import ConnectionRepository, EnvironmentRepository
        from primeqa.test_management.repository import TestCaseRepository
        from primeqa.metadata.repository import MetadataRepository
        db = SessionLocal()
        try:
            pf = Preflight(db,
                env_repo=EnvironmentRepository(db),
                conn_repo=ConnectionRepository(db),
                tc_repo=TestCaseRepository(db),
                meta_repo=MetadataRepository(db))
            rep = pf.check(TENANT_ID, {"role": "admin"}, 999999,
                           ResolvedRun(test_case_ids=[1, 2, 3], source_refs={}))
            codes = [b["code"] for b in rep.blockers]
            assert "ENV_NOT_FOUND" in codes
        finally:
            db.close()
    results.append(test("P2. Preflight blocks on unknown environment", test_preflight_bad_env))

    def test_preflight_override_requires_superadmin():
        from primeqa.runs.preflight import PreflightReport, Preflight
        pf = Preflight.__new__(Preflight)  # no init; we're only testing ensure_runnable
        rep = PreflightReport()
        rep.blockers.append({"code": "X", "message": "y", "details": {}})
        # Plain admin with OVERRIDE token: still raises
        try:
            pf.ensure_runnable(rep, {"role": "admin"}, override_token="OVERRIDE")
            raise AssertionError("admin should not override")
        except Exception as e:
            code = getattr(e, "code", "") or ""
            assert "PREFLIGHT_BLOCKERS" in code or "PREFLIGHT" in str(e).upper()
        # Super Admin + OVERRIDE token: passes
        pf.ensure_runnable(rep, {"role": "superadmin"}, override_token="OVERRIDE")
    results.append(test("P3. Preflight override requires superadmin + OVERRIDE", test_preflight_override_requires_superadmin))

    # ---- HTTP smoke -----------------------------------------------------

    def test_wizard_preview_round_trip():
        client.set_cookie("access_token", admin_token)
        # Post preview with bogus tc \u2192 should render a preview page with NO_TESTS_SELECTED blocker
        from primeqa.db import SessionLocal
        from primeqa.core.models import Environment
        db = SessionLocal()
        env = db.query(Environment).filter(Environment.tenant_id == TENANT_ID).first()
        db.close()
        r = client.post("/runs/new/preview", data={
            "environment_id": str(env.id), "run_type": "execute_only",
            "test_case_ids": "999999999",
        })
        assert r.status_code == 200
        body = r.data.decode()
        assert "NO_TESTS_SELECTED" in body
    results.append(test("H1. /runs/new/preview renders with blocker for empty resolution", test_wizard_preview_round_trip))

    def test_sse_endpoint_opens():
        client.set_cookie("access_token", admin_token)
        from primeqa.db import SessionLocal
        from primeqa.execution.models import PipelineRun
        db = SessionLocal()
        run = db.query(PipelineRun).filter(PipelineRun.tenant_id == TENANT_ID).order_by(
            PipelineRun.id.desc()).first()
        db.close()
        assert run, "need at least one pipeline_run"
        r = client.get(f"/api/runs/{run.id}/events")
        assert r.status_code == 200
        assert r.headers["Content-Type"].startswith("text/event-stream")
        # Read just the first event to confirm stream is emitting SSE
        first = b""
        for chunk in r.response:
            first += chunk
            if b"\n\n" in first or len(first) > 2000:
                break
        assert b"event:" in first, f"no SSE event found; got {first[:200]!r}"
    results.append(test("H2. SSE endpoint opens and emits first event", test_sse_endpoint_opens))

    # ---- Summary --------------------------------------------------------

    passed = sum(results); total = len(results)
    print(f"\n{'='*40}\nResults: {passed}/{total} passed")
    if passed == total:
        print("ALL R1 TESTS PASSED")
    else:
        print(f"{total - passed} test(s) FAILED")
    print()
    return passed == total


if __name__ == "__main__":
    ok = run_tests()
    sys.exit(0 if ok else 1)
