"""3f Slice 1 — the multi-org fail-loud guardrail on org-blind grounding.

Red-proofs BOTH directions of ``recompute_tenant_grounding`` without a DB:
  - >1 org with a model → REFUSE: clears the blend rows + returns 0, and never
    builds the org-blind ``SemanticOrgModel`` / recomputes.
  - exactly 1 (or 0) org → PROCEED: builds the model exactly as before, does NOT
    clear any rows.
Plus the two helpers (org count + the invalidation DELETE).
"""
import contextlib

import pytest

pytestmark = pytest.mark.unit

import primeqa.semantic.connection as _conn_mod
import primeqa.semantic.query as _query_mod
from primeqa.evolution.recompute import (
    _count_orgs_with_model, _invalidate_blend_grounding, recompute_tenant_grounding,
)


class _Result:
    def __init__(self, scalar=None, rowcount=0):
        self._scalar = scalar
        self.rowcount = rowcount

    def scalar(self):
        return self._scalar


class _FakeConn:
    """Records executed SQL; answers the org-count + DELETE the guardrail issues."""

    def __init__(self, org_count):
        self.org_count = org_count
        self.executed: list[str] = []

    def execute(self, stmt, *a, **k):
        sql = str(stmt)
        self.executed.append(sql)
        if "COUNT(DISTINCT connected_org_id)" in sql:
            return _Result(scalar=self.org_count)
        if "DELETE FROM s8_grounding_validity" in sql:
            return _Result(rowcount=99)
        return _Result()

    def deleted(self) -> bool:
        return any("DELETE FROM s8_grounding_validity" in s for s in self.executed)

    def counted_version(self) -> bool:  # proxy for "the model was built + read"
        return any("MAX(version_seq)" in s for s in self.executed)


# --- helpers -----------------------------------------------------------------

def test_count_orgs_with_model_coerces():
    assert _count_orgs_with_model(_FakeConn(2)) == 2
    assert _count_orgs_with_model(_FakeConn(1)) == 1
    assert _count_orgs_with_model(_FakeConn(None)) == 0   # NULL → 0


def test_invalidate_returns_rowcount():
    assert _invalidate_blend_grounding(_FakeConn(2)) == 99


# --- the guardrail, both directions ------------------------------------------

def _patch_conn(monkeypatch, conn):
    @contextlib.contextmanager
    def _fake_get(_tenant_id):
        yield conn
    monkeypatch.setattr(_conn_mod, "get_tenant_connection", _fake_get)


def test_guardrail_refuses_and_clears_on_multi_org(monkeypatch):
    conn = _FakeConn(org_count=2)
    _patch_conn(monkeypatch, conn)
    # If the guardrail were bypassed, SemanticOrgModel would be built — make that
    # explode so the test fails loudly if the refusal doesn't short-circuit.
    def _boom(*a, **k):
        raise AssertionError("SemanticOrgModel must NOT be built on a multi-org tenant")
    monkeypatch.setattr(_query_mod, "SemanticOrgModel", _boom)

    out = recompute_tenant_grounding(1)

    assert out == 0                 # refused → 0 grounded
    assert conn.deleted()           # the blend rows were cleared
    assert not conn.counted_version()  # the org-blind model was never read


def test_guardrail_proceeds_on_single_org(monkeypatch):
    conn = _FakeConn(org_count=1)
    _patch_conn(monkeypatch, conn)

    built = {"n": 0}

    class _Model:
        def __init__(self, _conn):
            built["n"] += 1

        def current_version_seq(self):
            # Short-circuit the rest of the (DB-bound) recompute cleanly via the
            # function's own VersionNotFoundError path — we only need to prove the
            # guardrail let it PROCEED to build the model.
            raise _query_mod.VersionNotFoundError("no version (test)")

    monkeypatch.setattr(_query_mod, "SemanticOrgModel", _Model)

    out = recompute_tenant_grounding(1)

    assert out == 0                 # no versions → 0, same as before
    assert built["n"] == 1          # the model WAS built → guardrail let it proceed
    assert not conn.deleted()       # single-org tenant's rows are NOT cleared


def test_guardrail_proceeds_on_zero_org(monkeypatch):
    conn = _FakeConn(org_count=0)
    _patch_conn(monkeypatch, conn)

    class _Model:
        def __init__(self, _conn):
            pass

        def current_version_seq(self):
            raise _query_mod.VersionNotFoundError("no version (test)")

    monkeypatch.setattr(_query_mod, "SemanticOrgModel", _Model)
    assert recompute_tenant_grounding(1) == 0
    assert not conn.deleted()       # 0 orgs is not >1 → no clear
