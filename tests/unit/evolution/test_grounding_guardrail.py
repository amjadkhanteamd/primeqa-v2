"""3f Slices 2-3 — the per-org grounding recompute loop (replaces the Slice-1
fail-loud guardrail these tests used to red-proof: multi-org tenants are now
GROUNDED per org, not refused).

Red-proofs ``recompute_tenant_grounding`` without a DB:
  - 2 orgs with a model → TWO org-bound ``SemanticOrgModel``s built (one per
    org), each read at its own seq; the per-org recompute core runs per org.
  - departed-org rows pruned (the targeted DELETE), never the blanket clear.
  - 0 orgs → everything cleared (org-less rows are the blend D-265 refused),
    nothing grounded, no model built.
  - an org whose versions are gone (VersionNotFoundError) is skipped; the
    other org still grounds.
"""
import contextlib

import pytest

pytestmark = pytest.mark.unit

import primeqa.evolution.recompute as _recompute_mod
import primeqa.semantic.connection as _conn_mod
import primeqa.semantic.query as _query_mod
from primeqa.evolution.recompute import (
    RecomputeResult,
    _orgs_with_model,
    _prune_departed_org_grounding,
    recompute_tenant_grounding,
)

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "22222222-2222-2222-2222-222222222222"


class _Result:
    def __init__(self, rows=(), rowcount=0):
        self._rows = rows
        self.rowcount = rowcount

    def __iter__(self):
        return iter(self._rows)


class _FakeConn:
    """Records executed SQL; answers the orgs-with-model read + the prune."""

    def __init__(self, orgs):
        self.orgs = list(orgs)
        self.executed: list[tuple[str, object]] = []

    def execute(self, stmt, *a, **k):
        sql = str(stmt)
        params = a[0] if a else k or None
        self.executed.append((sql, params))
        if "SELECT DISTINCT CAST(connected_org_id AS text)" in sql:
            return _Result(rows=[(o,) for o in self.orgs])
        return _Result(rowcount=3)

    def pruned_departed(self) -> bool:
        return any("!= ALL(:orgs)" in s for s, _ in self.executed)

    def cleared_all(self) -> bool:
        return any(s.strip().startswith("DELETE FROM s8_grounding_validity")
                   and "ALL" not in s for s, _ in self.executed)


def _patch_conn(monkeypatch, conn):
    @contextlib.contextmanager
    def _fake_get(_tenant_id):
        yield conn
    monkeypatch.setattr(_conn_mod, "get_tenant_connection", _fake_get)


def _patch_core(monkeypatch, *, grounded_per_org=2):
    """Stub the DB-bound pieces below the loop: artifact load, the S1 reader,
    the ORM Session, and the recompute core (recording each org it ran for)."""
    calls = {"orgs": [], "seqs": []}
    monkeypatch.setattr(_recompute_mod, "load_current_artifacts", lambda s: ["ref"])
    monkeypatch.setattr(_recompute_mod, "S8S1Reader",
                        lambda model, at_seq: ("s1", at_seq))

    def _core(session, refs, *, s1, at_seq, connected_org_id, cap):
        calls["orgs"].append(connected_org_id)
        calls["seqs"].append(at_seq)
        return RecomputeResult(grounded_per_org, 0, 0)
    monkeypatch.setattr(_recompute_mod, "recompute_grounding", _core)

    import sqlalchemy.orm as _orm
    monkeypatch.setattr(_orm, "Session", lambda bind=None: object())
    return calls


# --- helpers -----------------------------------------------------------------

def test_orgs_with_model_reads_distinct_orgs():
    assert _orgs_with_model(_FakeConn([ORG_A, ORG_B])) == [ORG_A, ORG_B]
    assert _orgs_with_model(_FakeConn([])) == []


def test_prune_departed_targets_only_missing_orgs():
    conn = _FakeConn([ORG_A])
    assert _prune_departed_org_grounding(conn, [ORG_A]) == 3
    assert conn.pruned_departed() and not conn.cleared_all()


def test_prune_with_no_orgs_clears_everything():
    conn = _FakeConn([])
    assert _prune_departed_org_grounding(conn, []) == 3
    assert conn.cleared_all()


# --- the per-org loop ----------------------------------------------------------

def test_two_orgs_ground_once_each_with_org_bound_models(monkeypatch):
    conn = _FakeConn([ORG_A, ORG_B])
    _patch_conn(monkeypatch, conn)
    calls = _patch_core(monkeypatch)

    built = []

    class _Model:
        def __init__(self, _conn, connected_org_id=None):
            self.org = connected_org_id
            built.append(connected_org_id)

        def current_version_seq(self):
            return {ORG_A: 10, ORG_B: 20}[self.org]

    monkeypatch.setattr(_query_mod, "SemanticOrgModel", _Model)

    out = recompute_tenant_grounding(1)

    assert out == 4                            # 2 grounded per org × 2 orgs
    assert built == [ORG_A, ORG_B]             # one ORG-BOUND model per org
    assert calls["orgs"] == [ORG_A, ORG_B]     # verdicts keyed per org
    assert calls["seqs"] == [10, 20]           # each at ITS OWN latest seq
    assert conn.pruned_departed()              # targeted prune, not the blanket
    assert not conn.cleared_all()


def test_zero_orgs_clears_and_grounds_nothing(monkeypatch):
    conn = _FakeConn([])
    _patch_conn(monkeypatch, conn)

    def _boom(*a, **k):
        raise AssertionError("no model may be built with zero orgs")
    monkeypatch.setattr(_query_mod, "SemanticOrgModel", _boom)

    assert recompute_tenant_grounding(1) == 0
    assert conn.cleared_all()                  # org-less rows are the blend


def test_org_without_versions_is_skipped_other_still_grounds(monkeypatch):
    conn = _FakeConn([ORG_A, ORG_B])
    _patch_conn(monkeypatch, conn)
    calls = _patch_core(monkeypatch, grounded_per_org=5)

    class _Model:
        def __init__(self, _conn, connected_org_id=None):
            self.org = connected_org_id

        def current_version_seq(self):
            if self.org == ORG_A:
                raise _query_mod.VersionNotFoundError("gone (test)")
            return 20

    monkeypatch.setattr(_query_mod, "SemanticOrgModel", _Model)

    assert recompute_tenant_grounding(1) == 5   # org B only
    assert calls["orgs"] == [ORG_B]
