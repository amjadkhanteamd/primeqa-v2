"""per-org Slice 3a — org-resolution units (offline, no DB).

Red-proofs the new resolution logic in isolation:
  * get_connected_org_for_environment: env→org returns it; env→no-org returns
    None; env_id None short-circuits to None (no query).
  * _resolve_run_org (S4 execution path): resolves → returns the id; unresolved →
    FAIL-LOUD (OrgResolutionError) — never a silent org-blind read.
  * _interpret_and_persist (S6): an env with no connected_org → best-effort skip
    (returns None, no crash, no interpret).
"""
import pytest
from unittest.mock import MagicMock

pytestmark = pytest.mark.unit

ORG = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"


class _Scalar:
    def __init__(self, val): self._val = val
    def scalar(self): return self._val


class _Conn:
    """Records the executed SQL + params; returns a fixed scalar."""
    def __init__(self, scalar_val):
        self._val = scalar_val
        self.sql = None
        self.params = None
        self.calls = 0
    def execute(self, stmt, params=None):
        self.calls += 1
        self.sql = str(stmt)
        self.params = params
        return _Scalar(self._val)


class TestHelper:
    def test_env_with_org_returns_it(self):
        from primeqa.sync.credentials import get_connected_org_for_environment
        conn = _Conn(ORG)
        assert get_connected_org_for_environment(conn, 59) == ORG
        assert conn.params == {"eid": 59}
        assert "FROM connected_orgs WHERE environment_id = :eid" in conn.sql

    def test_env_with_no_org_returns_none(self):
        from primeqa.sync.credentials import get_connected_org_for_environment
        assert get_connected_org_for_environment(_Conn(None), 60) is None

    def test_none_env_short_circuits_without_query(self):
        from primeqa.sync.credentials import get_connected_org_for_environment
        conn = _Conn(ORG)
        assert get_connected_org_for_environment(conn, None) is None
        assert conn.calls == 0  # no SQL issued


class _Session:
    def __init__(self, scalar_val): self._conn = _Conn(scalar_val)
    def connection(self): return self._conn


class TestResolveRunOrgFailLoud:
    def test_resolves_to_id(self):
        from primeqa.execution_engine.run import _resolve_run_org
        assert _resolve_run_org(_Session(ORG), 59) == ORG

    def test_unresolved_raises(self):
        from primeqa.execution_engine.run import _resolve_run_org
        from primeqa.execution_engine.errors import OrgResolutionError
        with pytest.raises(OrgResolutionError):
            _resolve_run_org(_Session(None), 60)


class TestS6SkipOnOrgless:
    def test_interpret_skips_when_no_org(self):
        from primeqa.execution_engine.run import _interpret_and_persist
        # evidence stub: only environment_id + run_id are touched before the skip.
        evidence = MagicMock()
        evidence.environment_id = 999
        evidence.run_id = "run-x"
        # session whose org lookup returns None → skip path
        result = _interpret_and_persist(_Session(None), evidence)
        assert result is None
