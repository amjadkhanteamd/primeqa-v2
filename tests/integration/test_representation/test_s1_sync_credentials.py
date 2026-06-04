"""Integration: S1-sync credential resolution + connected_org provisioning (D-150).

`ensure_connected_org_for_environment` (governance DB, idempotent on
environment_id) + `resolve_sync_sf_client` (the v1 environment → connection →
`_oauth_token` bridge, mocked) + the `access_token`-seeded `SalesforceClient`.
Cutover Step 0 / S0.1. Uses this package's per-test transactional `session`
fixture (tenant-schema-scoped, rolls back — no cleanup); the `20260604_0010`
migration applies via the autouse `alembic upgrade head` setup.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import text

from primeqa.integrations.sf_client import SalesforceClient
from primeqa.sync.credentials import (
    CredentialResolutionError,
    ensure_connected_org_for_environment,
    resolve_sync_sf_client,
)


# --- provisioning (governance DB; the session rolls back) ------------------

def test_ensure_connected_org_idempotent(session):
    first = ensure_connected_org_for_environment(
        session, 999001, "https://x.my.salesforce.com")
    second = ensure_connected_org_for_environment(   # re-provision → same row
        session, 999001, "https://x.my.salesforce.com")
    assert first == second
    n = session.execute(text(
        "SELECT count(*) FROM connected_orgs WHERE environment_id = 999001")).scalar()
    assert n == 1


def test_ensure_records_environment_link_and_target(session):
    oid = ensure_connected_org_for_environment(
        session, 999002, "https://y.my.salesforce.com")
    row = session.execute(text(
        "SELECT environment_id, sf_instance_url, org_type FROM connected_orgs "
        "WHERE id = :i"), {"i": oid}).one()
    assert row.environment_id == 999002
    assert row.sf_instance_url == "https://y.my.salesforce.com"
    assert row.org_type == "production"


# --- credential resolution (the v1 path, mocked — no DB) -------------------

class _Env:
    def __init__(self, *, connection_id=7, tenant_id=1,
                 sf_instance_url="https://z.my.salesforce.com"):
        self.id = 999003
        self.connection_id = connection_id
        self.tenant_id = tenant_id
        self.sf_instance_url = sf_instance_url


def _db_returning(env):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = env
    return db


def _patch_connection(monkeypatch, cfg):
    class _FakeRepo:
        def __init__(self, _db):
            pass

        def get_connection_decrypted(self, _cid, _tid):
            return {"config": cfg}

    monkeypatch.setattr("primeqa.core.repository.ConnectionRepository", _FakeRepo)


def test_resolve_builds_token_seeded_client(monkeypatch):
    _patch_connection(monkeypatch, {
        "instance_url": "https://z.my.salesforce.com",
        "client_id": "CID", "client_secret": "CSEC",
        "auth_flow": "client_credentials"})
    monkeypatch.setattr("primeqa.metadata.worker_runner._oauth_token",
                        lambda env, cfg: "AT.live")

    client = resolve_sync_sf_client(_db_returning(_Env()), 999003)
    try:
        assert isinstance(client, SalesforceClient)
        assert client._access_token == "AT.live"       # pre-seeded → skips refresh
        assert client.instance_url == "https://z.my.salesforce.com"
        assert client.client_id == "CID"
    finally:
        client.close()


def test_resolve_missing_environment_raises():
    with pytest.raises(CredentialResolutionError, match="not found"):
        resolve_sync_sf_client(_db_returning(None), 999003)


def test_resolve_no_connection_raises():
    with pytest.raises(CredentialResolutionError, match="no Salesforce connection"):
        resolve_sync_sf_client(_db_returning(_Env(connection_id=None)), 999003)


def test_resolve_empty_token_raises(monkeypatch):
    _patch_connection(monkeypatch, {"client_id": "C", "client_secret": "S"})
    monkeypatch.setattr("primeqa.metadata.worker_runner._oauth_token",
                        lambda env, cfg: "")
    with pytest.raises(CredentialResolutionError, match="no access_token"):
        resolve_sync_sf_client(_db_returning(_Env()), 999003)


# --- the access_token param (pure) -----------------------------------------

def test_seeded_client_skips_refresh_default_keeps_it():
    seeded = SalesforceClient("https://x", "id", "sec", "", access_token="AT.seed")
    try:
        assert seeded._access_token == "AT.seed"       # set → _ensure skips refresh
    finally:
        seeded.close()
    default = SalesforceClient("https://x", "id", "sec", "rt")   # backward-compat
    try:
        assert default._access_token is None           # lazy refresh path unchanged
    finally:
        default.close()
