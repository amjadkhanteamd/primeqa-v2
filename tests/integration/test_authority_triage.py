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


def _token(user_id: int, role: str) -> str:
    import jwt as pyjwt
    return pyjwt.encode({"sub": str(user_id), "tenant_id": 1, "role": role, "email": f"u{user_id}@audit", "full_name": f"u{user_id}"},
                        os.environ["JWT_SECRET"], algorithm="HS256")


def _posting_client(user_id: int, role: str):
    from primeqa.app import app
    token = _token(user_id, role)
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


def _sweep_triage_surface_worlds(eng):
    """Remove every requirement/link/identity/share this suite's world shape
    left behind (the marker is the key prefix and the share token prefix)."""
    from sqlalchemy import create_engine
    with create_engine(DB, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text("SET session_replication_role = replica"))
        c.execute(text("SET search_path TO tenant_1, public"))
        for sql in ("DELETE FROM requirement_surface_link_claims WHERE link_id IN (SELECT id FROM requirement_surface_links WHERE requirement_key LIKE 'TRIAGE-SURF-%')",
                    "DELETE FROM requirement_surface_links WHERE requirement_key LIKE 'TRIAGE-SURF-%'",
                    "DELETE FROM requirement_identities WHERE external_key LIKE 'TRIAGE-SURF-%'",
                    "DELETE FROM public.shared_dashboard_links WHERE token LIKE 'triage-%'",
                    "DELETE FROM public.release_requirements WHERE requirement_id IN (SELECT id FROM public.requirements WHERE external_key LIKE 'TRIAGE-SURF-%')",
                    "DELETE FROM public.requirements WHERE external_key LIKE 'TRIAGE-SURF-%'"):
            c.execute(text(sql))
        c.execute(text("SET session_replication_role = origin"))



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


# =====================================================================================
# Triage round 2, batch 1 — the rule applied everywhere (AUD-012 / 036 / 037 / 039)
# =====================================================================================

@pytest.fixture(scope="module")
def world2(world):
    """A surface link declared by 15 (needs an active-inventory member and an
    established identity), two shared-dashboard links (one by 15, one by 14),
    and the world's release (created by 15)."""
    from sqlalchemy import create_engine
    from primeqa.semantic.connection import get_tenant_connection
    from primeqa.test_representation.identity import establish_for_key
    from primeqa.intelligence.requirement_surface_console import declare_surface
    eng = create_engine(DB)
    key = f"TRIAGE-SURF-{uuid4().hex[:6]}"
    # Round 4 (AUD-052): self-healing — a run that died before its teardown
    # left its TRIAGE-SURF world behind (five on scratch); the next run sweeps
    # every world of this suite's shape before planting its own.
    _sweep_triage_surface_worlds(eng)
    with eng.begin() as c:
        req = c.execute(text("INSERT INTO requirements (tenant_id, section_id, source, created_by, jira_summary, external_key) "
                             "VALUES (1, (SELECT id FROM sections WHERE tenant_id = 1 ORDER BY id LIMIT 1), 'manual', :u, 'triage surface owner', :k) RETURNING id"),
                        {"u": OWNER, "k": key}).scalar()
        l15 = c.execute(text("INSERT INTO shared_dashboard_links (tenant_id, environment_id, token, created_by, expires_at) VALUES (1, :e, :t, :u, now() + interval '1 day') RETURNING id"),
                        {"e": ENV, "t": "triage-" + uuid4().hex, "u": OWNER}).scalar()
        l14 = c.execute(text("INSERT INTO shared_dashboard_links (tenant_id, environment_id, token, created_by, expires_at) VALUES (1, :e, :t, :u, now() + interval '1 day') RETURNING id"),
                        {"e": ENV, "t": "triage-" + uuid4().hex, "u": MEMBER}).scalar()
    with get_tenant_connection(1) as conn:
        establish_for_key(conn, key, established_by=OWNER)
        member = conn.execute(text("SELECT surface_key FROM ui_surface_inventory_members WHERE inventory_version = "
                                   "(SELECT MAX(inventory_version) FROM claim_sets WHERE status = 'approved') LIMIT 1")).scalar()
    d = declare_surface(1, requirement_key=key, surface_key=member, user_id=OWNER)
    link = (d.get("link") or {}).get("link_id") or d.get("link_id")
    assert link, d
    yield {"req": req, "key": key, "link": str(link), "share15": l15, "share14": l14, "release": world["release"]}
    eng2 = create_engine(DB, isolation_level="AUTOCOMMIT")
    with eng2.connect() as c:
        c.execute(text("SET session_replication_role = replica"))
        c.execute(text("SET search_path TO tenant_1, public"))
        for sql, p in (("DELETE FROM requirement_surface_link_claims WHERE CAST(link_id AS text) = :l", {"l": str(link)}),
                       ("DELETE FROM requirement_surface_links WHERE requirement_key = :k", {"k": key}),
                       ("DELETE FROM requirement_identities WHERE external_key = :k", {"k": key}),
                       ("DELETE FROM public.shared_dashboard_links WHERE id IN (:a, :b)", {"a": l15, "b": l14}),
                       ("DELETE FROM public.requirements WHERE id = :r", {"r": req})):
            try:
                c.execute(text(sql), p)
            except Exception:  # noqa: BLE001
                pass


def _link_active(link):
    from primeqa.semantic.connection import get_tenant_connection
    with get_tenant_connection(1) as conn:
        return conn.execute(text("SELECT active, deactivation_reason FROM requirement_surface_links WHERE CAST(id AS text) = :l"), {"l": link}).first()


def _share_state(lid):
    from primeqa import db as dbm
    with dbm.engine.connect() as c:
        return c.execute(text("SELECT revoked_at IS NOT NULL FROM shared_dashboard_links WHERE id = :i"), {"i": lid}).scalar()


def _token_set(rid):
    from primeqa import db as dbm
    with dbm.engine.connect() as c:
        return c.execute(text("SELECT status_poll_token_hash IS NOT NULL FROM releases WHERE id = :r"), {"r": rid}).scalar()


# --- AUD-037: surface unlink — declarer-or-admin, with a reason -----------------------------

def test_a_member_cannot_unlink_a_surface_another_user_declared(world2):
    c, hdr = _posting_client(MEMBER, "tester")
    r = c.post(f"/requirements/{world2['req']}/surfaces/{world2['link']}/unlink", headers=hdr, data={"reason": "not mine"})
    assert r.status_code == 302
    assert "Only the person who declared this surface" in _flash(c, r.headers["Location"])
    assert tuple(_link_active(world2["link"])) == (True, None)


def test_the_declarer_cannot_unlink_with_an_empty_reason(world2):
    c, hdr = _posting_client(OWNER, "admin")
    r = c.post(f"/requirements/{world2['req']}/surfaces/{world2['link']}/unlink", headers=hdr, data={"reason": "  "})
    assert "carries its reason" in _flash(c, r.headers["Location"])
    assert tuple(_link_active(world2["link"])) == (True, None)


def test_the_declarer_unlinks_with_a_reason(world2):
    c, hdr = _posting_client(OWNER, "admin")
    r = c.post(f"/requirements/{world2['req']}/surfaces/{world2['link']}/unlink", headers=hdr, data={"reason": "surface retired"})
    assert "Unlinked" in _flash(c, r.headers["Location"])
    assert tuple(_link_active(world2["link"])) == (False, "surface retired")


# --- AUD-039: share links — creator-or-admin; the listing scoped -----------------------------

def test_a_member_sees_only_their_own_share_links_and_an_admin_sees_all(world2):
    c, _ = _posting_client(MEMBER, "tester")
    ids = {l["id"] for l in c.get("/api/dashboard/share").get_json()["links"]}
    assert world2["share14"] in ids and world2["share15"] not in ids
    a, _ = _posting_client(OWNER, "admin")
    ids = {l["id"] for l in a.get("/api/dashboard/share").get_json()["links"]}
    assert {world2["share14"], world2["share15"]} <= ids


def test_a_member_cannot_revoke_another_users_share_link_but_their_own_yes(world2):
    c, hdr = _posting_client(MEMBER, "tester")
    r = c.post(f"/api/dashboard/share/{world2['share15']}/revoke", headers=hdr, json={})
    assert r.status_code == 403 and "creator or an admin" in r.get_json()["error"]["message"]
    assert _share_state(world2["share15"]) is False
    r = c.post(f"/api/dashboard/share/{world2['share14']}/revoke", headers=hdr, json={})
    assert r.status_code == 200 and _share_state(world2["share14"]) is True


def test_an_admin_revokes_any_share_link(world2):
    a, hdr = _posting_client(OWNER, "admin")
    r = a.post(f"/api/dashboard/share/{world2['share15']}/revoke", headers=hdr, json={})
    assert r.status_code == 200 and _share_state(world2["share15"]) is True


# --- AUD-012: the status token — owner-or-admin -----------------------------------------------

def test_a_member_cannot_mint_or_revoke_the_token_on_another_users_release(world2):
    c, hdr = _posting_client(MEMBER, "tester")
    hdr = {**hdr, "Authorization": "Bearer " + _token(MEMBER, "tester")}
    r = c.post(f"/api/releases/{world2['release']}/status-token", headers=hdr, json={})
    assert r.status_code == 403 and "owner or an admin" in r.get_json()["error"]["message"]
    assert _token_set(world2["release"]) is False
    r = c.delete(f"/api/releases/{world2['release']}/status-token", headers=hdr)
    assert r.status_code == 403


def test_the_owner_and_an_admin_mint_and_revoke(world2):
    a, hdr = _posting_client(OWNER, "admin")
    hdr = {**hdr, "Authorization": "Bearer " + _token(OWNER, "admin")}
    assert a.post(f"/api/releases/{world2['release']}/status-token", headers=hdr, json={}).status_code == 201
    assert _token_set(world2["release"]) is True
    assert a.delete(f"/api/releases/{world2['release']}/status-token", headers=hdr).status_code == 200
    assert _token_set(world2["release"]) is False


# --- AUD-036: the final decision is an admin's, on both doors --------------------------------

def test_a_member_cannot_record_the_final_decision_on_either_door(world2):
    from primeqa import db as dbm
    with dbm.engine.connect() as c:
        did = c.execute(text("INSERT INTO release_decisions (release_id, recommendation, confidence, reasoning, criteria_met, recommended_by) "
                             "VALUES (:r, 'no_go', 0.5, '\"triage\"'::jsonb, '[]'::jsonb, 'ai') RETURNING id"), {"r": world2["release"]}).scalar()
        c.commit()
    try:
        c14, hdr = _posting_client(MEMBER, "tester")
        r = c14.post(f"/releases/{world2['release']}/decisions/{did}/final", headers=hdr, data={"final_decision": "no_go", "reason": "x"})
        assert r.status_code == 302 and r.headers["Location"].rstrip("/") in ("", "http://localhost")       # the tier deny
        hdr = {**hdr, "Authorization": "Bearer " + _token(MEMBER, "tester")}
        r = c14.post(f"/api/releases/{world2['release']}/decisions/{did}/finalize", headers=hdr, json={"final_decision": "no_go"})
        assert r.status_code == 403
        with dbm.engine.connect() as c:
            assert c.execute(text("SELECT final_decision FROM release_decisions WHERE id = :d"), {"d": did}).scalar() is None
    finally:
        with dbm.engine.connect() as c:
            c.execute(text("DELETE FROM release_decisions WHERE id = :d"), {"d": did}); c.commit()
