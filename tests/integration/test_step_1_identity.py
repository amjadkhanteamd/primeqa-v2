"""Step 1 DB-real acceptance — gated on S3A3_TEST_DATABASE_URL (scratch,
tenant_1 at 20260908_0010; public.requirements at migration 072).

The seven verification items of LLD_STEP_1_PROVENANCE §g, on real rows
through the real paths:

  1. NO RE-KEY — the identity key set equals the tenant's key set before
     the backfill; the script REFUSES to commit on any difference
     (proven with a planted mismatch); every row's ``external_key``
     equals its pre-072 derived key;
  2. origin counts match the dry run, and a second ``--apply`` inserts
     nothing and reports the same counts (idempotent);
  3. a planted duplicate key is REFUSED (the partial UNIQUE index, the
     namespace CHECK, the immutability trigger, the service's friendly
     refusal);
  4. the GAP is visible and the decorate affordance DECORATES — the row
     is created carrying the identity's own key, no new identity;
  5. fixtures/probes are outside the default view's set and inside it
     under the filter, with the right count; gaps are NEVER hidden;
  6. runtime establishment: a link establishes the identity with the
     evidence rule, a declared origin is honoured, an unplaceable key
     lands CANNOT_CLASSIFY, and a purge leaves the identity as a gap.

Every row this suite plants is removed at teardown.
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DB, reason="set S3A3_TEST_DATABASE_URL "
                       "(non-prod scratch, tenant_1 at 20260908_0010 + 072)"),
]
TENANT = 1
MARK = "s1id"          # every planted key carries it, so teardown is exact


def _conn():
    from primeqa.semantic.connection import get_tenant_connection
    return get_tenant_connection(TENANT)


def _pub():
    from primeqa import db as dbm
    return dbm.SessionLocal()


@pytest.fixture(scope="module", autouse=True)
def _bootstrap():
    from primeqa import db as dbm
    if os.environ.get("DATABASE_URL", "") != DB:
        pytest.skip("DATABASE_URL must point at the scratch DB for this suite")
    if getattr(dbm, "engine", None) is None:
        dbm.init_db(DB)
    pub = _pub()
    try:
        sec = pub.execute(text(
            "INSERT INTO sections (tenant_id, name, created_by) "
            "VALUES (:t, :n, 1) RETURNING id"),
            {"t": TENANT, "n": f"{MARK} section"}).scalar()
        pub.commit()
    finally:
        pub.close()
    yield {"section": sec}
    pub = _pub()
    try:
        pub.execute(text("DELETE FROM requirements WHERE section_id = :s"), {"s": sec})
        pub.execute(text("DELETE FROM sections WHERE id = :s"), {"s": sec})
        pub.commit()
    finally:
        pub.close()
    with _conn() as conn:
        conn.execute(text("DELETE FROM requirement_identities "
                          "WHERE external_key LIKE :p"), {"p": f"%{MARK}%"})
        conn.execute(text("DELETE FROM test_requirement_links "
                          "WHERE external_key LIKE :p"), {"p": f"%{MARK}%"})


@pytest.fixture(autouse=True)
def _clean_between(_bootstrap):
    yield
    # Rows created without an explicit key carry the DERIVED key 'req-<id>',
    # which does not contain the marker — collect those before deleting the
    # rows, so no identity of this suite's making survives into the next test.
    pub = _pub()
    try:
        derived = [r[0] for r in pub.execute(text(
            "SELECT external_key FROM requirements WHERE section_id = :s"),
            {"s": _bootstrap["section"]}).all()]
        pub.execute(text("DELETE FROM requirements WHERE section_id = :s"),
                    {"s": _bootstrap["section"]})
        pub.commit()
    finally:
        pub.close()
    with _conn() as conn:
        conn.execute(text("DELETE FROM requirement_identities "
                          "WHERE external_key LIKE :p OR external_key = ANY(:k)"),
                     {"p": f"%{MARK}%", "k": derived})
        conn.execute(text("DELETE FROM test_requirement_links "
                          "WHERE external_key LIKE :p OR external_key = ANY(:k)"),
                     {"p": f"%{MARK}%", "k": derived})


def _key(suffix):
    return f"{MARK}-{suffix}-{uuid4().hex[:6]}"


def _plant_link(conn, key, *, test_id=None):
    """A link row for ``key`` — the substrate's own record that this
    identity exists. A claim is not required for the identity tests."""
    tid = test_id or uuid4()
    conn.execute(text(
        "INSERT INTO test_requirement_links (test_id, external_system, "
        "external_key, link_kind, linked_at, linked_by) "
        "VALUES (CAST(:t AS uuid), 'jira', :k, 'generated_from', now(), 's3') "
        "ON CONFLICT DO NOTHING"), {"t": str(tid), "k": key})
    return tid


def _service(db):
    from primeqa.core.repository import ActivityLogRepository
    from primeqa.test_management.repository import (
        RequirementRepository, SectionRepository,
    )
    from primeqa.test_management.service import TestManagementService
    return TestManagementService(
        section_repo=SectionRepository(db),
        requirement_repo=RequirementRepository(db),
        activity_repo=ActivityLogRepository(db))


def _create_requirement(section, **kw):
    pub = _pub()
    try:
        return _service(pub).create_requirement(
            TENANT, section, kw.pop("source", "manual"), 1, **kw)
    finally:
        pub.close()


def _identity(key):
    with _conn() as conn:
        return conn.execute(text(
            "SELECT external_key, origin, origin_evidence, established_by, "
            "classifier_version FROM requirement_identities "
            "WHERE external_key = :k"), {"k": key}).mappings().first()


# ---- 1. no re-key --------------------------------------------------------

def test_1_backfill_never_re_keys_and_refuses_when_it_would(_bootstrap):
    from primeqa.test_representation import identity_backfill as B
    key = _key("norekey")
    with _conn() as conn:
        _plant_link(conn, key)
    with _conn() as conn:
        before = set(B.link_key_census(conn)) | {
            r["key"] for r in B._live_requirement_rows(conn, TENANT)}
        planned = {i["key"] for i in B.plan(conn, TENANT)["identities"]}
    assert before == planned                      # the set is preserved exactly
    assert key in planned

    # the guard is real: a planned set that drops or invents a key refuses
    with pytest.raises(B.RekeyRefused) as exc:
        B._assert_no_rekey(before, planned - {key})
    assert key in str(exc.value)
    with pytest.raises(B.RekeyRefused):
        B._assert_no_rekey(before, planned | {"invented"})


def test_1b_every_row_key_equals_the_pre_072_derivation(_bootstrap):
    """072 backfilled COALESCE(jira_key, 'req-'||id) — the exact key
    _requirement_to_ref derived before it. Row-for-row on live rows."""
    pub = _pub()
    try:
        rows = pub.execute(text(
            "SELECT id, jira_key, external_key FROM requirements "
            "WHERE tenant_id = :t AND deleted_at IS NULL"), {"t": TENANT}).mappings().all()
    finally:
        pub.close()
    for r in rows:
        assert r["external_key"] == (r["jira_key"] or f"req-{r['id']}"), r["id"]


# ---- 2. counts + idempotence ---------------------------------------------

def test_2_backfill_counts_and_second_apply_is_a_no_op(_bootstrap):
    from primeqa.test_representation import identity_backfill as B
    fixture_key = "REQ-L7D-" + _key("fx")
    plain_key = _key("plain")
    req = _create_requirement(_bootstrap["section"], jira_summary="a manual one")
    with _conn() as conn:
        _plant_link(conn, fixture_key)
        _plant_link(conn, plain_key)

    # the row's identity was ALREADY established by create_requirement (the
    # runtime path) — the backfill covers the two link keys only, and leaves
    # the established one exactly as it was.
    assert _identity(req["external_key"])["established_by"] == "human:1"
    first = B.run(TENANT, apply=True, actor_user_id=1)
    assert first["applied"] is True and first["inserted"] == 2
    assert _identity(fixture_key)["origin"] == "fixture"
    assert _identity(plain_key)["origin"] == "CANNOT_CLASSIFY"
    assert _identity(req["external_key"])["origin"] == "manual"

    second = B.run(TENANT, apply=True, actor_user_id=1)
    assert second["inserted"] == 0                       # idempotent
    assert second["counts"] == first["counts"]
    assert second["identity_rows_after"] == first["identity_rows_after"]


# ---- 3. refusals ---------------------------------------------------------

def test_3_a_duplicate_identity_key_is_refused(_bootstrap):
    from primeqa.shared.api import ValidationError
    key = _key("dupe")
    _create_requirement(_bootstrap["section"], external_key=key, jira_summary="first")
    with pytest.raises(ValidationError) as exc:
        _create_requirement(_bootstrap["section"], external_key=key, jira_summary="second")
    assert key in str(exc.value)


def test_3b_the_database_refuses_a_duplicate_even_without_the_service(_bootstrap):
    from sqlalchemy.exc import IntegrityError
    key = _key("dbdupe")
    r = _create_requirement(_bootstrap["section"], external_key=key, jira_summary="first")
    pub = _pub()
    try:
        with pytest.raises(IntegrityError):
            pub.execute(text(
                "INSERT INTO requirements (tenant_id, section_id, source, "
                "external_key, jira_summary, created_by) "
                "VALUES (:t, :s, 'manual', :k, 'clash', 1)"),
                {"t": TENANT, "s": _bootstrap["section"], "k": key})
            pub.commit()
    finally:
        pub.rollback(); pub.close()
    assert r["external_key"] == key


def test_3c_a_typed_key_cannot_spoof_another_rows_req_namespace(_bootstrap):
    from sqlalchemy.exc import IntegrityError
    pub = _pub()
    try:
        with pytest.raises(IntegrityError):
            pub.execute(text(
                "INSERT INTO requirements (tenant_id, section_id, source, "
                "external_key, jira_summary, created_by) "
                "VALUES (:t, :s, 'manual', 'req-999999', 'spoof', 1)"),
                {"t": TENANT, "s": _bootstrap["section"]})
            pub.commit()
    finally:
        pub.rollback(); pub.close()


def test_3d_an_established_identity_cannot_be_re_keyed(_bootstrap):
    from sqlalchemy.exc import DatabaseError
    from primeqa.test_representation.identity import establish
    key = _key("immutable")
    with _conn() as conn:
        establish(conn, key, origin="manual", evidence={"rule": "none"},
                  established_by="test")
    with pytest.raises(DatabaseError) as exc:
        with _conn() as conn:
            conn.execute(text("UPDATE requirement_identities SET external_key = :n "
                              "WHERE external_key = :k"),
                         {"n": key + "-moved", "k": key})
    assert "immutable" in str(exc.value).lower()


# ---- 4. the gap and its decoration ---------------------------------------

def test_4_a_gap_is_reported_and_decorating_it_mints_no_key(_bootstrap):
    from primeqa.intelligence.requirement_identity_console import (
        decorate_identity, identity_overview,
    )
    key = _key("gap")
    with _conn() as conn:
        _plant_link(conn, key)
    before = identity_overview(TENANT)
    assert before["available"] is True
    assert key in [g["key"] for g in before["gaps"]]

    pub = _pub()
    try:
        res = decorate_identity(pub, TENANT, key,
                                section_id=_bootstrap["section"],
                                summary="now it has a record", created_by=1)
    finally:
        pub.close()
    assert res["ok"] is True and res["external_key"] == key

    after = identity_overview(TENANT)
    assert key not in [g["key"] for g in after["gaps"]]       # the gap closed
    assert len(after["origins"]) == len(before["origins"])    # no key minted
    pub = _pub()
    try:
        row = pub.execute(text(
            "SELECT id, external_key, jira_key, source FROM requirements "
            "WHERE id = :i"), {"i": res["requirement_id"]}).mappings().first()
    finally:
        pub.close()
    assert row["external_key"] == key                # the row carries the key
    assert row["jira_key"] is None and row["source"] == "manual"


# ---- 5. the default view's set -------------------------------------------

def test_5_fixtures_are_outside_the_default_set_and_gaps_never_are(_bootstrap):
    from primeqa.intelligence.requirement_identity_console import identity_overview
    from primeqa.test_representation import identity_backfill as B
    from primeqa.test_representation.identity import HIDDEN_BY_DEFAULT
    fixture_key = "REQ-ARC-" + _key("hid")
    gap_key = _key("visiblegap")
    with _conn() as conn:
        _plant_link(conn, fixture_key)
        _plant_link(conn, gap_key)
    B.run(TENANT, apply=True, actor_user_id=1)

    ov = identity_overview(TENANT)
    assert ov["origins"][fixture_key] == "fixture"
    assert fixture_key in ov["hidden_keys"]
    assert ov["hidden_count"] == len(ov["hidden_keys"]) >= 1
    # a gap on a SHOWN origin is never hidden — it is a defect to see
    assert gap_key not in ov["hidden_keys"]
    assert gap_key in [g["key"] for g in ov["gaps"]]
    assert ov["origins"][gap_key] not in HIDDEN_BY_DEFAULT
    # the CONSOLE reports every gap, including the fixture's — a fixture
    # having no record is its normal state; the VIEW applies the default
    assert fixture_key in [g["key"] for g in ov["gaps"]]


# ---- 6. runtime establishment --------------------------------------------

def test_6_a_link_establishes_the_identity_with_its_evidence(_bootstrap):
    from primeqa.test_representation.identity import establish_for_key
    key = _key("runtime")
    with _conn() as conn:
        assert establish_for_key(conn, key, established_by="s3") is True
    rec = _identity(key)
    assert rec["origin"] == "CANNOT_CLASSIFY"          # unplaceable, loudly
    assert rec["origin_evidence"] == {"rule": "none"}
    assert rec["established_by"] == "s3"
    assert rec["classifier_version"] == "origin@v1"
    # insert-or-leave-alone: a second sighting never re-classifies
    with _conn() as conn:
        assert establish_for_key(conn, key, established_by="s3",
                                 declared_origin="fixture") is False
    assert _identity(key)["origin"] == "CANNOT_CLASSIFY"


def test_6b_a_declared_origin_is_honoured_and_recorded_as_declared(_bootstrap):
    from primeqa.test_representation.identity import establish_for_key
    key = _key("declared")
    with _conn() as conn:
        establish_for_key(conn, key, established_by="s3", declared_origin="probe")
    rec = _identity(key)
    assert rec["origin"] == "probe"
    assert rec["origin_evidence"] == {"rule": "declared", "by": "s3"}


def test_6c_purging_the_row_leaves_the_identity_as_a_gap(_bootstrap):
    from primeqa.intelligence.requirement_identity_console import identity_overview
    key = _key("purged")
    req = _create_requirement(_bootstrap["section"], external_key=key,
                              jira_summary="about to be purged")
    with _conn() as conn:
        _plant_link(conn, key)
    assert _identity(key)["origin"] == "manual"

    pub = _pub()
    try:
        _service(pub).purge_requirement(req["id"], TENANT, 1)
    finally:
        pub.close()
    ov = identity_overview(TENANT)
    assert key in [g["key"] for g in ov["gaps"]]     # the identity survives
    assert _identity(key) is not None               # and keeps its origin
