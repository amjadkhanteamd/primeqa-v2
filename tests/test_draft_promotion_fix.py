"""Item 1 — draft-status stickiness fix (A + B).

A — fail-loud promotion guard in submit_review: approving a review whose TC was
    soft-deleted / superseded must NOT silently succeed. It surfaces a typed
    ConflictError and the TC is not falsely promoted.
B — supersession cleanup in soft_delete_test_case: soft-deleting a TC cancels
    its pending BA reviews (they leave the review queue), removing the condition
    that triggers the A-path.
Regression — the normal path (live draft -> approve) still promotes the TC to
    approved / shared.

Integration-style against the configured DB, same convention as
test_review_queue.py: seed a TC + version + pending review via SessionLocal,
exercise the service / repo, clean up in finally (hard-delete cascades).
"""
import os
import sys
from datetime import datetime, timezone
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text

from primeqa.app import app  # noqa: F401 — importing the app calls init_db()
from primeqa.core.models import User
from primeqa.core.repository import ActivityLogRepository
from primeqa.db import SessionLocal
from primeqa.shared.api import ConflictError
from primeqa.test_management.models import BAReview, TestCase, TestCaseVersion
from primeqa.test_management.repository import (
    BAReviewRepository, RequirementRepository,
    SectionRepository, TestCaseRepository, TestSuiteRepository,
)
from primeqa.test_management.service import TestManagementService

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


def _service(db):
    return TestManagementService(
        section_repo=SectionRepository(db),
        requirement_repo=RequirementRepository(db),
        test_case_repo=TestCaseRepository(db),
        suite_repo=TestSuiteRepository(db),
        review_repo=BAReviewRepository(db),
        activity_repo=ActivityLogRepository(db),
    )


def _fixture_ids(db):
    """A tenant-1 user id, a valid meta_versions id (reused from an existing
    version so the NOT NULL FK is satisfied), and a section id (TestCase
    requires a requirement or section anchor — `test_cases_anchor_check`).
    Returns (uid, meta_vid, section_id) or None when the tenant lacks fixture
    data (test then skips)."""
    user = db.query(User).filter_by(tenant_id=TENANT_ID).first()
    meta_vid = (
        db.query(TestCaseVersion.metadata_version_id)
        .join(TestCase, TestCase.id == TestCaseVersion.test_case_id)
        .filter(TestCase.tenant_id == TENANT_ID)
        .first()
    )
    section = db.execute(
        text("SELECT id FROM sections WHERE tenant_id = :t LIMIT 1"),
        {"t": TENANT_ID},
    ).first()
    if user is None or meta_vid is None or section is None:
        return None
    return user.id, meta_vid[0], section[0]


def _seed(db, uid, meta_vid, section_id, *, tc_status="draft"):
    """Create TC(draft) + version + pending review. Returns (tc_id, review_id)."""
    tc = TestCase(
        tenant_id=TENANT_ID, title=f"PQA_ITEM1_FIX_{uuid4().hex[:8]}",
        owner_id=uid, created_by=uid, status=tc_status, visibility="private",
        section_id=section_id,
    )
    db.add(tc)
    db.flush()
    tcv = TestCaseVersion(
        test_case_id=tc.id, version_number=1, metadata_version_id=meta_vid,
        generation_method="ai", created_by=uid,
    )
    db.add(tcv)
    db.flush()
    review = BAReview(
        tenant_id=TENANT_ID, test_case_version_id=tcv.id, assigned_to=uid,
        status="pending", review_reason="low_confidence",
    )
    db.add(review)
    db.flush()
    db.commit()
    return tc.id, review.id


def _cleanup(tc_id):
    # Raw deletes in FK order — bypass the ORM relationship cascade (which would
    # try to NULL the version's NOT NULL test_case_id) and don't depend on DB
    # ON DELETE CASCADE being configured.
    db = SessionLocal()
    try:
        db.execute(text(
            "DELETE FROM ba_reviews WHERE test_case_version_id IN "
            "(SELECT id FROM test_case_versions WHERE test_case_id = :id)"
        ), {"id": tc_id})
        db.execute(text(
            "DELETE FROM test_case_versions WHERE test_case_id = :id"
        ), {"id": tc_id})
        db.execute(text("DELETE FROM test_cases WHERE id = :id"), {"id": tc_id})
        db.commit()
    finally:
        db.close()


def run_tests():
    results = []
    print("\n=== Item 1 — draft-status stickiness fix (A + B) ===\n")

    db0 = SessionLocal()
    try:
        fx = _fixture_ids(db0)
    finally:
        db0.close()
    if fx is None:
        print("  SKIP  no tenant-1 user / meta version / section fixture available")
        return True
    uid, meta_vid, section_id = fx

    # --- A: approving a review whose TC is soft-deleted must fail loud --------
    def test_a_fail_loud_on_superseded_tc():
        db = SessionLocal()
        tc_id = None
        try:
            tc_id, review_id = _seed(db, uid, meta_vid, section_id)
            # Soft-delete the TC directly (simulating a soft-delete path that
            # did NOT cancel the review — the residual case A guards). Bypasses
            # B on purpose so the review stays pending.
            tc = db.query(TestCase).filter_by(id=tc_id).first()
            tc.deleted_at = datetime.now(timezone.utc)
            db.commit()

            svc = _service(db)
            raised = None
            try:
                svc.submit_review(review_id, "approved", reviewed_by=uid)
            except ConflictError as e:
                raised = e
            assert raised is not None, \
                "expected ConflictError; promotion silently succeeded"
            assert raised.http == 409 and raised.code == "CONFLICT"

            # TC was NOT falsely promoted — still draft (and still soft-deleted).
            fresh = SessionLocal()
            try:
                tc2 = fresh.query(TestCase).filter_by(id=tc_id).first()
                assert tc2.status == "draft", \
                    f"TC must not be promoted; status={tc2.status}"
            finally:
                fresh.close()
        finally:
            db.close()
            if tc_id is not None:
                _cleanup(tc_id)
    results.append(test("A. approve on superseded TC fails loud, no false promote",
                        test_a_fail_loud_on_superseded_tc))

    # --- B: supersession (soft_delete_test_case) cancels pending reviews ------
    def test_b_soft_delete_cancels_reviews():
        db = SessionLocal()
        tc_id = None
        try:
            tc_id, review_id = _seed(db, uid, meta_vid, section_id)
            # The pending review is visible before deletion.
            before = _service(db).list_reviews(TENANT_ID, status="pending")
            assert any(r["id"] == review_id for r in before), \
                "seeded review should be in the pending queue"

            _service(db).test_case_repo.soft_delete_test_case(tc_id, TENANT_ID, uid)

            fresh = SessionLocal()
            try:
                rev = fresh.query(BAReview).filter_by(id=review_id).first()
                assert rev.deleted_at is not None, \
                    "soft_delete_test_case must cancel the pending review"
                after = _service(fresh).list_reviews(TENANT_ID, status="pending")
                assert not any(r["id"] == review_id for r in after), \
                    "cancelled review must leave the pending queue"
            finally:
                fresh.close()
        finally:
            db.close()
            if tc_id is not None:
                _cleanup(tc_id)
    results.append(test("B. soft_delete_test_case cancels the TC's pending reviews",
                        test_b_soft_delete_cancels_reviews))

    # --- Regression: normal live draft -> approve still promotes --------------
    def test_regression_live_approve_promotes():
        db = SessionLocal()
        tc_id = None
        try:
            tc_id, review_id = _seed(db, uid, meta_vid, section_id)
            result = _service(db).submit_review(review_id, "approved", reviewed_by=uid)
            assert result is not None and result.get("status") == "approved"

            fresh = SessionLocal()
            try:
                tc2 = fresh.query(TestCase).filter_by(id=tc_id).first()
                assert tc2.status == "approved", f"status={tc2.status}"
                assert tc2.visibility == "shared", f"visibility={tc2.visibility}"
            finally:
                fresh.close()
        finally:
            db.close()
            if tc_id is not None:
                _cleanup(tc_id)
    results.append(test("Regression. live draft -> approve promotes to approved/shared",
                        test_regression_live_approve_promotes))

    passed = sum(results)
    total = len(results)
    print(f"\n{'='*60}")
    print(f"  {passed}/{total} passed")
    print(f"{'='*60}\n")
    return passed == total


if __name__ == "__main__":
    ok = run_tests()
    sys.exit(0 if ok else 1)
