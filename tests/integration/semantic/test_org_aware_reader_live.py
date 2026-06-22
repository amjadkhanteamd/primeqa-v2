"""per-org Slice 2 (D-256) — SemanticOrgModel org-scoping, behavioral red-proof.

Runs against the local-PG harness (``conftest.py``: a fresh, fully-migrated,
EMPTY tenant_1 with a per-test rolled-back ``conn``). Seeds a synthetic TWO-org
fixture (org A + org B sharing the version timeline AND an sf_id) and proves the
org-aware reader actually discriminates — the thing the SQL-shape unit test
(``tests/unit/semantic/test_org_aware_reader.py``) can only assert structurally.

The migration chain the harness runs includes Slice 1 (``20260622_0020``), so the
``connected_org_id`` column + the re-keyed ``(sf_id, connected_org_id)`` active
index are present — seeding the same sf_id under two orgs as both-active is itself
a live proof of the Slice-1 loosening.
"""
from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from primeqa.semantic.query import SemanticOrgModel

pytestmark = pytest.mark.integration

ORG_A = "aaaaaaaa-0000-4000-8000-000000000001"
ORG_B = "bbbbbbbb-0000-4000-8000-000000000002"


def _org(conn, org_id: str, label: str) -> None:
    conn.execute(text(
        "INSERT INTO connected_orgs (id, org_type, sf_instance_url, label) "
        "VALUES (CAST(:id AS uuid), 'sandbox', :url, :lbl)"
    ), {"id": org_id, "url": f"https://{label}.example.com", "lbl": label})


def _version(conn, org_id: str) -> int:
    return int(conn.execute(text(
        "INSERT INTO logical_versions (version_name, version_type, connected_org_id) "
        "VALUES (:n, 'sync_run', CAST(:o AS uuid)) RETURNING version_seq"
    ), {"n": f"v_{uuid4().hex[:10]}", "o": org_id}).scalar())


def _object(conn, org_id: str, sf_id: str, vfrom: int, *, is_custom: bool,
            api: str = "Account") -> UUID:
    eid = conn.execute(text(
        "INSERT INTO entities "
        "(entity_type, sf_id, sf_api_name, display_name, attributes, "
        " valid_from_seq, valid_to_seq, last_synced_at, connected_org_id) "
        "VALUES ('Object', :sfid, :api, :api, CAST('{}' AS jsonb), "
        " :vf, NULL, NOW(), CAST(:o AS uuid)) RETURNING id"
    ), {"sfid": sf_id, "api": api, "vf": vfrom, "o": org_id}).scalar()
    eid = eid if isinstance(eid, UUID) else UUID(str(eid))
    conn.execute(text(
        "INSERT INTO object_details (entity_id, key_prefix, is_custom) "
        "VALUES (CAST(:e AS uuid), :kp, :ic)"
    ), {"e": str(eid), "kp": "001", "ic": is_custom})
    return eid


@pytest.fixture
def two_orgs(conn):
    """org A: one version (vA) + one Object. org B: two versions (vB1<vB2, so the
    GLOBAL max is B's) + one Object that shares A's sf_id. Both Objects active."""
    _org(conn, ORG_A, "orgA")
    _org(conn, ORG_B, "orgB")
    vA = _version(conn, ORG_A)
    vB1 = _version(conn, ORG_B)
    vB2 = _version(conn, ORG_B)          # global MAX belongs to org B
    assert vA < vB1 < vB2
    objA = _object(conn, ORG_A, "001SHARED0000000AA", vA, is_custom=True)
    objB = _object(conn, ORG_B, "001SHARED0000000AA", vB1, is_custom=False)
    return {"conn": conn, "vA": vA, "vB2": vB2, "objA": objA, "objB": objB}


class TestCurrentVersionDiscrimination:
    def test_org_a_pins_its_own_latest_not_global_max(self, two_orgs):
        c, vA, vB2 = two_orgs["conn"], two_orgs["vA"], two_orgs["vB2"]
        assert SemanticOrgModel(c, ORG_A).current_version_seq() == vA
        assert SemanticOrgModel(c, ORG_B).current_version_seq() == vB2
        # org-blind = the global MAX = org B's vB2 (today's behavior).
        assert SemanticOrgModel(c).current_version_seq() == vB2


class TestGetEntitiesDiscrimination:
    def test_each_org_sees_only_its_own(self, two_orgs):
        c, vB2 = two_orgs["conn"], two_orgs["vB2"]
        a = SemanticOrgModel(c, ORG_A).get_entities("Object", at_seq=vB2)
        b = SemanticOrgModel(c, ORG_B).get_entities("Object", at_seq=vB2)
        assert [e.id for e in a] == [two_orgs["objA"]]
        assert [e.id for e in b] == [two_orgs["objB"]]

    def test_org_blind_returns_the_union(self, two_orgs):
        c, vB2 = two_orgs["conn"], two_orgs["vB2"]
        allrows = SemanticOrgModel(c).get_entities("Object", at_seq=vB2)
        assert {e.id for e in allrows} == {two_orgs["objA"], two_orgs["objB"]}

    def test_same_sf_id_two_orgs_coexist_active(self, two_orgs):
        # The Slice-1 loosening, read back: one sf_id, both orgs, both active.
        c, vB2 = two_orgs["conn"], two_orgs["vB2"]
        by_sfid = SemanticOrgModel(c).get_entities(
            "Object", at_seq=vB2, filters={"sf_id": "001SHARED0000000AA"})
        assert {e.id for e in by_sfid} == {two_orgs["objA"], two_orgs["objB"]}
        only_a = SemanticOrgModel(c, ORG_A).get_entities(
            "Object", at_seq=vB2, filters={"sf_id": "001SHARED0000000AA"})
        assert [e.id for e in only_a] == [two_orgs["objA"]]


class TestDetailDiscrimination:
    def test_detail_under_org_a_returns_a_not_b(self, two_orgs):
        c, vB2 = two_orgs["conn"], two_orgs["vB2"]
        objA, objB = two_orgs["objA"], two_orgs["objB"]
        a_model = SemanticOrgModel(c, ORG_A)
        # A's own entity → its detail (is_custom True for A).
        da = a_model.get_entity_details(objA, at_seq=vB2)
        assert da is not None and da["is_custom"] is True
        # B's entity, asked under org A → None (foreign-org id excluded).
        assert a_model.get_entity_details(objB, at_seq=vB2) is None
        # Under org B, B's entity resolves (is_custom False).
        db = SemanticOrgModel(c, ORG_B).get_entity_details(objB, at_seq=vB2)
        assert db is not None and db["is_custom"] is False
        # Org-blind resolves either (today's behavior).
        assert SemanticOrgModel(c).get_entity_details(objB, at_seq=vB2) is not None
