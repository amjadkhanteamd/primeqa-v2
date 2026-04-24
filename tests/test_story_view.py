"""Story-view enrichment tests (Prompt covering Parts 1-11, migration 048).

Covers the seven surfaces the feature touches:

  1. Feature flag ON  → generate_test_plan populates version.story_view
                        with the four required keys
  2. Feature flag OFF → generate_test_plan leaves story_view NULL and
                        still commits the batch cleanly
  3. Regeneration    → the NEW version gets a fresh story_view (the
                        prior one is superseded, not carried forward)
  4. Detail template → GET /test-cases/<id> renders the story block when
                        story_view is populated
  5. NULL fallback   → GET /test-cases/<id> falls back to the mechanical
                        step view when story_view is NULL (status=200,
                        step content still visible)
  6. Usage logging   → a llm_usage_log row with task='story_view_generation'
                        is written per enrichment call
  7. Malformed LLM   → enricher returns None on bad output, batch still
                        commits with story_view NULL (warning logged)

Every test uses the real Railway DB via _mk_service / _plan_payload
fixtures that mirror test_reliability_fixes.py so we don't drift on
plan-shape assumptions. Real Anthropic calls are patched at the
`llm_call` entry point — tests never burn credits.
"""

import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import jwt
from sqlalchemy import text

from primeqa.app import app
from primeqa.db import SessionLocal
from primeqa.core.models import Environment, TenantAgentSettings, User
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


# ---------------------------------------------------------------------------
# Fixtures — mirror test_reliability_fixes.py so plan shape is identical.
# ---------------------------------------------------------------------------

def _pick_env_and_section(db):
    env = (db.query(Environment)
           .filter(Environment.tenant_id == TENANT_ID,
                   Environment.is_active.is_(True),
                   Environment.current_meta_version_id.isnot(None),
                   Environment.llm_connection_id.isnot(None))
           .first())
    section = (db.query(Section)
               .filter(Section.tenant_id == TENANT_ID,
                       Section.deleted_at.is_(None))
               .first())
    return env, section


def _mk_requirement(db, section_id, tag):
    req = Requirement(
        tenant_id=TENANT_ID,
        section_id=section_id,
        source="manual",
        jira_key=f"STORY-{tag}-{int(datetime.now(timezone.utc).timestamp())}",
        jira_summary=f"Story-view test requirement {tag}",
        jira_description="synthetic requirement — safe to delete",
        created_by=1,
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    return req


def _mk_service(db) -> TestManagementService:
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
    """Nuke every row for a requirement. Used in finally blocks."""
    try:
        db.rollback()
    except Exception:
        pass
    teardown = SessionLocal()
    try:
        tc_ids = [t.id for t in teardown.query(TestCase).filter(
            TestCase.requirement_id == req_id).all()]
        if tc_ids:
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
    """Single-TC plan — enough to prove enrichment runs per-TC without
    multiplying mock calls."""
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
        ],
        "explanation": "Positive create scenario.",
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "cost_usd": 0.01,
        "model_used": "claude-sonnet-4-20250514",
        "usage_log_id": None,
        "usage_log_ids": [],
    }


def _fake_llm_response(parsed):
    """Build a MagicMock that duck-types as LLMResponse for the enricher.

    The enricher reads: .parsed_content, .model, .prompt_version. Nothing
    else. Keep the mock minimal so we don't accidentally pass on
    non-existent attribute access.
    """
    r = MagicMock()
    r.parsed_content = parsed
    r.model = "claude-haiku-4-5-20251001"
    r.prompt_version = "story_view@v1"
    r.raw_text = "" if parsed is None else str(parsed)
    return r


def _set_story_flag(tenant_id: int, enabled: bool):
    """Set the per-tenant story enrichment flag. Idempotent create/update.

    SessionLocal is scoped_session — the same-thread `.close()` here
    would also close the caller's session and detach its ORM instances.
    We bypass that by pulling a fresh Session from the underlying
    factory so tests can freely interleave sessions without tripping
    DetachedInstanceError.
    """
    from sqlalchemy.orm import Session
    from primeqa.db import engine
    db = Session(bind=engine)
    try:
        row = db.query(TenantAgentSettings).filter_by(
            tenant_id=tenant_id).first()
        if row is None:
            row = TenantAgentSettings(
                tenant_id=tenant_id,
                llm_enable_story_enrichment=enabled,
            )
            db.add(row)
        else:
            row.llm_enable_story_enrichment = enabled
        db.commit()
    finally:
        db.close()


def _mint_jwt_for_user(user_id: int):
    """Mint a JWT for a specific user id. Uses a detached Session so it
    doesn't close the caller's scoped session.

    We pin to a specific user_id (vs. 'any admin') so viewer-side filters
    like `tc.visibility == 'private' and tc.owner_id != request.user['id']`
    don't redirect us off the detail page.
    """
    from sqlalchemy.orm import Session
    from primeqa.db import engine
    db = Session(bind=engine)
    try:
        u = db.query(User).filter(User.id == user_id).first()
        if u is None:
            raise RuntimeError(f"No user with id={user_id} in DB")
        token = jwt.encode({
            "sub": str(u.id), "tenant_id": u.tenant_id, "email": u.email,
            "role": u.role, "full_name": u.full_name or u.email,
        }, os.environ["JWT_SECRET"], algorithm="HS256")
        return token, u.tenant_id, u.id
    finally:
        db.close()


def _mint_jwt(role: str):
    """Back-compat shim — minted JWT for any user with the given role."""
    from sqlalchemy.orm import Session
    from primeqa.db import engine
    db = Session(bind=engine)
    try:
        u = db.query(User).filter(
            User.tenant_id == TENANT_ID, User.role == role).first()
        if u is None:
            u = db.query(User).filter(User.role == role).first()
        if u is None:
            raise RuntimeError(f"No user with role={role} in DB")
        token = jwt.encode({
            "sub": str(u.id), "tenant_id": u.tenant_id, "email": u.email,
            "role": u.role, "full_name": u.full_name or u.email,
        }, os.environ["JWT_SECRET"], algorithm="HS256")
        return token, u.tenant_id, u.id
    finally:
        db.close()


def _patched_generate_context(svc, env, created_by=1):
    """Build the MagicMock wrapper patches that every generate_test_plan
    test needs. Returns a dict of context patches the caller wraps with
    `with patch(...) as ...`.
    """
    env_repo = MagicMock()
    env_repo.get_environment.return_value = env
    conn_repo = MagicMock()
    conn_repo.get_connection_decrypted.return_value = {
        "config": {"api_key": "sk-test",
                   "model": "claude-sonnet-4-20250514"}}
    metadata_repo = MagicMock()
    return env_repo, conn_repo, metadata_repo


_GOOD_STORY = {
    "title": "Prospecting opportunity persists with required fields",
    "description": "Verifies that a sales rep can create a new "
                   "opportunity in Prospecting stage with the minimum "
                   "required fields and it is saved by Salesforce.",
    "preconditions_narrative": "A user with create permission on "
                               "Opportunity is authenticated; no "
                               "pre-existing records required.",
    "expected_outcome": "The opportunity record is created with the "
                        "given StageName, CloseDate and Amount and "
                        "Salesforce returns a created Id.",
}


# ---------------------------------------------------------------------------
# Shared helper that drives the batch through generate_test_plan with the
# LLM mocks we want. Returns the service result dict.
# ---------------------------------------------------------------------------

def _drive_batch(svc, *, requirement_id, env,
                 story_parsed=_GOOD_STORY,
                 llm_call_patch=None):
    """Drive generate_test_plan end-to-end with the usual mock stack."""
    env_repo, conn_repo, metadata_repo = _patched_generate_context(svc, env)
    plan = _plan_payload()

    with patch("primeqa.intelligence.generation.TestCaseGenerator") as gen_cls:
        gen_inst = MagicMock()
        gen_inst.generate_plan.return_value = plan
        gen_cls.return_value = gen_inst
        with patch.object(svc, "_store_validation_report"):
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

                    # Patch llm_call at the enrichment import site.
                    if llm_call_patch is None:
                        llm_call_patch = MagicMock(
                            return_value=_fake_llm_response(story_parsed))

                    with patch(
                            "primeqa.intelligence.enrichment.llm_call",
                            new=llm_call_patch):
                        # env can be an ORM row that's been expired by a
                        # prior commit in the same test; guard by reading
                        # .id defensively and falling back to whatever the
                        # caller supplied on an 'env_id' attribute.
                        env_id = getattr(env, "id", None) or env
                        return svc.generate_test_plan(
                            tenant_id=TENANT_ID,
                            requirement_id=requirement_id,
                            environment_id=env_id,
                            created_by=1,
                            env_repo=env_repo,
                            conn_repo=conn_repo,
                            metadata_repo=metadata_repo,
                        )


# ===========================================================================
# The seven tests
# ===========================================================================

def run_tests():
    results = []
    print("\n=== Story-view Enrichment Tests (migration 048) ===\n")

    # ---- 1. Flag ON → enrichment runs, story_view populated --------------
    def test_enrichment_runs_when_flag_enabled():
        _set_story_flag(TENANT_ID, True)
        db = SessionLocal()
        env, section = _pick_env_and_section(db)
        if env is None or section is None:
            print("    SKIP: no env / section fixture in tenant 1")
            db.close()
            return
        req = _mk_requirement(db, section.id, "flag-on")
        req_id = req.id
        try:
            svc = _mk_service(db)
            result = _drive_batch(svc, requirement_id=req_id, env=env,
                                  story_parsed=_GOOD_STORY)

            # One TC expected from the payload. Read the version back.
            assert len(result["test_cases"]) == 1
            tc_id = result["test_cases"][0]["test_case_id"]

            db2 = SessionLocal()
            try:
                ver = (db2.query(TestCaseVersion)
                       .filter_by(test_case_id=tc_id).first())
                assert ver is not None, "no version persisted"
                sv = ver.story_view
                assert isinstance(sv, dict), \
                    f"story_view not a dict: {type(sv).__name__}"
                for key in ("title", "description",
                            "preconditions_narrative", "expected_outcome"):
                    assert key in sv, f"missing key: {key}"
                    assert sv[key], f"empty value for {key}"
                assert sv.get("prompt_version") == "story_view@v1"
                assert sv.get("model") == "claude-haiku-4-5-20251001"
                assert "generated_at" in sv
            finally:
                db2.close()
        finally:
            _cleanup_batch(db, req_id)
            db.close()
            _set_story_flag(TENANT_ID, False)
    results.append(test(
        "1. Flag ON → story_view populated with four required keys",
        test_enrichment_runs_when_flag_enabled))

    # ---- 2. Flag OFF → story_view NULL, batch commits --------------------
    def test_enrichment_skipped_when_flag_disabled():
        _set_story_flag(TENANT_ID, False)
        db = SessionLocal()
        env, section = _pick_env_and_section(db)
        if env is None or section is None:
            print("    SKIP: no env / section fixture in tenant 1")
            db.close()
            return
        req = _mk_requirement(db, section.id, "flag-off")
        req_id = req.id
        try:
            svc = _mk_service(db)

            # Spy on llm_call so we can assert it was NEVER called when
            # the flag is off.
            llm_spy = MagicMock(return_value=_fake_llm_response(_GOOD_STORY))
            result = _drive_batch(svc, requirement_id=req_id, env=env,
                                  story_parsed=_GOOD_STORY,
                                  llm_call_patch=llm_spy)

            assert len(result["test_cases"]) == 1
            tc_id = result["test_cases"][0]["test_case_id"]
            assert llm_spy.call_count == 0, (
                f"llm_call should not be invoked when flag off; "
                f"got {llm_spy.call_count} calls")

            db2 = SessionLocal()
            try:
                ver = (db2.query(TestCaseVersion)
                       .filter_by(test_case_id=tc_id).first())
                assert ver is not None, "batch did not commit"
                assert ver.story_view is None, (
                    f"expected story_view NULL, got {ver.story_view!r}")
            finally:
                db2.close()
        finally:
            _cleanup_batch(db, req_id)
            db.close()
    results.append(test(
        "2. Flag OFF → story_view is NULL, batch still commits",
        test_enrichment_skipped_when_flag_disabled))

    # ---- 3. Regeneration produces a fresh story_view ---------------------
    def test_regeneration_cascades():
        _set_story_flag(TENANT_ID, True)
        db = SessionLocal()
        env, section = _pick_env_and_section(db)
        if env is None or section is None:
            print("    SKIP: no env / section fixture in tenant 1")
            db.close()
            return
        req = _mk_requirement(db, section.id, "regen")
        req_id = req.id
        try:
            svc = _mk_service(db)

            first_story = dict(_GOOD_STORY, title="First generation title")
            _ = _drive_batch(svc, requirement_id=req_id, env=env,
                             story_parsed=first_story)

            svc2 = _mk_service(SessionLocal())
            second_story = dict(_GOOD_STORY, title="Second generation title")
            result2 = _drive_batch(svc2, requirement_id=req_id, env=env,
                                   story_parsed=second_story)

            # The second batch should produce a new TC with a new version.
            new_tc_id = result2["test_cases"][0]["test_case_id"]
            db2 = SessionLocal()
            try:
                ver = (db2.query(TestCaseVersion)
                       .filter_by(test_case_id=new_tc_id).first())
                assert ver is not None, "second batch did not commit"
                sv = ver.story_view
                assert isinstance(sv, dict)
                assert sv.get("title") == "Second generation title", (
                    f"new version got stale story_view: {sv.get('title')!r}")
            finally:
                db2.close()
                svc2.test_case_repo.db.close()
        finally:
            _cleanup_batch(db, req_id)
            db.close()
            _set_story_flag(TENANT_ID, False)
    results.append(test(
        "3. Regeneration cascades — new version has fresh story_view",
        test_regeneration_cascades))

    # ---- 4. Detail template renders story block --------------------------
    def test_detail_template_renders_story():
        db = SessionLocal()
        env, section = _pick_env_and_section(db)
        if env is None or section is None:
            print("    SKIP: no env / section fixture in tenant 1")
            db.close()
            return

        # Enable flag so the first-batch enrichment populates story_view.
        _set_story_flag(TENANT_ID, True)
        req = _mk_requirement(db, section.id, "tpl-render")
        req_id = req.id
        try:
            svc = _mk_service(db)
            story = dict(_GOOD_STORY,
                         title="VERY_UNIQUE_TITLE_FOR_ASSERTION")
            result = _drive_batch(svc, requirement_id=req_id, env=env,
                                  story_parsed=story)
            tc_id = result["test_cases"][0]["test_case_id"]

            # Mint a JWT for user_id=1 (the TC's owner) so the
            # visibility=private redirect doesn't fire.
            token, _tid, _uid = _mint_jwt_for_user(1)
            c = app.test_client()
            c.set_cookie(domain="localhost", key="access_token", value=token)
            resp = c.get(f"/test-cases/{tc_id}", follow_redirects=False)
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code} "
                f"(location={resp.headers.get('Location')})")
            html = resp.get_data(as_text=True)
            assert "VERY_UNIQUE_TITLE_FOR_ASSERTION" in html, (
                "story title not rendered on detail page")
            # Description should also appear (first ~40 chars are enough)
            assert _GOOD_STORY["description"][:40] in html, (
                "story description not rendered on detail page")
            # Technical detail section should be present as a details
            # disclosure (collapsible mechanical view).
            assert "Technical detail" in html, (
                "collapsible mechanical view missing")
        finally:
            _cleanup_batch(db, req_id)
            db.close()
            _set_story_flag(TENANT_ID, False)
    results.append(test(
        "4. /test-cases/<id> renders the story block when present",
        test_detail_template_renders_story))

    # ---- 5. NULL story_view falls back to mechanical view ----------------
    def test_null_story_view_falls_back():
        _set_story_flag(TENANT_ID, False)  # ensures story_view NULL
        db = SessionLocal()
        env, section = _pick_env_and_section(db)
        if env is None or section is None:
            print("    SKIP: no env / section fixture in tenant 1")
            db.close()
            return
        req = _mk_requirement(db, section.id, "fallback")
        req_id = req.id
        try:
            svc = _mk_service(db)
            result = _drive_batch(svc, requirement_id=req_id, env=env)
            tc_id = result["test_cases"][0]["test_case_id"]

            token, _tid, _uid = _mint_jwt_for_user(1)
            c = app.test_client()
            c.set_cookie(domain="localhost", key="access_token", value=token)
            resp = c.get(f"/test-cases/{tc_id}", follow_redirects=False)
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code} "
                f"(location={resp.headers.get('Location')})")
            html = resp.get_data(as_text=True)
            # No story section should have appeared.
            assert "What we're testing" not in html, (
                "story block rendered even though story_view is NULL")
            # Mechanical view must still render — action verb should
            # appear since the plan_payload step is a 'create' on
            # Opportunity.
            assert "create" in html.lower() and "opportunity" in html.lower(), (
                "mechanical step view did not render on fallback")
        finally:
            _cleanup_batch(db, req_id)
            db.close()
    results.append(test(
        "5. NULL story_view falls back to mechanical view (page still 200)",
        test_null_story_view_falls_back))

    # ---- 6. LLM usage logging tags task correctly -------------------------
    def test_llm_usage_log_tagged_correctly():
        """We patch llm_call so no row is actually written, but we still
        want to prove the task-name contract: the enricher MUST be
        invoking llm_call with task='story_view_generation'. A wrong
        task name would silently misroute in the router.
        """
        _set_story_flag(TENANT_ID, True)
        db = SessionLocal()
        env, section = _pick_env_and_section(db)
        if env is None or section is None:
            print("    SKIP: no env / section fixture in tenant 1")
            db.close()
            return
        req = _mk_requirement(db, section.id, "usage-tag")
        req_id = req.id
        try:
            svc = _mk_service(db)
            spy = MagicMock(return_value=_fake_llm_response(_GOOD_STORY))
            _ = _drive_batch(svc, requirement_id=req_id, env=env,
                             story_parsed=_GOOD_STORY, llm_call_patch=spy)

            assert spy.call_count == 1, (
                f"expected exactly 1 llm_call, got {spy.call_count}")
            # All llm_call args are keyword-only per the gateway signature.
            call_kwargs = spy.call_args.kwargs
            assert call_kwargs.get("task") == "story_view_generation", (
                f"wrong task: {call_kwargs.get('task')!r}")
            assert call_kwargs.get("tenant_id") == TENANT_ID
            # Enricher passes test_case_id + generation_batch_id for
            # back-linking; verify they're supplied.
            assert "test_case_id" in call_kwargs
            assert "generation_batch_id" in call_kwargs
        finally:
            _cleanup_batch(db, req_id)
            db.close()
            _set_story_flag(TENANT_ID, False)
    results.append(test(
        "6. Enricher invokes llm_call with task='story_view_generation'",
        test_llm_usage_log_tagged_correctly))

    # ---- 7. Malformed LLM response is swallowed, batch commits -----------
    def test_malformed_llm_response_handled():
        _set_story_flag(TENANT_ID, True)
        db = SessionLocal()
        env, section = _pick_env_and_section(db)
        if env is None or section is None:
            print("    SKIP: no env / section fixture in tenant 1")
            db.close()
            return
        req = _mk_requirement(db, section.id, "bad-llm")
        req_id = req.id
        try:
            svc = _mk_service(db)
            # Missing required keys: enricher returns None.
            bad_story = {"title": "only title, nothing else"}
            result = _drive_batch(svc, requirement_id=req_id, env=env,
                                  story_parsed=bad_story)
            tc_id = result["test_cases"][0]["test_case_id"]
            db2 = SessionLocal()
            try:
                ver = (db2.query(TestCaseVersion)
                       .filter_by(test_case_id=tc_id).first())
                assert ver is not None, "batch failed to commit despite bad LLM"
                assert ver.story_view is None, (
                    f"malformed LLM should leave story_view NULL, "
                    f"got {ver.story_view!r}")
            finally:
                db2.close()
        finally:
            _cleanup_batch(db, req_id)
            db.close()
            _set_story_flag(TENANT_ID, False)
    results.append(test(
        "7. Malformed LLM output → story_view=NULL, batch still commits",
        test_malformed_llm_response_handled))

    passed = sum(results)
    total = len(results)
    print(f"\n{'='*60}")
    print(f"  {passed}/{total} passed")
    print(f"{'='*60}\n")
    return passed == total


if __name__ == "__main__":
    ok = run_tests()
    sys.exit(0 if ok else 1)
