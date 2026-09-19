"""Triage batch (2026-09-19), DB-real on scratch through the REAL routes: the
three member-authority gaps the audit landed (AUD-019/020/023) are closed
end to end — a member's POST is refused, the row is unchanged, the owner
and the admin still act, and an empty reason is refused for everyone.

Runs against ``S3A3_TEST_DATABASE_URL`` with ``REPORT_PAGES=1``; plants a
release, a waiver (recorded and reviewed by user 15) and a target (declared
by user 15) and removes them.
"""
from __future__ import annotations

import os
import re
from uuid import uuid4

import pytest
from sqlalchemy import text

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
_ENABLED = os.environ.get("REPORT_PAGES") == "1" and bool(DB)
pytestmark = [pytest.mark.skipif(not _ENABLED, reason="set REPORT_PAGES=1 + S3A3_TEST_DATABASE_URL")]
if _ENABLED:
    os.environ["DATABASE_URL"] = DB

ENV = 4
OWNER, MEMBER, VIEWER = 15, 14, 13          # the scratch audit users (admin / tester / viewer)


def _posting_client(user_id: int, role: str):
    import jwt as pyjwt
    from primeqa.app import app
    token = pyjwt.encode({"sub": str(user_id), "tenant_id": 1, "role": role, "email": f"u{user_id}@audit", "full_name": f"u{user_id}"},
                         os.environ["JWT_SECRET"], algorithm="HS256")
    c = app.test_client(); c.set_cookie("access_token", token)
    c.get("/login")
    tok = None
    for k, v in getattr(c, "_cookies", {}).items():
        if "csrf_token" in str(k):
            tok = getattr(v, "value", None) or str(v).split("=")[-1]
    return c, {"X-CSRF-Token": tok or ""}


def _flash(c, location: str) -> str:
    html = c.get(location).get_data(as_text=True)
    m = re.search(r'__primeqaFlash = \[\["(?:error|success)", "((?:[^"\\]|\\.)*)"\]\]', html)
    return (m.group(1) if m else "").encode().decode("unicode_escape")


@pytest.fixture(scope="module")
def world():
    from sqlalchemy import create_engine
    from primeqa.semantic.connection import get_tenant_connection
    eng = create_engine(DB)
    with eng.begin() as c:
        rid = c.execute(text("INSERT INTO releases (tenant_id, name, version_tag, status, created_by) "
                             "VALUES (1, :n, 'triage', 'planning', :u) RETURNING id"),
                        {"n": f"TRIAGE-authority-{uuid4().hex[:6]}", "u": OWNER}).scalar()
    with get_tenant_connection(1) as conn:
        claim = conn.execute(text("SELECT CAST(claim_test_id AS text) FROM s4_execution_runs LIMIT 1")).scalar()
        wid = str(uuid4())
        conn.execute(text("INSERT INTO quality_waivers (id, release_id, item_kind, item_ref, axis, reviewer_user_id, reason, expires_at, created_by, created_at) "
                          "VALUES (CAST(:w AS uuid), :r, 'claim', :c, 'functional', :u, 'triage fixture', now() + interval '1 day', :u, now())"),
                     {"w": wid, "r": rid, "c": claim or "t", "u": OWNER})
        conn.execute(text("INSERT INTO release_targets (release_id, environment_id, declared_by, declared_at, active) VALUES (:r, :e, :u, now(), true)"),
                     {"r": rid, "e": ENV, "u": OWNER})
    yield {"release": rid, "waiver": wid}
    eng2 = create_engine(DB, isolation_level="AUTOCOMMIT")
    with eng2.connect() as c:
        c.execute(text("SET session_replication_role = replica"))
        c.execute(text("SET search_path TO tenant_1, public"))
        for sql, p in (("DELETE FROM quality_waivers WHERE id = CAST(:w AS uuid)", {"w": wid}),
                       ("DELETE FROM release_targets WHERE release_id = :r", {"r": rid}),
                       ("DELETE FROM release_decisions WHERE release_id = :r", {"r": rid}),
                       ("DELETE FROM releases WHERE id = :r", {"r": rid})):
            try:
                c.execute(text(sql), p)
            except Exception:  # noqa: BLE001
                pass


def _waiver_state(wid):
    from primeqa.semantic.connection import get_tenant_connection
    with get_tenant_connection(1) as conn:
        return conn.execute(text("SELECT revoked_by, revocation_reason FROM quality_waivers WHERE id = CAST(:w AS uuid)"), {"w": wid}).first()


def _target_state(rid):
    from primeqa.semantic.connection import get_tenant_connection
    with get_tenant_connection(1) as conn:
        return conn.execute(text("SELECT active, deactivated_by, deactivation_reason FROM release_targets WHERE release_id = :r AND environment_id = :e"),
                            {"r": rid, "e": ENV}).first()


# --- AUD-019: activation is admin's --------------------------------------------

def test_a_member_cannot_reach_policy_activation(world):
    c, hdr = _posting_client(MEMBER, "tester")
    r = c.post("/settings/quality-policy/activate", headers=hdr, data={"policy_id": str(uuid4())})
    assert r.status_code == 302 and r.headers["Location"].rstrip("/") in ("", "http://localhost")   # the tier deny


def test_an_admin_reaches_the_act(world):
    c, hdr = _posting_client(OWNER, "admin")
    r = c.post("/settings/quality-policy/activate", headers=hdr, data={"policy_id": str(uuid4())})
    assert r.status_code == 302 and r.headers["Location"].endswith("/settings/quality-policy")
    assert "no policy" in _flash(c, r.headers["Location"])                     # the act answered, for a made-up id


# --- AUD-020: a waiver's revocation ---------------------------------------------

def test_a_member_cannot_revoke_another_users_waiver(world):
    c, hdr = _posting_client(MEMBER, "tester")
    r = c.post(f"/settings/waivers/{world['waiver']}/revoke", headers=hdr, data={"reason": "I disagree"})
    assert r.status_code == 302
    assert "only the waiver's reviewer" in _flash(c, r.headers["Location"])
    assert _waiver_state(world["waiver"]) == (None, None)


def test_the_owner_cannot_revoke_with_an_empty_reason(world):
    c, hdr = _posting_client(OWNER, "admin")
    r = c.post(f"/settings/waivers/{world['waiver']}/revoke", headers=hdr, data={"reason": "   "})
    assert "carries its reason" in _flash(c, r.headers["Location"])
    assert _waiver_state(world["waiver"]) == (None, None)


def test_the_owner_revokes_with_a_reason(world):
    c, hdr = _posting_client(OWNER, "admin")
    r = c.post(f"/settings/waivers/{world['waiver']}/revoke", headers=hdr, data={"reason": "the state changed"})
    assert "Waiver revoked" in _flash(c, r.headers["Location"])
    assert _waiver_state(world["waiver"]) == (OWNER, "the state changed")


# --- AUD-023: a target's removal --------------------------------------------------

def test_a_member_cannot_remove_another_users_target(world):
    c, hdr = _posting_client(MEMBER, "tester")
    r = c.post(f"/releases/{world['release']}/targets/{ENV}/remove", headers=hdr, data={"reason": "not mine to keep"})
    assert "declared this target, or an admin" in _flash(c, r.headers["Location"])
    assert tuple(_target_state(world["release"])) == (True, None, None)


def test_the_declarer_cannot_remove_with_an_empty_reason(world):
    c, hdr = _posting_client(OWNER, "admin")
    r = c.post(f"/releases/{world['release']}/targets/{ENV}/remove", headers=hdr, data={"reason": ""})
    assert "carries its reason" in _flash(c, r.headers["Location"])
    assert tuple(_target_state(world["release"])) == (True, None, None)


def test_the_declarer_removes_with_a_reason(world):
    c, hdr = _posting_client(OWNER, "admin")
    r = c.post(f"/releases/{world['release']}/targets/{ENV}/remove", headers=hdr, data={"reason": "wrong org"})
    assert "Target removed" in _flash(c, r.headers["Location"])
    assert tuple(_target_state(world["release"])) == (False, OWNER, "wrong org")
