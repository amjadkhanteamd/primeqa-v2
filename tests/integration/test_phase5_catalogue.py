"""Phase 5 Part 1 DB-real tests — gated on S3A3_TEST_DATABASE_URL (scratch
with migrations 062-067 applied, the revision-2 standard sets ACTIVE and
B-1's run copied in). Covers the pins, the ratified catalogue on the
ACTIVE sets, the widened content hash, the standard views' real
denominator over B-1, and the ingest's refusals."""
from __future__ import annotations

import hashlib
import os
import uuid

import pytest
from sqlalchemy import text

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DB, reason="set S3A3_TEST_DATABASE_URL "
                       "(non-prod scratch with 062-067 + B-1 copied)"),
]

SUPER = dict(actor_user_id=7, actor_tenant_id=1, actor_role="superadmin")
B1_JOB = uuid.UUID("471a9c35-13d6-466a-bd7b-38b809f9aac6")
EXPECTED = {  # in-scope denominator, criteria ingested, levels
    "WCAG22": (55, 86, {"A": 31, "AA": 24, "AAA": 31}),
    "EN301549": (50, 50, {"A": 30, "AA": 20}),
    "SECTION508": (38, 38, {"A": 25, "AA": 13}),
}


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


def test_a_every_pin_agrees_db_module_file(session):
    from primeqa.knowledge import criterion_catalogue as CC
    checked = CC.require_artifact_pins(session)
    assert len(checked) == 5
    for c in checked:
        p = CC.artifact_path(c["name"], c["version"])
        assert hashlib.sha256(p.read_bytes()).hexdigest() == c["sha256"]
        assert c["source_url"] and c["retrieved_at"].startswith("2026-09-01")


def test_b_active_sets_carry_the_ratified_catalogue(session):
    from primeqa.knowledge import criterion_catalogue as CC
    for std, (in_scope, total, levels) in EXPECTED.items():
        d = CC.catalogue_denominator(session, std)
        assert d is not None and d["complete"] is True
        assert d["provenance"] == "ratified_catalogue"
        assert len(d["criteria"]) == total
        census = {}
        for c in d["criteria"]:
            census[c["level"]] = census.get(c["level"], 0) + 1
        assert census == levels
        assert sum(1 for c in d["criteria"] if c["level"] in ("A", "AA")) == in_scope
        # reproducible by hash: the stored rows ARE a parse of the pinned bytes
        assert CC.stored_rows_hash(session, d["set_id"]) == \
            CC.catalogue_for(std).rows_hash() == d["catalogue_rows_hash"]
        # the set is revision 2, ACTIVE, reviewed by 1; revision 1 retired
        row = session.execute(text("""
            SELECT revision, state, reviewed_by, content_hash,
                   provenance->'ratification'->>'review_act'
            FROM s5_standard_map_sets WHERE id=:i"""),
            {"i": d["set_id"]}).fetchone()
        assert row[0] == 2 and row[1] == "ACTIVE" and row[2] == 1 and row[3]
        assert "GO" in row[4]
        old = session.execute(text("""
            SELECT state FROM s5_standard_map_sets
            WHERE standard=:s AND revision=1
              AND standard_version=:v"""),
            {"s": std, "v": d["standard_version"]}).scalar_one()
        assert old == "RETIRED"


def test_c_content_hash_covers_catalogue_and_maps(session):
    """Recompute the APPROVED digest exactly as the lifecycle does: maps
    then criteria, under one hash. A hash over maps alone must differ."""
    for std in EXPECTED:
        sid, stored = session.execute(text(
            "SELECT id, content_hash FROM s5_standard_map_sets "
            "WHERE standard=:s AND state='ACTIVE'"), {"s": std}).fetchone()
        maps = session.execute(text("""
            SELECT rule_id, rule_version, standard, criterion, COALESCE(level, '')
            FROM s5_standard_maps WHERE map_set_id=:i ORDER BY rule_id, criterion
        """), {"i": sid}).fetchall()
        crit = session.execute(text("""
            SELECT criterion, title, COALESCE(level, ''), ordinal,
                   COALESCE(binds_wcag_sc, '')
            FROM s5_criteria WHERE set_id=:i ORDER BY ordinal, criterion
        """), {"i": sid}).fetchall()
        lines = ["|".join(str(c) for c in m) for m in maps]
        maps_only = hashlib.sha256("\n".join(lines).encode()).hexdigest()
        lines += ["criterion|" + "|".join(str(c) for c in r) for r in crit]
        both = hashlib.sha256("\n".join(lines).encode()).hexdigest()
        assert stored.strip() == both != maps_only


def test_d_no_mismatch_no_orphan_on_the_active_sets(session):
    from primeqa.knowledge import criterion_catalogue as CC
    for std in EXPECTED:
        sid = session.execute(text(
            "SELECT id FROM s5_standard_map_sets WHERE standard=:s AND state='ACTIVE'"),
            {"s": std}).scalar_one()
        rep = CC.level_mismatch_report(session, sid)
        assert rep["mismatches"] == [] and rep["orphans"] == []
        # the report that WAS loud is preserved on the set
        prov = session.execute(text(
            "SELECT provenance FROM s5_standard_map_sets WHERE id=:i"),
            {"i": sid}).scalar_one()
        assert "level_mismatch_report" in prov
    wcag = session.execute(text("""
        SELECT provenance->'level_mismatch_report'->'mismatches'
        FROM s5_standard_map_sets WHERE standard='WCAG22' AND state='ACTIVE'""")).scalar_one()
    assert [(m["rule_id"], m["criterion"], m["map_level"], m["catalogue_level"])
            for m in wcag] == [("PLM-A11Y-059", "2.1.3", "A", "AAA")]
    for std, crit in (("EN301549", "9.2.1.3"), ("SECTION508", "2.1.3")):
        wd = session.execute(text("""
            SELECT provenance->'withdrawn_maps' FROM s5_standard_map_sets
            WHERE standard=:s AND state='ACTIVE'"""), {"s": std}).scalar_one()
        assert [(w["rule_id"], w["criterion"]) for w in wd] == [("PLM-A11Y-059", crit)]


def test_e_views_over_b1_count_against_the_real_denominator(session):
    from primeqa.interpretation.standard_view import standard_view
    want = {"WCAG22": (21, 55, 34), "EN301549": (21, 50, 29),
            "SECTION508": (19, 38, 19)}
    for std, (n, m, nc) in want.items():
        v = standard_view(session, standard=std, job_id=B1_JOB)
        h = v["header"]
        assert h["denominator_provenance"] == "ratified_catalogue"
        assert h["denominator_complete"] is True and h["denominator_limitation"] is None
        assert h["catalogue_rows_hash"] and h["catalogue_artifacts"]
        assert h["engine_run_set_size"] == 74 and h["catalogue_release_id"] == 3
        assert v["denominator"] == {"size": m, "covered": n, "complete": True}
        assert v["coverage_counts"].get("NOT_COVERED", 0) == nc
        assert v["coverage_counts"].get("AUTOMATED", 0) == n
        assert not [r for r in v["criteria"] if r["orphan"]]
        assert all(r["level_source"] == "catalogue" for r in v["criteria"]
                   if r["contributing_rules"] or r["in_scope"])
        assert all(r["title"] for r in v["criteria"] if r["in_scope"])
        in_scope_fail = sorted(r["criterion"] for r in v["criteria"]
                               if r["in_scope"] and r["criterion_verdict"] == "FAIL")
        if std == "EN301549":
            assert in_scope_fail == ["9.1.3.1", "9.2.4.1"]
        else:
            assert in_scope_fail == ["1.3.1", "2.4.1"]
    # B-1's 1.4.6 FAIL is OUTSIDE the AA gate, at its true level, still visible
    v = standard_view(session, standard="WCAG22", job_id=B1_JOB)
    oos = {r["criterion"]: r for r in v["out_of_scope"]["criteria"]}
    assert set(oos) == {"1.4.6", "2.1.3", "2.2.4", "2.4.9", "3.2.5"}
    assert oos["1.4.6"]["level"] == "AAA" and oos["1.4.6"]["criterion_verdict"] == "FAIL"
    assert "1.4.6" not in {r["criterion"] for r in v["criteria"] if r["in_scope"]}
    assert v["criterion_verdict_counts"].get("FAIL") == 2      # not 3 any more


def test_f_derivation_reads_the_catalogue_level(session):
    from primeqa.knowledge.standard_derivation import derive_candidates
    d = derive_candidates(session, "EN301549")
    assert {c["provenance"]["level_source"] for c in d["candidates"]} == {"catalogue"}
    gated = [o for o in d["out_of_scope"] if o.get("criterion") == "2.1.3"]
    assert gated and gated[0]["level"] == "AAA" and gated[0]["level_source"] == "catalogue"


def test_g_ingest_refusals_and_the_withdraw_gate(session):
    from primeqa.knowledge import criterion_catalogue as CC
    from primeqa.knowledge import rule_lifecycle as lc
    active = session.execute(text(
        "SELECT id FROM s5_standard_map_sets WHERE standard='WCAG22' AND state='ACTIVE'")).scalar_one()
    with pytest.raises(CC.CatalogueIngestError, match="requires the set in DRAFT"):
        CC.ingest_catalogue(session, standard="WCAG22", map_set_id=active)
    session.rollback()
    with pytest.raises(lc.LifecycleError, match="requires the SET in DRAFT"):
        lc.remove_standard_map(session, map_set_id=active, rule_id="PLM-A11Y-001",
                               version=1, criterion="2.4.4", reason="probe", **SUPER)
    session.rollback()
    ms = lc.create_map_set(session, standard="SECTION508",
                           standard_version=f"508 probe {uuid.uuid4().hex[:6]}",
                           revision=1, provenance={"probe": True},
                           notes="ingest probe", **SUPER)
    with pytest.raises(CC.CatalogueIngestError, match="is for SECTION508, not WCAG22"):
        CC.ingest_catalogue(session, standard="WCAG22", map_set_id=ms)
    session.rollback()
    with pytest.raises(lc.LifecycleError, match="refusing to approve an EMPTY"):
        lc.transition_map_set(session, map_set_id=ms, to_state="REVIEW", **SUPER)
        lc.transition_map_set(session, map_set_id=ms, to_state="APPROVED", **SUPER)
    session.rollback()
