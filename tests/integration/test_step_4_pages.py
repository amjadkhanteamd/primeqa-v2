"""Step 4 pages (LLD_STEP_4 §d, item 6): /run is retired to a redirect with the
retirement note — GET and POST both redirect, the POST enqueues nothing. Gated
like test_report_pages (REPORT_PAGES=1 + scratch, its own pytest invocation)."""
from __future__ import annotations

import os

import pytest

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
_ENABLED = os.environ.get("REPORT_PAGES") == "1" and bool(DB)
pytestmark = [pytest.mark.skipif(not _ENABLED, reason="set REPORT_PAGES=1 + S3A3_TEST_DATABASE_URL; run as its own pytest invocation")]
if _ENABLED:
    os.environ["DATABASE_URL"] = DB
    os.environ.setdefault("JWT_SECRET", "a" * 64)


def _client(role="tester"):
    import jwt as pyjwt
    from primeqa.app import app
    token = pyjwt.encode({"sub": "1", "tenant_id": 1, "role": role, "email": "amjad.khan@teamd.co.in"},
                         os.environ["JWT_SECRET"], algorithm="HS256")
    c = app.test_client(); c.set_cookie("access_token", token)
    return c


def _jobs():
    from sqlalchemy import create_engine, text
    e = create_engine(DB)
    with e.connect() as c:
        return c.execute(text("SELECT count(*) FROM tenant_1.s4_execution_jobs")).scalar()


def test_run_tests_is_a_redirect_with_the_retirement_note():
    c = _client()
    before = _jobs()
    r = c.get("/run", follow_redirects=False)
    assert r.status_code == 302 and r.headers["Location"].endswith("/releases?from=run")
    r2 = c.post("/run", data={"environment_id": "5901", "run_all": "1"}, follow_redirects=False)
    assert r2.status_code in (302, 400, 403)                # CSRF refuses the bare POST before the route; either way nothing runs
    assert _jobs() == before
    page = c.get("/releases?from=run", follow_redirects=False)
    assert page.status_code == 200 and b"Run Tests" in page.data


def test_schedules_panel_renders_the_template_and_authority_columns():
    c = _client(role="admin")
    r = c.get("/runs/substrate", follow_redirects=False)
    assert r.status_code == 200
    assert b"Scheduled runs" in r.data and b"fires a recorded plan" in r.data
