"""Close 2 — the session seams: same answer, fewer connections.

Each console below used to open its own tenant connection for a read that the
render had already opened a connection for. The seam lets it read on the
render's connection instead. The test that matters is not "it is faster" but
"it returns the SAME thing" — so every case runs the console both ways over one
fake connection and asserts the results are equal, then asserts the seam opened
nothing of its own.
"""
from __future__ import annotations

import contextlib

import pytest

from primeqa.semantic import read_scope as rs


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar(self):
        return self._rows[0][0] if self._rows else None


class _Conn:
    """Answers every query with a fixed row set, and counts what it was asked."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.statements = 0

    def execute(self, *_a, **_k):
        self.statements += 1
        return _Result(self.rows)


class _Session:
    def __init__(self, conn):
        self._conn = conn

    def connection(self):
        return self._conn

    def close(self):
        pass


@pytest.fixture()
def wiring(monkeypatch):
    """A fake tenant connection; returns (conn, opened) where opened counts the
    times a console opened a connection of its own."""
    conn = _Conn()
    opened = []

    @contextlib.contextmanager
    def _open(_tenant_id):
        opened.append(1)
        yield conn

    import primeqa.semantic.connection as conn_mod
    monkeypatch.setattr(conn_mod, "get_tenant_connection", _open)
    return conn, opened


def _scoped(conn):
    """A session that reports as a read scope and hands back ``conn``."""
    s = _Session(conn)
    setattr(s, "_plimsol_read_memo", {})
    return s


CASES = [
    ("quarantine map",
     lambda **kw: __import__("primeqa.intelligence.quarantine",
                            fromlist=["manual_states"]).manual_states(1, **kw),
     [{"test_id": "7", "active": True}]),
    ("claim counts",
     lambda **kw: __import__("primeqa.intelligence.s3_generation_console",
                            fromlist=["count_claims_by_requirement_status"]
                            ).count_claims_by_requirement_status(1, ["SQ-205"], **kw),
     []),
]


@pytest.mark.parametrize("name,call,rows", CASES, ids=[c[0] for c in CASES])
def test_the_seam_returns_what_its_own_connection_returns(wiring, name, call, rows):
    conn, opened = wiring
    conn.rows = rows
    own = call()
    assert len(opened) == 1, f"{name} should open one connection when given none"
    shared = call(session=_scoped(conn))
    assert len(opened) == 1, f"{name} opened a connection despite being given one"
    assert shared == own, f"{name} answered differently on the shared connection"


def test_the_attention_badge_reads_once_per_request(wiring, monkeypatch):
    """Two callers, one number. The sidebar badge and the Requirements page both
    want it; two reads could return two, and a nav badge that contradicts the
    page it links to is exactly the defect D-491 named."""
    from flask import Flask

    from primeqa.core.permissions import pending_human_attention
    conn, opened = wiring
    conn.rows = [(3,)]
    monkeypatch.setattr("primeqa.core.permissions._pending_human_reviews",
                        lambda *_a, **_k: 2)
    app = Flask(__name__)
    with app.test_request_context("/requirements"):
        first = pending_human_attention(1)
        second = pending_human_attention(1)
    assert first == second == {"drafts": 3, "human_reviews": 2, "total": 5}
    assert len(opened) == 1, "the badge must be read once per request"

    with app.test_request_context("/requirements"):
        pending_human_attention(1)
    assert len(opened) == 2, "a new request must read again"


def test_the_attention_badge_takes_the_render_session(wiring, monkeypatch):
    from flask import Flask

    from primeqa.core.permissions import pending_human_attention
    conn, opened = wiring
    conn.rows = [(3,)]
    monkeypatch.setattr("primeqa.core.permissions._pending_human_reviews",
                        lambda *_a, **_k: 0)
    app = Flask(__name__)
    with app.test_request_context("/requirements"):
        out = pending_human_attention(1, session=_scoped(conn))
    assert out == {"drafts": 3, "human_reviews": 0, "total": 3}
    assert opened == [], "the badge opened a connection despite being given one"


def test_the_badge_still_works_with_no_request_context(wiring, monkeypatch):
    from primeqa.core.permissions import pending_human_attention
    conn, opened = wiring
    conn.rows = [(1,)]
    monkeypatch.setattr("primeqa.core.permissions._pending_human_reviews",
                        lambda *_a, **_k: 0)
    assert pending_human_attention(1)["total"] == 1
    assert pending_human_attention(1)["total"] == 1
    assert len(opened) == 2, "outside a request there is nothing to cache on"


def test_a_failing_read_still_yields_a_zero_badge(monkeypatch):
    import primeqa.semantic.connection as conn_mod
    from primeqa.core.permissions import pending_human_attention

    def _boom(_tid):
        raise RuntimeError("no substrate")

    monkeypatch.setattr(conn_mod, "get_tenant_connection", _boom)
    assert pending_human_attention(1) == {"drafts": 0, "human_reviews": 0, "total": 0}
