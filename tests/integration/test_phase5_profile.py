"""Phase 5 Part 3 DB-real tests — gated on S3A3_TEST_DATABASE_URL
(scratch with tenant 20260903_0010 applied, Part 2's PLM-CUST-00001
ACTIVE and the refusal ledger populated). Covers: the profile-set
lifecycle, CUSTOM:<profile> rendered through standard_view against the
ratified heading denominator, refusals as first-class rows beside
NOT_COVERED (on the profile view AND the public standards), and the
orphan-rule surfacing."""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DB, reason="set S3A3_TEST_DATABASE_URL "
                       "(scratch with tenant 20260903_0010)"),
]

ADMIN = dict(actor_user_id=1, actor_tenant_id=1, actor_role="admin")
B1_JOB = uuid.UUID("471a9c35-13d6-466a-bd7b-38b809f9aac6")
HEADINGS = ["Brand/Targets", "Brand/Labels", "Brand/Contrast"]


@pytest.fixture()
def session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    eng = create_engine(DB, pool_pre_ping=True, connect_args={
        "options": "-csearch_path=tenant_1,public -capp.tenant_id=1"})
    s = Session(bind=eng)
    yield s
    s.rollback()
    s.close()


def _active_profile(session, key="acme"):
    return session.execute(text(
        "SELECT id FROM cust_profile_sets WHERE profile_key=:k "
        "AND state='ACTIVE'"), {"k": key}).scalar()


def _ensure_refusal(session):
    from primeqa.knowledge import cust_authoring as A
    n = session.execute(text(
        "SELECT COUNT(*) FROM cust_authoring_ledger WHERE outcome='refused'"
    )).scalar_one()
    if n == 0:
        A.record_refusal(
            session, guideline_thread_id="GT-BRAND-03",
            prose="Buttons must consume --brand-primary, never a "
                  "hardcoded hex.",
            refusal_class="needs_capability_not_captured",
            refusal_reason="computed style is post-resolution: token and "
                           "hex are byte-identical in the observation",
            nearest_expressible=[{"predicate": {
                "form": "member_of", "fact": "style:background-color",
                "token_set": {"key": "brand-palette", "version": 1}}}],
            **ADMIN)


def test_a_profile_set_lifecycle(session):
    from primeqa.knowledge import cust_authoring as A

    if _active_profile(session):
        pytest.skip("profile set already ACTIVE (idempotent replay)")
    sid = A.create_profile_set(session, profile_key="acme",
                               notes="ACME brand guidelines", **ADMIN)
    # empty set cannot be approved
    A.transition_profile_set(session, set_id=sid, to_state="REVIEW", **ADMIN)
    with pytest.raises(A.AuthoringError, match="EMPTY profile set"):
        A.transition_profile_set(session, set_id=sid, to_state="APPROVED",
                                 **ADMIN)
    session.rollback()
    # authoring is DRAFT-gated
    with pytest.raises(A.AuthoringError, match="requires the SET in DRAFT"):
        A.add_profile_criterion(session, set_id=sid,
                                criterion="Brand/Targets", **ADMIN)
    session.rollback()
    # a fresh DRAFT revision carries the headings and ratifies
    sid2 = A.create_profile_set(session, profile_key="acme", revision=2,
                                notes="with headings", **ADMIN)
    for h in HEADINGS:
        A.add_profile_criterion(session, set_id=sid2, criterion=h,
                                title=h.split("/")[1], **ADMIN)
    A.transition_profile_set(session, set_id=sid2, to_state="REVIEW", **ADMIN)
    A.transition_profile_set(session, set_id=sid2, to_state="APPROVED",
                             **ADMIN)
    row = session.execute(text("""
        SELECT reviewed_by, content_hash FROM cust_profile_sets
        WHERE id=:i"""), {"i": sid2}).fetchone()
    assert row[0] == 1 and row[1]
    A.transition_profile_set(session, set_id=sid2, to_state="ACTIVE", **ADMIN)
    assert _active_profile(session) == sid2
    # single-ACTIVE per key is a DB guarantee
    with pytest.raises(Exception, match="cust_profile_sets_single_active"):
        session.execute(text("""
            INSERT INTO cust_profile_sets
                (profile_key, revision, state, created_by)
            VALUES ('acme', 9, 'ACTIVE', 1)"""))
    session.rollback()


def test_b_custom_profile_renders_through_standard_view(session):
    from primeqa.interpretation.standard_view import standard_view

    if not _active_profile(session):
        pytest.skip("run test_a first (fresh scratch)")
    v = standard_view(session, standard="CUSTOM:acme", job_id=B1_JOB)
    h = v["header"]
    assert h["standard"] == "CUSTOM:acme"
    assert h["denominator_provenance"] == "ratified_profile"
    assert h["denominator_complete"] is True
    assert h["profile_set_content_hash"]
    assert h["engine_run_set_size"] == 74          # the run header still rides
    assert v["denominator"]["size"] == 3
    by = {r["criterion"]: r for r in v["criteria"]}
    assert set(by) == set(HEADINGS)
    # Brand/Targets is covered by PLM-CUST-00001's own ratified content
    t = by["Brand/Targets"]
    assert t["coverage"] == "AUTOMATED"
    assert [c["rule_id"] for c in t["contributing_rules"]] == ["PLM-CUST-00001"]
    # B-1 predates custom rules: covered-but-undetermined, honestly
    assert t["criterion_verdict"] is None
    assert by["Brand/Contrast"]["coverage"] == "NOT_COVERED"
    assert v["denominator"]["covered"] >= 1
    assert v["coverage_counts"]["NOT_COVERED"] >= 1


def test_c_refusals_are_first_class_beside_not_covered(session):
    from primeqa.interpretation.standard_view import standard_view

    if not _active_profile(session):
        pytest.skip("run test_a first (fresh scratch)")
    _ensure_refusal(session)
    for std in ("CUSTOM:acme", "WCAG22"):
        v = standard_view(session, standard=std, job_id=B1_JOB)
        ref = v["refusals"]
        assert ref["available"] is True and ref["count"] >= 1
        token_case = [r for r in ref["rows"]
                      if r["refusal_class"] == "needs_capability_not_captured"
                      and "token" in (r["refusal_reason"] or "")]
        assert token_case, "the token-vs-literal refusal must be visible"
        assert token_case[0]["nearest_expressible"]
        assert "beside" in ref["note"] or "NOT_COVERED" in ref["note"]


def test_d_an_unratified_heading_is_an_orphan_rule_not_a_silent_drop(session):
    from primeqa.interpretation.standard_view import standard_view

    if not _active_profile(session):
        pytest.skip("run test_a first (fresh scratch)")
    orphan_active = session.execute(text("""
        SELECT COUNT(*) FROM cust_rule_versions
        WHERE state='ACTIVE'
          AND definition->'criterion'->>'profile' NOT IN
              (SELECT criterion FROM cust_profile_criteria cpc
               JOIN cust_profile_sets cps ON cps.id = cpc.set_id
               WHERE cps.state='ACTIVE')""")).scalar_one()
    v = standard_view(session, standard="CUSTOM:acme", job_id=B1_JOB)
    assert len(v["orphan_rules"]) == orphan_active
    # ...and an unknown profile refuses rather than rendering emptily
    from primeqa.interpretation.standard_view import StandardViewError
    with pytest.raises(StandardViewError, match="no ACTIVE profile set"):
        standard_view(session, standard="CUSTOM:ghost", job_id=B1_JOB)
