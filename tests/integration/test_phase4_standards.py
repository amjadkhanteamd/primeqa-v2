"""Phase 4 DB-real tests — gated on S3A3_TEST_DATABASE_URL (scratch with
migrations 062-066 applied and P-1's run copied in). Covers the map-set
lifecycle, the derivation cross-check, and the three-standard render."""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DB, reason="set S3A3_TEST_DATABASE_URL "
                       "(non-prod scratch with 062-066 + P-1 copied)"),
]

SUPER = dict(actor_user_id=7, actor_tenant_id=1, actor_role="superadmin")
P1_JOB = uuid.UUID("c70fa8e6-888d-4a6f-9087-f18fd8ef3196")


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


def test_c_map_set_lifecycle_and_no_rule_versioning(session):
    from primeqa.knowledge import rule_lifecycle as lc

    before = session.execute(text(
        "SELECT COUNT(*) FROM s5_rule_versions WHERE version > 1")).scalar_one()

    ms = lc.create_map_set(
        session, standard="EN301549",
        standard_version=f"EN probe {uuid.uuid4().hex[:6]}",
        provenance={"probe": True}, notes="lifecycle probe", **SUPER)
    # authoring is legal in DRAFT
    lc.add_standard_map(session, rule_id="PLM-A11Y-001", version=1,
                        standard="EN301549", criterion="9.4.1.2", level="A",
                        map_set_id=ms, provenance={"probe": True}, **SUPER)
    lc.transition_map_set(session, map_set_id=ms, to_state="REVIEW", **SUPER)
    # ...and refused from REVIEW onward
    with pytest.raises(lc.LifecycleError, match="requires the SET in DRAFT"):
        lc.add_standard_map(session, rule_id="PLM-A11Y-002", version=1,
                            standard="EN301549", criterion="9.1.1.1",
                            level="A", map_set_id=ms, **SUPER)
    lc.transition_map_set(session, map_set_id=ms, to_state="APPROVED", **SUPER)
    row = session.execute(text(
        "SELECT reviewed_by, reviewed_at IS NOT NULL, content_hash "
        "FROM s5_standard_map_sets WHERE id=:i"), {"i": ms}).fetchone()
    assert row[0] == 7 and row[1] is True and row[2]     # real actor + hash

    # single-ACTIVE is a DB guarantee, not a code convention
    with pytest.raises(Exception, match="s5_standard_map_sets_single_active"):
        session.execute(text("""
            INSERT INTO s5_standard_map_sets
                (standard, standard_version, state, created_by)
            VALUES ('EN301549', :v, 'ACTIVE', 7)"""),
            {"v": f"dup {uuid.uuid4().hex[:6]}"})
    session.rollback()

    # and NO rule version was cut by any of it
    after = session.execute(text(
        "SELECT COUNT(*) FROM s5_rule_versions WHERE version > 1")).scalar_one()
    assert after == before == 0


def test_d_derivation_and_engine_cross_check(session):
    from primeqa.knowledge.standard_derivation import derive_candidates

    en = derive_candidates(session, "EN301549", release_id=3)
    s508 = derive_candidates(session, "SECTION508", release_id=3)

    # every EN candidate is corroborated by the engine's own EN clause tag
    assert en["agreements"] == len(en["candidates"])
    assert en["disagreements"] == []
    assert s508["disagreements"] == []

    # 508 binds WCAG 2.0 only, so it maps strictly fewer criteria than EN
    assert len(s508["candidates"]) < len(en["candidates"])
    # the WCAG 2.1/2.2-only criteria are refused a map, not given a false one
    reasons = " ".join(x["reason"] for x in s508["out_of_scope"])
    assert "outside SECTION508's bound WCAG version" in reasons
    # the heading/landmark rules cannot be derived — a human must author
    assert {x["rule_id"] for x in en["requires_authoring"]} == {
        "PLM-A11Y-069", "PLM-A11Y-070", "PLM-A11Y-071", "PLM-A11Y-072"}


def test_e_acc05_closure_state(session):
    pair = ("PLM-A11Y-073", "PLM-A11Y-074")
    states = dict(session.execute(text("""
        SELECT rule_id, state FROM s5_rule_versions WHERE rule_id IN :p"""
        ).bindparams(p=pair)).fetchall())
    assert set(states.values()) == {"ACTIVE"}
    # mapped for EN + 508, and NEVER for WCAG22 (4.1.1 is gone in 2.2)
    by_std = dict(session.execute(text("""
        SELECT x.s, x.n FROM (SELECT standard s, COUNT(*) n
        FROM s5_standard_maps WHERE rule_id IN :p GROUP BY 1) x"""
        ).bindparams(p=pair)).fetchall())
    assert by_std.get("WCAG22", 0) == 0
    assert by_std["EN301549"] == 2 and by_std["SECTION508"] == 2
    # the deprecation rationale rides the map provenance, verbatim
    prov = session.execute(text("""
        SELECT provenance->>'deprecation_rationale' FROM s5_standard_maps
        WHERE rule_id='PLM-A11Y-073' AND standard='EN301549'""")).scalar_one()
    assert "the engine's lifecycle signal about its own rule" in prov
    assert "4.1.1 is live in WCAG 2.0/2.1" in prov
    # and release 3's run set names them, so they actually execute
    from primeqa.execution_engine.ui_manifest import engine_run_set
    rs = engine_run_set(session, 3, "axe-core", "4.13.0")
    assert {"duplicate-id", "duplicate-id-active"} <= set(rs)


def test_f_three_standards_over_p1_and_nothing_passes(session):
    from primeqa.interpretation.standard_view import standard_view

    seen = {}
    for std in ("WCAG22", "EN301549", "SECTION508"):
        v = standard_view(session, standard=std, job_id=P1_JOB)
        seen[std] = v
        # P-1 holds 0 PASS verdicts, so NO criterion may read PASS
        assert not [c for c in v["criteria"]
                    if c["criterion_verdict"] == "PASS"]
        # the honesty header names the exact projection + run
        h = v["header"]
        assert h["standard_version"] and h["map_set_id"]
        assert h["denominator_complete"] is False
        assert h["engine"] == "axe-core"
        assert h["catalogue_release_id"] == 2      # P-1 ran on release 2
        # covered-but-undetermined is the honest state after D-466
        assert v["criterion_verdict_counts"].get("NOT_DETERMINED", 0) > 0
        # a criterion with no bound rule is present, not absent
        assert set(c["coverage"] for c in v["criteria"]) <= {
            "AUTOMATED", "HUMAN_ONLY", "NOT_COVERED"}

    # the SAME underlying failure surfaces in every standard, under that
    # standard's own numbering — the phase's whole thesis
    fails = {s: sorted(c["criterion"] for c in v["criteria"]
                       if c["criterion_verdict"] == "FAIL")
             for s, v in seen.items()}
    assert fails["WCAG22"] == ["1.3.1", "2.4.1"]
    assert fails["SECTION508"] == ["1.3.1", "2.4.1"]   # 508 does not renumber
    assert fails["EN301549"] == ["9.1.3.1", "9.2.4.1"]  # EN does
