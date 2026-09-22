"""Round 4, part D (AUD-034): the API request log — what a row carries, what it
never carries, and the write path. No database: a fake engine records the
INSERT; the hook runs on a planted Flask app."""
from __future__ import annotations

import pytest
from flask import Flask, jsonify

from primeqa.shared import request_log as RL

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_queue():
    RL.drain()
    yield
    RL.drain()


class _FakeEngine:
    def __init__(self, fail=False):
        self.rows, self.fail = [], fail

    def begin(self):
        eng = self

        class _Ctx:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def execute(self_, stmt, rows):
                if eng.fail:
                    raise RuntimeError("db down")
                eng.rows.extend(rows)
        return _Ctx()


@pytest.fixture
def planted():
    app = Flask("planted")

    @app.route("/api/things/<int:thing_id>", methods=["GET", "DELETE"])
    def thing(thing_id):
        return jsonify({"id": thing_id})

    @app.route("/api/secret", methods=["POST"])
    def secret():
        return jsonify({"ok": True}), 201

    @app.route("/page")
    def page():
        return "html"

    RL.install(app)
    return app


def test_only_api_requests_are_recorded_and_a_row_carries_the_rule_never_the_path(planted):
    c = planted.test_client()
    c.get("/page")
    c.get("/api/things/42?token=SECRET&q=1", headers={"Authorization": "Bearer eyJ.secret.token"})
    rows = RL.drain()
    assert len(rows) == 1
    r = rows[0]
    assert r["rule"] == "/api/things/<int:thing_id>" and r["method"] == "GET" and r["status"] == 200
    assert r["caller_kind"] == "bearer" and r["endpoint"] == "thing"
    assert "42" not in r["rule"]                                  # the id is data, never logged
    blob = repr(r)
    assert "SECRET" not in blob and "eyJ" not in blob and "q=1" not in blob   # no query, no token


def test_a_post_body_is_never_recorded_and_the_status_is(planted):
    c = planted.test_client()
    c.set_cookie("access_token", "cookie-token-value")
    c.post("/api/secret", json={"password": "hunter2", "note": "BODY-MARKER"})
    r = RL.drain()[0]
    assert r["status"] == 201 and r["caller_kind"] == "cookie"
    assert "BODY-MARKER" not in repr(r) and "hunter2" not in repr(r) and "cookie-token-value" not in repr(r)


def test_caller_kinds(planted):
    c = planted.test_client()
    c.get("/api/things/1")
    c.get("/api/things/1", headers={"X-Signature": "sha256=abc"})
    kinds = [r["caller_kind"] for r in RL.drain()]
    assert kinds == ["anonymous", "webhook"]


def test_the_row_carries_the_user_the_gate_read_and_the_rules_declared_tier():
    row = RL.record(rule="/api/x", method="POST", endpoint="x", status=200, duration_ms=12.7,
                    user={"id": 7, "tenant_id": 1, "role": "tester", "email": "t@x", "full_name": "T"},
                    kind="bearer", min_tier="MEMBER")
    assert (row["user_id"], row["tenant_id"], row["role"], row["min_tier"], row["duration_ms"]) == (7, 1, "tester", "MEMBER", 12)
    assert "email" not in row and "full_name" not in row
    anon = RL.record(rule="/api/x", method="GET", endpoint="x", status=401, duration_ms=1,
                     user=None, kind="anonymous", min_tier=None)
    assert anon["user_id"] is None and anon["tenant_id"] is None and anon["role"] is None


def test_flush_writes_one_insert_of_every_queued_row_and_never_raises():
    for i in range(3):
        RL.enqueue(RL.record(rule="/api/x", method="GET", endpoint="x", status=200, duration_ms=i,
                             user=None, kind="anonymous", min_tier=None))
    eng = _FakeEngine()
    assert RL.flush(engine=eng) == 3 and len(eng.rows) == 3
    assert RL.flush(engine=eng) == 0                                  # drained
    RL.enqueue(RL.record(rule="/api/x", method="GET", endpoint="x", status=200, duration_ms=0,
                         user=None, kind="anonymous", min_tier=None))
    before = RL.STATS.flush_failures
    assert RL.flush(engine=_FakeEngine(fail=True)) == 0                # dropped, counted, no raise
    assert RL.STATS.flush_failures == before + 1


def test_the_queue_is_bounded_and_drops_are_counted():
    before = RL.STATS.dropped
    for i in range(RL.MAX_QUEUE + 5):
        RL.enqueue({"i": i})
    assert RL.STATS.dropped == before + 5
    rows = RL.drain()
    assert len(rows) == RL.MAX_QUEUE and rows[0]["i"] == 5


def test_the_declared_tier_of_every_live_rule_is_known():
    from primeqa.app import app
    from primeqa.core.authz_gates import min_tier_by_rule
    tiers = min_tier_by_rule(app)
    assert tiers[("/api/releases", "POST")] in ("MEMBER", "ADMIN")
    assert tiers[("/api/auth/login", "POST")] is None                 # anonymous by design
    assert tiers[("/api/_internal/health", "GET")] is None
    assert all(v in (None, "AUTH", "VIEWER", "MEMBER", "ADMIN", "SUPERADMIN") for v in tiers.values())


def test_the_prune_statement_keeps_the_window():
    assert "make_interval(days => :days)" in str(RL.PRUNE_SQL) and RL.RETENTION_DAYS == 30
    from primeqa.scheduler import api_request_log_prune_tick  # noqa: F401 — the tick exists
