"""AUD-038 containment (D-499), DB-real on scratch through the real routes: a
deactivated user's NEXT request is refused within the token's life — web and
API — by both deactivation routes, both of which revoke refresh tokens; the
reactivated user is back; an active user is untouched. Users 14 (tester) and
15 (admin) are the scratch audit users; everything is restored."""
from __future__ import annotations

import os

import pytest
from sqlalchemy import text

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
_ENABLED = os.environ.get("REPORT_PAGES") == "1" and bool(DB)
pytestmark = [pytest.mark.skipif(not _ENABLED, reason="set REPORT_PAGES=1 + S3A3_TEST_DATABASE_URL")]
if _ENABLED:
    os.environ["DATABASE_URL"] = DB

MEMBER, ADMIN = 14, 15


def _client(uid, role):
    import jwt as pyjwt
    from primeqa.app import app
    tok = pyjwt.encode({"sub": str(uid), "tenant_id": 1, "role": role, "email": f"u{uid}@audit", "full_name": f"u{uid}"},
                       os.environ["JWT_SECRET"], algorithm="HS256")
    c = app.test_client(); c.set_cookie("access_token", tok)
    return c, tok


def _admin():
    c, tok = _client(ADMIN, "admin")
    c.get("/login")
    csrf = next((getattr(v, "value", None) for k, v in getattr(c, "_cookies", {}).items() if "csrf_token" in str(k)), "")
    return c, {"X-CSRF-Token": csrf, "Authorization": "Bearer " + tok}


def _engine():
    from primeqa import db as dbm
    return dbm.engine


def _active(uid):
    with _engine().connect() as c:
        return c.execute(text("SELECT is_active FROM users WHERE id = :u"), {"u": uid}).scalar()


def _live_tokens(uid):
    with _engine().connect() as c:
        return c.execute(text("SELECT count(*) FROM refresh_tokens WHERE user_id = :u AND NOT revoked AND expires_at > now()"), {"u": uid}).scalar()


def _plant_token(uid):
    from primeqa import db as dbm
    from primeqa.core.repository import RefreshTokenRepository, UserRepository
    from primeqa.core.service import AuthService
    db = dbm.SessionLocal()
    try:
        svc = AuthService(UserRepository(db), RefreshTokenRepository(db))
        raw, _ = svc._create_refresh_token(uid); db.commit()
        return raw
    finally:
        db.close()


@pytest.fixture(autouse=True)
def restore():
    yield
    with _engine().connect() as c:
        c.execute(text("UPDATE users SET is_active = true WHERE id IN (:a, :b)"), {"a": MEMBER, "b": ADMIN})
        c.execute(text("UPDATE refresh_tokens SET revoked = true WHERE user_id = :u AND NOT revoked"), {"u": MEMBER})
        c.commit()


def _assert_out(member_web, member_api):
    r = member_web.get("/requirements", follow_redirects=False)
    assert r.status_code == 302 and r.headers["Location"].endswith("/login?reason=inactive")
    assert any("access_token=" in h and ("=;" in h or '=""' in h) for h in r.headers.getlist("Set-Cookie"))
    r = member_api.get("/api/releases", follow_redirects=False)
    assert r.status_code == 401 and r.get_json()["error"]["code"] == "ACCOUNT_INACTIVE"


def _assert_in(member_web, member_api):
    assert member_web.get("/requirements", follow_redirects=False).status_code == 200
    assert member_api.get("/api/releases", follow_redirects=False).status_code == 200


def test_the_api_deactivate_route_ends_the_session_and_revokes_refresh_tokens():
    admin, hdr = _admin()
    raw = _plant_token(MEMBER)
    assert _live_tokens(MEMBER) >= 1
    web, _ = _client(MEMBER, "tester"); api, _ = _client(MEMBER, "tester")
    _assert_in(web, api)
    r = admin.post(f"/api/users/{MEMBER}/deactivate", headers=hdr, json={})
    assert r.status_code == 204 and _active(MEMBER) is False
    assert _live_tokens(MEMBER) == 0                                  # the service path revoked them
    _assert_out(web, api)                                             # the SAME still-valid token, next request
    from primeqa.app import app
    assert app.test_client().post("/api/auth/refresh", json={"refresh_token": raw}).status_code == 401
    assert admin.post(f"/api/users/{MEMBER}/activate", headers=hdr, json={}).status_code == 204
    web2, _ = _client(MEMBER, "tester"); api2, _ = _client(MEMBER, "tester")
    _assert_in(web2, api2)


def test_the_web_toggle_route_ends_the_session_and_revokes_refresh_tokens():
    admin, hdr = _admin()
    _plant_token(MEMBER)
    web, _ = _client(MEMBER, "tester"); api, _ = _client(MEMBER, "tester")
    r = admin.post(f"/users/{MEMBER}/toggle-active", headers=hdr, data={})
    assert r.status_code == 302 and _active(MEMBER) is False and _live_tokens(MEMBER) == 0
    _assert_out(web, api)


def test_the_active_admin_is_untouched_by_the_chokepoint():
    admin, hdr = _admin()
    assert admin.get("/requirements", follow_redirects=False).status_code == 200
    assert admin.get("/api/releases", headers=hdr, follow_redirects=False).status_code == 200
