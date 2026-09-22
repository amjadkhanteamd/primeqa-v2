"""Round 4, part D (AUD-034), DB-real on scratch: the API request log lands in
its table through the real engine, carries what the row should and nothing it
should not, and the prune keeps 30 days."""
from __future__ import annotations

import os
import sys

import pytest
from sqlalchemy import create_engine, text

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
pytestmark = [pytest.mark.skipif(not DB, reason="set S3A3_TEST_DATABASE_URL")]
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

MARK = "/api/__round4_probe__"


@pytest.fixture(scope="module")
def eng():
    e = create_engine(DB)
    yield e
    with e.begin() as c:
        c.execute(text("DELETE FROM api_request_log WHERE endpoint LIKE 'r4probe%' OR rule = :m"), {"m": MARK})


def test_the_hook_writes_through_the_real_engine_and_prune_keeps_the_window(eng):
    from primeqa.shared import request_log as RL
    from primeqa.app import app
    import primeqa.db as dbmod
    if dbmod.engine is None:
        dbmod.init_db(os.environ["DATABASE_URL"])
    RL.drain()
    c = app.test_client()
    r = c.get("/api/_internal/health")                      # anonymous by design
    assert r.status_code == 200
    r = c.get("/api/auth/me?token=NEVER-LOGGED", headers={"Authorization": "Bearer not.a.token"})
    assert r.status_code == 401
    written = RL.flush()
    assert written >= 2, f"flush wrote {written}"
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT rule, method, status, caller_kind, min_tier, user_id FROM api_request_log "
            "WHERE rule IN ('/api/_internal/health', '/api/auth/me') ORDER BY at DESC LIMIT 2")).mappings().all()
    by_rule = {r["rule"]: r for r in rows}
    assert by_rule["/api/_internal/health"]["caller_kind"] == "anonymous" and by_rule["/api/_internal/health"]["min_tier"] is None
    assert by_rule["/api/auth/me"]["status"] == 401 and by_rule["/api/auth/me"]["caller_kind"] == "bearer"
    with eng.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM api_request_log WHERE rule LIKE '%NEVER-LOGGED%' OR endpoint LIKE '%NEVER%'")).scalar() == 0
    # the prune: a 31-day-old row goes, a fresh one stays
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO api_request_log (at, caller_kind, method, rule, endpoint, status) VALUES "
            "(now() - interval '31 days', 'anonymous', 'GET', :m, 'r4probe_old', 200), "
            "(now() - interval '1 day', 'anonymous', 'GET', :m, 'r4probe_new', 200)"), {"m": MARK})
        n = RL.prune(conn, days=30)
    assert n >= 1
    with eng.connect() as conn:
        left = [r[0] for r in conn.execute(text("SELECT endpoint FROM api_request_log WHERE rule = :m"), {"m": MARK})]
    assert left == ["r4probe_new"]
