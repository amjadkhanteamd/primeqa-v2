"""Phase-4 security-core tests (D-245): L1/L4 conformance, the dispatch gate,
and the reaper guard. DB-free — the gate is a pure function; the reaper is
exercised with a fake tenant connection.
"""
from contextlib import contextmanager

import pytest

from primeqa.core.authz import AuthorizationError, Tier
from primeqa.execution_engine.errors import PolicyError
from primeqa.execution_engine import run as run_mod
from primeqa.execution_engine.read_only_client import ReadOnlyClient
from primeqa.execution_engine.tooling_client import ToolingReadClient
from primeqa.execution_engine.data_mutation_client import DataMutationClient

META = run_mod._METADATA_RECIPE_KIND
DATA = run_mod._DATA_RECIPE_KIND


class _Recipe:
    def __init__(self, kind):
        self.recipe_kind = kind


def _gate(kind, policy, prod, tier):
    run_mod._authorize_dispatch(
        _Recipe(kind), execution_policy=policy, is_production=prod, caller_tier=tier)


# --- L1 / L4: no write reachable from the inspection path -------------------

class TestReadOnlyClientConformance:
    def test_tooling_read_client_is_read_only(self):
        assert issubclass(ToolingReadClient, ReadOnlyClient)

    def test_mutation_client_is_not_read_only(self):
        # DataMutationClient lacks query_data + exposes create/update/delete, so
        # passing it to execute_metadata_inspection(client: ReadOnlyClient) is a
        # type error — no write is reachable from the inspection path (L4).
        assert not issubclass(DataMutationClient, ReadOnlyClient)

    def test_executor_param_is_annotated_read_only(self):
        from primeqa.execution_engine.executor import execute_metadata_inspection
        # executor.py uses `from __future__ import annotations`, so the annotation
        # is the string name (lazy). Either form proves the param is gated.
        assert execute_metadata_inspection.__annotations__["client"] == "ReadOnlyClient"


# --- (i) resource policy — env state, applies to everyone -------------------

class TestResourcePolicyGate:
    def test_disabled_rejects_all(self):
        with pytest.raises(PolicyError):
            _gate(DATA, "disabled", False, Tier.ADMIN)
        with pytest.raises(PolicyError):
            _gate(META, "disabled", False, Tier.ADMIN)  # even inspection, even admin

    def test_read_only_rejects_data_even_for_admin(self):
        with pytest.raises(PolicyError):
            _gate(DATA, "read_only", False, Tier.ADMIN)

    def test_read_only_permits_inspection(self):
        _gate(META, "read_only", False, None)  # no raise

    def test_full_permits(self):
        _gate(DATA, "full", False, None)


# --- (ii) production role rule — keyed on read-mode, distinct error ---------

class TestProductionRoleGate:
    def test_member_prod_data_rejected(self):
        with pytest.raises(AuthorizationError):
            _gate(DATA, "full", True, Tier.MEMBER)

    def test_member_prod_inspection_allowed(self):
        _gate(META, "full", True, Tier.MEMBER)  # read-mode → allowed

    def test_admin_prod_data_allowed(self):
        _gate(DATA, "full", True, Tier.ADMIN)

    def test_system_caller_skips_role_rule(self):
        # caller_tier None = trusted/system (async, sandbox-only); the resource
        # policy still applies but the production-role rule is skipped.
        _gate(DATA, "full", True, None)

    def test_errors_are_distinct_types(self):
        assert not issubclass(PolicyError, AuthorizationError)
        assert not issubclass(AuthorizationError, PolicyError)


# --- reaper guard ----------------------------------------------------------

class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    def __init__(self, policy_row):
        self.policy_row = policy_row

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "s4_created_records cr" in sql:  # step 1 — the stranded select
            return _FakeResult([{"id": 1, "sobject": "Account",
                                 "record_id": "001x", "environment_id": 5}])
        if "execution_policy" in sql:       # step 2 — the policy guard read
            return _FakeResult([self.policy_row])
        return _FakeResult([])              # step 3 mark-cleaned etc.


def _reap_with_policy(monkeypatch, policy_row):
    import primeqa.semantic.connection as conn_mod
    from primeqa.execution_engine import stranded_cleanup

    @contextmanager
    def fake_scope(tenant_id):
        yield _FakeConn(policy_row)

    monkeypatch.setattr(conn_mod, "get_tenant_connection", fake_scope)

    calls = {"resolved": 0, "deleted": 0}

    class _Client:
        def delete(self, sobject, record_id):
            calls["deleted"] += 1

    def resolver(session, env_id):
        calls["resolved"] += 1
        return _Client()

    reaped = stranded_cleanup.reap_stranded_records(
        tenant_id=1, stale_minutes=0, client_resolver=resolver)
    return reaped, calls


class TestReaperGuard:
    def test_skips_read_only_env(self, monkeypatch):
        reaped, calls = _reap_with_policy(
            monkeypatch, {"execution_policy": "read_only", "is_production": False})
        assert reaped == 0 and calls["resolved"] == 0 and calls["deleted"] == 0

    def test_skips_disabled_env(self, monkeypatch):
        reaped, calls = _reap_with_policy(
            monkeypatch, {"execution_policy": "disabled", "is_production": False})
        assert reaped == 0 and calls["deleted"] == 0

    def test_skips_production_env(self, monkeypatch):
        reaped, calls = _reap_with_policy(
            monkeypatch, {"execution_policy": "full", "is_production": True})
        assert reaped == 0 and calls["deleted"] == 0

    def test_reaps_full_nonprod_env(self, monkeypatch):
        reaped, calls = _reap_with_policy(
            monkeypatch, {"execution_policy": "full", "is_production": False})
        assert calls["resolved"] == 1 and calls["deleted"] == 1 and reaped == 1
