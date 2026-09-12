"""Close 2 — a memo hit may only serve an identical call.

AK's condition on this slice: consolidation changes how many sessions a render
opens, never what a console RETURNS. The risk in a memo is a key that omits an
input, so a second call with different arguments is served the first call's
answer. These tests pin every input into the key for the two reads that are
shared across consoles.

No database: the underlying reader is a fake that records its arguments.
"""
from __future__ import annotations

import contextlib

import pytest

from primeqa.semantic import read_scope as rs


class _FakeSession:
    def __init__(self, bind=None):
        self.bind = bind

    def close(self):
        pass


@contextlib.contextmanager
def _fake_conn(_tenant_id):
    yield object()


@pytest.fixture()
def scope(monkeypatch):
    import primeqa.semantic.connection as conn_mod
    import sqlalchemy.orm as orm_mod
    monkeypatch.setattr(conn_mod, "get_tenant_connection", _fake_conn)
    monkeypatch.setattr(orm_mod, "Session", _FakeSession)


@pytest.fixture()
def reader(monkeypatch):
    """Replace the real assembler; record every call's arguments."""
    from primeqa.intelligence import substrate_decision as sd
    seen: list[dict] = []

    def _fake(session, external_keys, *, tenant_id=None, environment_id=None,
              connected_org_id=None, manual_q=None):
        seen.append({"keys": list(external_keys or []), "tenant_id": tenant_id,
                     "environment_id": environment_id,
                     "connected_org_id": connected_org_id,
                     "manual_q": dict(manual_q or {})})
        return [{"call": len(seen)}]

    monkeypatch.setattr(sd, "_assemble_claim_evidence_uncached", _fake)
    monkeypatch.setattr("primeqa.intelligence.quarantine.manual_states",
                        lambda tid, *, session=None: {"7": "pinned"})
    return seen


def _assemble(session, keys, **kw):
    from primeqa.intelligence.substrate_decision import _assemble_claim_evidence
    return _assemble_claim_evidence(session, keys, **kw)


# ------------------------------------------------- outside a scope: unchanged

def test_outside_a_scope_every_call_reads(reader):
    for _ in range(3):
        _assemble(_FakeSession(), ["A"], tenant_id=1, environment_id=59)
    assert len(reader) == 3


# --------------------------------------------------- inside a scope: one read

def test_identical_calls_read_once(scope, reader):
    with rs.read_scope(1) as s:
        a = _assemble(s, ["A", "B"], tenant_id=1, environment_id=59, connected_org_id="o")
        b = _assemble(s, ["A", "B"], tenant_id=1, environment_id=59, connected_org_id="o")
    assert len(reader) == 1
    assert a == b


@pytest.mark.parametrize("differs", [
    {"environment_id": 78},
    {"connected_org_id": "other-org"},
    {"tenant_id": 2},
])
def test_a_different_input_is_a_different_read(scope, reader, differs):
    base = {"tenant_id": 1, "environment_id": 59, "connected_org_id": "o"}
    with rs.read_scope(1) as s:
        _assemble(s, ["A"], **base)
        _assemble(s, ["A"], **{**base, **differs})
    assert len(reader) == 2, f"{differs} was served a stale memo"


def test_different_keys_are_a_different_read(scope, reader):
    with rs.read_scope(1) as s:
        _assemble(s, ["A"], tenant_id=1, environment_id=59)
        _assemble(s, ["A", "B"], tenant_id=1, environment_id=59)
        _assemble(s, ["B", "A"], tenant_id=1, environment_id=59)
    assert len(reader) == 3, "key ORDER must miss the memo, never share it"


# ------------------------------------------- the manual quarantine map is an input

def test_an_explicit_manual_map_is_part_of_the_key(scope, reader):
    with rs.read_scope(1) as s:
        _assemble(s, ["A"], tenant_id=1, environment_id=59, manual_q={"7": "pinned"})
        _assemble(s, ["A"], tenant_id=1, environment_id=59, manual_q={"7": "lifted"})
    assert len(reader) == 2
    assert [c["manual_q"] for c in reader] == [{"7": "pinned"}, {"7": "lifted"}]


def test_the_map_is_read_once_per_render_and_shared(scope, reader, monkeypatch):
    """A caller passing None and a caller passing the same map are one read.

    This is the decision tab's shape: the substrate verdict passes the map it
    fetched, the quality preview passes None. Both must see the SAME map, and
    the map must be read once for the render, not once per console.
    """
    reads = []
    monkeypatch.setattr(
        "primeqa.intelligence.quarantine.manual_states",
        lambda tid, *, session=None: reads.append(("t%s" % tid, rs.in_scope(session)))
        or {"7": "pinned"})
    with rs.read_scope(1) as s:
        a = _assemble(s, ["A"], tenant_id=1, environment_id=59)
        b = _assemble(s, ["A"], tenant_id=1, environment_id=59, manual_q={"7": "pinned"})
    assert reads == [("t1", True)], (
        "the quarantine map must be read ONCE per render, on the render's session")
    assert len(reader) == 1 and a == b


def test_a_none_map_still_reaches_the_reader_resolved(scope, reader):
    with rs.read_scope(1) as s:
        _assemble(s, ["A"], tenant_id=1, environment_id=59)
    assert reader[0]["manual_q"] == {"7": "pinned"}


def test_no_tenant_means_no_quarantine_read(scope, reader, monkeypatch):
    reads = []
    monkeypatch.setattr("primeqa.intelligence.quarantine.manual_states",
                        lambda tid, *, session=None: reads.append(tid) or {})
    with rs.read_scope(1) as s:
        _assemble(s, ["A"], environment_id=59)
    assert reads == []
