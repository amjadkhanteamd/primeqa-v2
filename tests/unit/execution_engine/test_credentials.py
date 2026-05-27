"""Unit tests for resolve_tooling_client wiring (D-108.1) — mocked, no DB / no org.

Credential resolution is reused v1 plumbing (env -> connection -> `_oauth_token`);
these tests verify the wiring + every binding-failure branch without a real DB,
by faking the session/query and monkeypatching the reused pieces.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from primeqa.execution_engine.credentials import resolve_tooling_client
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
