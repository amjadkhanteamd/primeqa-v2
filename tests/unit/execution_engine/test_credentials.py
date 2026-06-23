"""Unit tests for resolve_tooling_client wiring (D-108.1) — mocked, no DB / no org.

Credential resolution is reused v1 plumbing (env -> connection -> `_oauth_token`);
these tests verify the wiring + every binding-failure branch without a real DB,
by faking the session/query and monkeypatching the reused pieces.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from primeqa.execution_engine.credentials import (
    _resolve_instance_url, resolve_data_mutation_client, resolve_tooling_client,
)
from primeqa.execution_engine.data_mutation_client import DataMutationClient
from primeqa.execution_engine.errors import CredentialResolutionError
from primeqa.execution_engine.tooling_client import ToolingReadClient


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._result


class _FakeDB:
    """Returns a fixed Environment row for any .query(...).filter(...).first()."""

    def __init__(self, env):
        self._env = env

    def query(self, _model):
        return _FakeQuery(self._env)


def _env(connection_id=5):
    return SimpleNamespace(
        id=1, connection_id=connection_id, tenant_id=1,
        sf_instance_url="https://acme.my.salesforce.com", sf_api_version="60.0")


def _patch_conn_and_oauth(monkeypatch, *, conn, token="tok"):
    class _FakeConnRepo:
        def __init__(self, db):
            pass

        def get_connection_decrypted(self, connection_id, tenant_id):
            return conn

    monkeypatch.setattr(
        "primeqa.core.repository.ConnectionRepository", _FakeConnRepo)
    monkeypatch.setattr(
        "primeqa.metadata.worker_runner._oauth_token", lambda env, cfg: token)


def test_resolves_to_tooling_client(monkeypatch):
    _patch_conn_and_oauth(monkeypatch, conn={"config": {"client_id": "c"}}, token="tok-xyz")
    client = resolve_tooling_client(_FakeDB(_env()), environment_id=1)
    assert isinstance(client, ToolingReadClient)


def test_missing_environment_raises():
    with pytest.raises(CredentialResolutionError, match="not found"):
        resolve_tooling_client(_FakeDB(None), environment_id=99)


def test_environment_without_connection_raises():
    with pytest.raises(CredentialResolutionError, match="no Salesforce connection"):
        resolve_tooling_client(_FakeDB(_env(connection_id=None)), environment_id=1)


def test_undecryptable_connection_raises(monkeypatch):
    _patch_conn_and_oauth(monkeypatch, conn=None)
    with pytest.raises(CredentialResolutionError, match="connection"):
        resolve_tooling_client(_FakeDB(_env()), environment_id=1)


def test_empty_token_raises(monkeypatch):
    _patch_conn_and_oauth(monkeypatch, conn={"config": {}}, token="")
    with pytest.raises(CredentialResolutionError, match="access_token"):
        resolve_tooling_client(_FakeDB(_env()), environment_id=1)


# --- task_247242c3: instance resolution mirrors sync (connection-authoritative) ---

def test_resolve_instance_url_connection_wins():
    """The connection's instance_url (the My Domain the token is minted against)
    WINS over the env's display URL."""
    env = SimpleNamespace(sf_instance_url="https://acme.lightning.force.com")
    cfg = {"instance_url": "https://acme.my.salesforce.com"}
    assert _resolve_instance_url(cfg, env) == "https://acme.my.salesforce.com"


def test_resolve_instance_url_falls_back_to_env_when_cfg_absent():
    env = SimpleNamespace(sf_instance_url="https://acme.my.salesforce.com")
    assert _resolve_instance_url({}, env) == "https://acme.my.salesforce.com"


def test_resolve_instance_url_falls_back_when_cfg_empty():
    env = SimpleNamespace(sf_instance_url="https://acme.my.salesforce.com")
    assert _resolve_instance_url({"instance_url": ""}, env) == \
        "https://acme.my.salesforce.com"


def test_resolve_instance_url_env59_shaped_stays_my_domain():
    """env-59 safety: a valid My Domain on BOTH cfg and env → a My Domain resolves
    either way (cfg present OR fallback). The fix must not turn env-59's valid
    resolution into anything invalid (its runs pass today)."""
    env = SimpleNamespace(
        sf_instance_url="https://x--primeqa.sandbox.my.salesforce.com/")
    assert ".my.salesforce.com" in _resolve_instance_url(
        {"instance_url": "https://x.my.salesforce.com"}, env)
    assert ".my.salesforce.com" in _resolve_instance_url({}, env)   # fallback leg


def test_tooling_client_uses_connection_instance_url(monkeypatch):
    """Wiring: with cfg.instance_url present, the built client targets the
    connection's My Domain — NOT the env's sf_instance_url."""
    _patch_conn_and_oauth(
        monkeypatch,
        conn={"config": {"instance_url": "https://conn.my.salesforce.com"}})
    client = resolve_tooling_client(_FakeDB(_env()), environment_id=1)
    assert "conn.my.salesforce.com" in client._base   # cfg wins over env (acme...)


def test_tooling_client_falls_back_to_env_instance_url(monkeypatch):
    """Wiring: cfg without instance_url → the client falls back to the env URL
    (backward-compatible with the pre-fix behavior)."""
    _patch_conn_and_oauth(monkeypatch, conn={"config": {"client_id": "c"}})
    client = resolve_tooling_client(_FakeDB(_env()), environment_id=1)
    assert "acme.my.salesforce.com" in client._base


def test_data_mutation_client_lightning_env_overridden_by_my_domain(monkeypatch):
    """The live 401 repro (env 78): the env URL is the .lightning UI host (a
    non-API host); the connection's My Domain wins so the data-mutation client
    targets the API host → no INVALID_SESSION_ID 401."""
    env = SimpleNamespace(
        id=1, connection_id=5, tenant_id=1,
        sf_instance_url="https://acme.lightning.force.com", sf_api_version="60.0")
    _patch_conn_and_oauth(
        monkeypatch,
        conn={"config": {"instance_url": "https://acme.my.salesforce.com"}})
    client = resolve_data_mutation_client(_FakeDB(env), environment_id=1)
    assert isinstance(client, DataMutationClient)
    assert "acme.my.salesforce.com" in client._base
    assert "lightning.force.com" not in client._base
