"""per-org Slice 3a+3b — behavioral red-proof against local PG (two-org fixture).

Runs on the local-PG harness (``conftest.py``: fresh, fully-migrated tenant_1 —
the chain includes Slice 3a's UNIQUE index 20260623_0010 — with a rolled-back
``conn``). Proves:

  * get_connected_org_for_environment resolves env→org, returns None for an
    env with no connected_org, and (with the UNIQUE index live) can never see >1.
  * the UNIQUE index: a second connected_org for the same environment_id violates.
  * 3b build_metadata_s1_reader is org-scoped when given an environment_id (only
    that org's metadata) and org-blind (the union) without one — the wrapper is
    exercised end-to-end by pointing its get_tenant_connection at the test conn.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from primeqa.sync.credentials import get_connected_org_for_environment

pytestmark = pytest.mark.integration

ORG_A = "aaaaaaaa-3a00-4a00-8a00-000000000001"
ORG_B = "bbbbbbbb-3a00-4b00-8b00-000000000002"
ENV_A, ENV_B = 901, 902


def _org(conn, org_id, env_id, label):
    conn.execute(text(
        "INSERT INTO connected_orgs (id, org_type, sf_instance_url, label, environment_id) "
        "VALUES (CAST(:id AS uuid), 'sandbox', :url, :lbl, :eid)"
    ), {"id": org_id, "url": f"https://{label}.example.com", "lbl": label, "eid": env_id})


def _version(conn, org_id):
    return int(conn.execute(text(
        "INSERT INTO logical_versions (version_name, version_type, connected_org_id) "
        "VALUES (:n, 'sync_run', CAST(:o AS uuid)) RETURNING version_seq"
    ), {"n": f"v_{uuid4().hex[:10]}", "o": org_id}).scalar())


def _object(conn, org_id, vfrom, api):
    eid = conn.execute(text(
        "INSERT INTO entities (entity_type, sf_id, sf_api_name, display_name, attributes, "
        " valid_from_seq, valid_to_seq, last_synced_at, connected_org_id) "
        "VALUES ('Object', :sfid, :api, :api, CAST('{}' AS jsonb), :vf, NULL, NOW(), "
        " CAST(:o AS uuid)) RETURNING id"
    ), {"sfid": f"001{api[:6]}", "api": api, "vf": vfrom, "o": org_id}).scalar()
    eid = eid if isinstance(eid, UUID) else UUID(str(eid))
    conn.execute(text(
        "INSERT INTO object_details (entity_id, key_prefix, is_custom) "
        "VALUES (CAST(:e AS uuid), '001', FALSE)"
    ), {"e": str(eid)})
    return eid


@pytest.fixture
def two_orgs(conn):
    _org(conn, ORG_A, ENV_A, "orgA")
    _org(conn, ORG_B, ENV_B, "orgB")
    _object(conn, ORG_A, _version(conn, ORG_A), "AcctA")
    _object(conn, ORG_B, _version(conn, ORG_B), "AcctB")
    return conn


class TestHelperResolution:
    def test_env_resolves_to_its_org(self, two_orgs):
        assert get_connected_org_for_environment(two_orgs, ENV_A) == ORG_A
        assert get_connected_org_for_environment(two_orgs, ENV_B) == ORG_B

    def test_env_with_no_org_returns_none(self, two_orgs):
        assert get_connected_org_for_environment(two_orgs, 999) is None


class TestUniqueIndex:
    def test_second_org_for_same_env_violates(self, two_orgs):
        sp = two_orgs.begin_nested()
        with pytest.raises(IntegrityError):
            _org(two_orgs, str(uuid4()), ENV_A, "orgA-dup")  # same env → UNIQUE violation
        sp.rollback()
        # the original still resolves uniquely
        assert get_connected_org_for_environment(two_orgs, ENV_A) == ORG_A


class TestMetadataReaderOrgScoping:
    def _patch_conn(self, monkeypatch, conn):
        @contextmanager
        def _fake(tenant_id):
            yield conn
        monkeypatch.setattr(
            "primeqa.semantic.connection.get_tenant_connection", _fake)

    def test_env_scopes_to_its_org(self, two_orgs, monkeypatch):
        from primeqa.metadata_bridge.s1_reader import build_metadata_s1_reader
        self._patch_conn(monkeypatch, two_orgs)
        rA = build_metadata_s1_reader(1, environment_id=ENV_A)
        names = {o.api_name for o in rA.get_objects(None)}
        assert names == {"AcctA"}                      # only org A's metadata

    def test_no_env_is_org_blind_union(self, two_orgs, monkeypatch):
        from primeqa.metadata_bridge.s1_reader import build_metadata_s1_reader
        self._patch_conn(monkeypatch, two_orgs)
        r = build_metadata_s1_reader(1)                # no env → org-blind
        names = {o.api_name for o in r.get_objects(None)}
        assert names == {"AcctA", "AcctB"}             # the union (today's behavior)
