"""Unit tests for the run-all ROUTER (D-278, S6 Slice 3.4) — dispatch a claim to
the single run path or the run-all path (Slice 3.3) by its RECORDED strategy kind.

The load-bearing properties:
  * the routing TABLE is exact and fail-loud — ``None``/``"single"`` → single,
    ``"bva"`` → run-all, any OTHER non-null kind → ``ValueError`` (never a silent
    fall-back to single);
  * **ABSENT (→single) and UNRECOGNIZED (→raise) are two DISTINCT branches** —
    an un-kinded claim is fine (no bva was intended), an unrecognized kind is a
    wiring fault that must surface;
  * the router READS the recorded kind off the claim and NEVER derives one from
    claim shape (that inference is §4a, generation-time);
  * DORMANCY — today no claim records a kind (recon case A), so over real
    approved claims the router always selects the single path.

Dispatch is tested with injected ``session_scope`` / ``single_fn`` / ``runall_fn``
fakes, so we assert WHICH path is selected without a live SF run or a DB.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

pytestmark = pytest.mark.unit

from primeqa.execution_engine.run import (
    route_strategy,
    _recorded_strategy_kind,
    run_claim_execution_for_tenant,
)


# --- 1. the routing table ----------------------------------------------------

@pytest.mark.parametrize("kind,expected", [
    (None, "single"),       # NO kind recorded — today's reality → single (honest default)
    ("single", "single"),
    ("bva", "runall"),      # the ONLY way to reach run-all
])
def test_route_strategy_table(kind, expected):
    assert route_strategy(kind) == expected


@pytest.mark.parametrize("bad", ["weird", "BVA", "Single", "boundary", "x", " "])
def test_route_strategy_unrecognized_non_null_raises(bad):
    # a recorded kind we don't understand is a fault, surfaced — never a silent
    # fall-back to single.
    with pytest.raises(ValueError):
        route_strategy(bad)


def test_route_strategy_empty_string_raises_not_absent():
    # the SEPARATE-BRANCH proof: empty string is a NON-NULL unrecognized kind →
    # raise; only literal None (absent) → single. The two are not conflated.
    with pytest.raises(ValueError):
        route_strategy("")
    assert route_strategy(None) == "single"


# --- 2. the recorded-kind reader (the §4a seam) ------------------------------

def test_recorded_kind_none_claim_is_none():
    assert _recorded_strategy_kind(None) is None


def test_recorded_kind_claim_without_field_is_none():
    # today's ClaimRead carries claim_kind (the assertion type), NOT a strategy
    # kind — a claim shape with no strategy_kind attr reads None (recon case A).
    claim = SimpleNamespace(claim_kind="existence", test_id=uuid4())
    assert _recorded_strategy_kind(claim) is None


def test_recorded_kind_reads_recorded_value():
    # the forward-compat seam: WHEN §4a records the kind, the reader returns it.
    assert _recorded_strategy_kind(SimpleNamespace(strategy_kind="bva")) == "bva"
    assert _recorded_strategy_kind(SimpleNamespace(strategy_kind="single")) == "single"


def test_recorded_kind_explicit_none_field_is_none():
    assert _recorded_strategy_kind(SimpleNamespace(strategy_kind=None)) is None


# --- 3. dispatch — WHICH path is selected (injected fakes, no DB) -------------

class _DispatchCoord:
    """Stands in for the coordinator: returns a fixed claim from
    ``get_current_approved_claim`` so the router resolves a strategy kind."""

    def __init__(self, claim):
        self._claim = claim
        self.resolved = []

    def get_current_approved_claim(self, session, test_id):
        self.resolved.append((session, test_id))
        return self._claim


class _Recorder:
    """A fake single_fn / runall_fn: records its (session, test_id, kwargs) and
    returns a sentinel so we can assert WHICH path produced the result."""

    def __init__(self, ret):
        self.ret = ret
        self.calls = []

    def __call__(self, session, test_id, **kwargs):
        self.calls.append((session, test_id, kwargs))
        return self.ret


def _scope_yielding(session_obj):
    @contextmanager
    def scope(tenant_id):
        yield session_obj
    return scope


def _run(claim, *, single, runall, session_obj=None, test_id=None):
    session_obj = session_obj if session_obj is not None else object()
    test_id = test_id or uuid4()
    coord = _DispatchCoord(claim)
    out = run_claim_execution_for_tenant(
        1, test_id, environment_id=7, coordinator=coord,
        session_scope=_scope_yielding(session_obj),
        single_fn=single, runall_fn=runall)
    return out, coord, session_obj, test_id


def test_dispatch_absent_kind_routes_single():
    claim = SimpleNamespace(claim_kind="existence")     # no strategy_kind → None
    single, runall = _Recorder("SINGLE"), _Recorder("RUNALL")
    out, coord, session_obj, test_id = _run(claim, single=single, runall=runall)
    assert out == "SINGLE"
    assert len(single.calls) == 1 and len(runall.calls) == 0
    # the resolved session + test_id are threaded straight through to the path fn.
    assert single.calls[0][0] is session_obj
    assert single.calls[0][1] == test_id
    # and the coordinator/env/sink are forwarded (contract lock).
    kw = single.calls[0][2]
    assert kw["environment_id"] == 7
    assert kw["coordinator"] is coord
    assert kw["record_sink"] is not None


def test_dispatch_single_kind_routes_single():
    claim = SimpleNamespace(strategy_kind="single")
    single, runall = _Recorder("SINGLE"), _Recorder("RUNALL")
    out, _, _, _ = _run(claim, single=single, runall=runall)
    assert out == "SINGLE"
    assert len(single.calls) == 1 and len(runall.calls) == 0


def test_dispatch_bva_kind_routes_runall():
    claim = SimpleNamespace(strategy_kind="bva")
    single, runall = _Recorder("SINGLE"), _Recorder("RUNALL")
    out, coord, session_obj, test_id = _run(claim, single=single, runall=runall)
    assert out == "RUNALL"
    assert len(runall.calls) == 1 and len(single.calls) == 0
    assert runall.calls[0][0] is session_obj
    assert runall.calls[0][1] == test_id


def test_dispatch_unrecognized_kind_raises_neither_path_runs():
    claim = SimpleNamespace(strategy_kind="weird")
    single, runall = _Recorder("SINGLE"), _Recorder("RUNALL")
    with pytest.raises(ValueError):
        _run(claim, single=single, runall=runall)
    assert single.calls == [] and runall.calls == []


def test_dispatch_no_approved_claim_routes_single():
    # get_current_approved_claim → None (no approved version) → _recorded is None
    # → single. The router degrades to today's behavior, not a crash.
    single, runall = _Recorder("SINGLE"), _Recorder("RUNALL")
    out, _, _, _ = _run(None, single=single, runall=runall)
    assert out == "SINGLE"
    assert len(single.calls) == 1 and len(runall.calls) == 0


# --- 4. DORMANCY proof on real claims (read-only; skips without the DB) -------

@pytest.mark.integration
def test_real_approved_claims_route_single_dormant():
    """Over real approved claims (the SQ-205/SQ-212 requirements), no claim records
    a strategy kind (recon case A), so ``_recorded_strategy_kind`` is ``None`` and
    the router selects the SINGLE path — run-all stays dormant on live data.
    Read-only; no writes; skips if the Railway DB isn't reachable."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        try:
            from dotenv import load_dotenv
            load_dotenv(os.path.join(os.path.dirname(__file__),
                                     "..", "..", "..", ".env"))
            url = os.environ.get("DATABASE_URL")
        except Exception:
            url = None
    if not url:
        pytest.skip("no DATABASE_URL — dormancy proof needs the Railway DB")

    from primeqa import db
    if getattr(db, "engine", None) is None:
        db.init_db(url)
    from sqlalchemy.orm import Session
    from primeqa.semantic.connection import get_tenant_connection
    from primeqa.intelligence.substrate_decision import _assemble_claim_evidence
    from primeqa.test_representation import SemanticTransactionCoordinator

    coord = SemanticTransactionCoordinator()
    checked = 0
    with get_tenant_connection(1) as conn:
        s = Session(bind=conn)
        try:
            ev = _assemble_claim_evidence(s, ["SQ-205", "SQ-212"], tenant_id=1)
            for c in ev:
                claim = coord.get_current_approved_claim(s, UUID(str(c["test_id"])))
                if claim is None:
                    continue
                kind = _recorded_strategy_kind(claim)
                assert kind is None, (c["test_id"], kind)      # recon case A holds
                assert route_strategy(kind) == "single"        # → dormant single
                checked += 1
        finally:
            s.close()
    assert checked > 0, "dormancy proof exercised no approved claims"
