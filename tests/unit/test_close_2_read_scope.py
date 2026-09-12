"""Close 2 — the read scope and the memo discipline.

The scope exists to open ONE tenant connection per read-only render instead of
one per console. These tests fix the two properties that make that safe:

* the scope is best-effort about ACQUIRING a connection and never about the
  render — a failing render must propagate its own exception, not be masked;
* a memo hit may only serve a call whose inputs are identical, so consolidation
  stays an optimisation and never becomes a semantics change (AK's condition).

No database. The connection is a fake, so a red here is a logic defect.
"""
from __future__ import annotations

import contextlib

import pytest

from primeqa.semantic import read_scope as rs


class _FakeSession:
    def __init__(self, bind=None):
        self.bind = bind
        self.closed = False

    def close(self):
        self.closed = True


@contextlib.contextmanager
def _fake_conn(_tenant_id, log=None):
    if log is not None:
        log.append("enter")
    try:
        yield object()
    finally:
        if log is not None:
            log.append("exit")


@pytest.fixture()
def scope(monkeypatch):
    """A read_scope backed by a fake connection; returns the event log."""
    events: list[str] = []
    import primeqa.semantic.connection as conn_mod
    import sqlalchemy.orm as orm_mod
    monkeypatch.setattr(conn_mod, "get_tenant_connection",
                        lambda tid: _fake_conn(tid, events))
    monkeypatch.setattr(orm_mod, "Session", _FakeSession)
    return events


# ---------------------------------------------------------------- the scope

def test_scope_yields_one_session_and_closes_it(scope):
    with rs.read_scope(1) as s:
        assert isinstance(s, _FakeSession)
        assert rs.in_scope(s) is True
        assert not s.closed
        held = s
    assert held.closed is True
    assert scope == ["enter", "exit"]


def test_unreachable_substrate_yields_none_not_an_exception(monkeypatch):
    """The pre-scope behaviour was best-effort per console; the scope keeps it."""
    import primeqa.semantic.connection as conn_mod

    def _boom(_tid):
        raise RuntimeError("no substrate here")

    monkeypatch.setattr(conn_mod, "get_tenant_connection", _boom)
    ran = []
    with rs.read_scope(1) as s:
        ran.append(s)
    assert ran == [None]
    assert rs.in_scope(None) is False


def test_a_failing_render_propagates_and_still_closes(scope):
    """The scope must not swallow — or disguise — the render's own exception."""
    with pytest.raises(ValueError, match="the render failed"):
        with rs.read_scope(1) as s:
            held = s
            raise ValueError("the render failed")
    assert held.closed is True
    assert scope == ["enter", "exit"]


def test_a_plain_session_is_not_a_scope():
    assert rs.in_scope(_FakeSession()) is False


def test_a_test_double_is_not_a_scope():
    """A Mock answers every getattr. If the memo believed one, it would return
    the mock's attribute instead of performing the read — which is exactly how
    this was first caught, by four sequence-resolver tests going red."""
    from unittest.mock import MagicMock
    assert rs.in_scope(MagicMock()) is False
    calls = []
    for _ in range(2):
        rs.memo(MagicMock(), "k", lambda: calls.append(1) or "REAL")
    assert len(calls) == 2
    assert rs.memo(MagicMock(), "k", lambda: "REAL") == "REAL"


# ----------------------------------------------------------------- the memo

def test_memo_produces_once_per_key(scope):
    calls = []
    with rs.read_scope(1) as s:
        for _ in range(3):
            rs.memo(s, ("k", 1), lambda: calls.append("a") or "A")
        rs.memo(s, ("k", 2), lambda: calls.append("b") or "B")
    assert calls == ["a", "b"]


def test_memo_returns_the_same_value_every_time(scope):
    with rs.read_scope(1) as s:
        first = rs.memo(s, "k", lambda: {"rows": [1, 2]})
        second = rs.memo(s, "k", lambda: {"rows": ["DIFFERENT"]})
    assert second is first


def test_memo_off_a_scope_always_calls_through():
    calls = []
    for session in (None, _FakeSession()):
        for _ in range(2):
            rs.memo(session, "k", lambda: calls.append(1))
    assert len(calls) == 4


def test_two_scopes_do_not_share(scope):
    calls = []
    for _ in range(2):
        with rs.read_scope(1) as s:
            rs.memo(s, "k", lambda: calls.append(1))
    assert len(calls) == 2
