"""3A-3 DB-real tests — gated on S3A3_TEST_DATABASE_URL (a NON-production
scratch DB with public 001+062+063+064 applied, the S5 seed loaded, and
tenant_1's alembic chain at 20260825_0020). Skipped entirely otherwise;
the merge gate is tests/unit/test_representation/test_3a3_enumeration.py.

The verification-transcript flows (b)/(c)/(d)/(e) of the 3A-3 GO live
here so they stay re-runnable.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DB, reason="set S3A3_TEST_DATABASE_URL "
                       "(non-prod scratch, tenant_1 at 20260825_0020)"),
]

TENANT_ID = 1
USER_ID = 7
RELEASE = int(os.environ.get("S3A3_RELEASE_ID", "2"))


@pytest.fixture()
def session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    eng = create_engine(DB, pool_pre_ping=True)
    s = Session(bind=eng)
    s.execute(text("SET search_path TO tenant_1, public"))
    yield s
    s.rollback()
    s.close()


def _seed_inventory(session):
    from primeqa.test_representation.claim_sets import (
        create_inventory_version)
    return create_inventory_version(session, members=[
        {"site": "portal.example.com", "path": "/s/home",
         "persona_scope": "customer", "display_name": "Portal home"},
        {"site": "portal.example.com", "path": "/s/cases",
         "persona_scope": "customer", "auth_required": True},
        {"site": "portal.example.com", "path": "/s/home",
         "persona_scope": "guest"},
    ], created_by=USER_ID, notes="3A-3 verification inventory")


def _release_size(session):
    return session.execute(text(
        "SELECT COUNT(*) FROM s5_catalogue_release_members "
        "WHERE release_id = :r"), {"r": RELEASE}).scalar_one()


def test_b_cross_product_idempotence_and_hash_stability(session):
    from primeqa.generation.enumeration import enumerate_claims

    inv = _seed_inventory(session)
    n_rules = _release_size(session)

    r1 = enumerate_claims(
        session, catalogue_release_id=RELEASE, inventory_version=inv,
        persona_scope="customer", created_by=USER_ID)
    assert r1["members"] == n_rules * 2          # 2 customer surfaces
    assert r1["created"] == n_rules * 2
    assert r1["existing"] == 0

    r2 = enumerate_claims(
        session, catalogue_release_id=RELEASE, inventory_version=inv,
        persona_scope="customer", created_by=USER_ID)
    assert r2["created"] == 0                    # the re-run no-ops
    assert r2["existing"] == n_rules * 2

    # identity hashes stable: the two sets reference the SAME test ids
    ids = lambda sid: {r[0] for r in session.execute(text(
        "SELECT test_id FROM claim_set_members WHERE claim_set_id = :s"),
        {"s": str(sid)}).fetchall()}
    assert ids(r1["claim_set_id"]) == ids(r2["claim_set_id"])

    r3 = enumerate_claims(
        session, catalogue_release_id=RELEASE, inventory_version=inv,
        persona_scope="guest", created_by=USER_ID)
    assert r3["members"] == n_rules              # 1 guest surface
    assert r3["created"] == n_rules              # distinct persona = new ids


def test_b_all_four_refusals(session):
    from primeqa.generation.enumeration import (
        EnumerationRefusal, enumerate_claims)

    inv = _seed_inventory(session)

    with pytest.raises(EnumerationRefusal, match="unpinned release"):
        enumerate_claims(session, catalogue_release_id=999999,
                         inventory_version=inv, persona_scope="customer",
                         created_by=USER_ID)

    with pytest.raises(EnumerationRefusal, match="surface outside inventory"):
        enumerate_claims(session, catalogue_release_id=RELEASE,
                         inventory_version=inv, persona_scope="customer",
                         created_by=USER_ID,
                         surface_keys=["nowhere.example.com|/x|customer|-|-"])

    with pytest.raises(EnumerationRefusal, match="empty cross product"):
        enumerate_claims(session, catalogue_release_id=RELEASE,
                         inventory_version=inv, persona_scope="nobody",
                         created_by=USER_ID)

    # stale release: retire one member rule's ACTIVE version out from
    # under the release pin, on a SAVEPOINT so the catalogue heals.
    rule_id = session.execute(text(
        "SELECT rule_id FROM s5_catalogue_release_members "
        "WHERE release_id = :r ORDER BY rule_id LIMIT 1"),
        {"r": RELEASE}).scalar_one()
    nested = session.begin_nested()
    session.execute(text(
        "UPDATE s5_rule_versions SET state = 'RETIRED' "
        "WHERE rule_id = :rid AND state = 'ACTIVE'"), {"rid": rule_id})
    with pytest.raises(EnumerationRefusal,
                       match="cut a new catalogue release"):
        enumerate_claims(session, catalogue_release_id=RELEASE,
                         inventory_version=inv, persona_scope="customer",
                         created_by=USER_ID)
    nested.rollback()


def test_cd_applicability_rows_approval_attribution_revocation(session):
    from primeqa.generation.enumeration import enumerate_claims
    from primeqa.test_representation.claim_sets import (
        ClaimSetError, approve_claim_set, revoke_member)

    inv = _seed_inventory(session)
    res = enumerate_claims(
        session, catalogue_release_id=RELEASE, inventory_version=inv,
        persona_scope="guest", created_by=USER_ID)
    sid = res["claim_set_id"]

    # (c) member rows carry the applicability snapshot
    rows = session.execute(text(
        "SELECT applicability, executable, COUNT(*) FROM claim_set_members "
        "WHERE claim_set_id = :s GROUP BY 1, 2"), {"s": str(sid)}).fetchall()
    counts = {(r[0], r[1]): r[2] for r in rows}
    assert sum(counts.values()) == res["members"]

    # (d) ONE act; event_data carries user_id + claim_set_id; activity_log
    out = approve_claim_set(session, claim_set_id=sid, user_id=USER_ID,
                            tenant_id=TENANT_ID)
    assert out["claims_promoted"] == res["members"]
    ev = session.execute(text("""
        SELECT event_data->>'user_id', event_data->>'claim_set_id',
               event_actor
        FROM test_provenance WHERE event_kind = 'claim_approved'
          AND event_data->>'claim_set_id' = :s LIMIT 1
    """), {"s": str(sid)}).fetchone()
    assert ev == (str(USER_ID), str(sid), "human")
    act = session.execute(text("""
        SELECT user_id, details->>'claim_set_id' FROM public.activity_log
        WHERE action = 's2.claim_set.approve'
          AND details->>'claim_set_id' = :s
    """), {"s": str(sid)}).fetchone()
    assert act == (USER_ID, str(sid))

    # approve twice → REFUSED with the recorded approver
    with pytest.raises(ClaimSetError, match="already approved by user"):
        approve_claim_set(session, claim_set_id=sid, user_id=USER_ID + 1,
                          tenant_id=TENANT_ID)

    # revocation leaves the set intact
    victim = session.execute(text(
        "SELECT test_id FROM claim_set_members WHERE claim_set_id = :s "
        "ORDER BY test_id LIMIT 1"), {"s": str(sid)}).scalar_one()
    revoke_member(session, claim_set_id=sid, test_id=uuid.UUID(str(victim)),
                  user_id=USER_ID, reason="3A-3 verification revocation")
    status, revoked = session.execute(text("""
        SELECT cs.status,
               (SELECT COUNT(*) FROM claim_set_members m
                WHERE m.claim_set_id = cs.id AND m.revoked_at IS NOT NULL)
        FROM claim_sets cs WHERE cs.id = :s"""), {"s": str(sid)}).fetchone()
    assert (status, revoked) == ("approved", 1)


def test_e_default_none_promote_is_byte_identical(session):
    """(e) A promote WITHOUT event_context writes exactly the four
    pre-3A-3 event_data keys."""
    from primeqa.generation.enumeration import enumerate_claims
    from primeqa.test_representation.coordinator import (
        SemanticTransactionCoordinator)

    inv = _seed_inventory(session)
    res = enumerate_claims(
        session, catalogue_release_id=RELEASE, inventory_version=inv,
        persona_scope="customer", created_by=USER_ID)
    tid = session.execute(text(
        "SELECT test_id FROM claim_set_members WHERE claim_set_id = :s "
        "ORDER BY test_id LIMIT 1"),
        {"s": str(res["claim_set_id"])}).scalar_one()
    coord = SemanticTransactionCoordinator()
    claim = coord.get_latest_claim(session, uuid.UUID(str(tid)))
    coord.promote_claim_to_approved(
        session, actor="human", test_id=uuid.UUID(str(tid)),
        version_seq=claim.version_seq)
    keys = session.execute(text("""
        SELECT event_data FROM test_provenance
        WHERE claim_test_id = :t AND event_kind = 'claim_approved'
        ORDER BY event_at DESC LIMIT 1"""),
        {"t": str(tid)}).scalar_one()
    assert set(keys.keys()) == {"actor", "version_seq", "prior_status",
                                "new_status"}
