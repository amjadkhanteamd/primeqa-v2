"""Integration tests for the auth module — against the live tenant_1 with the
seeded admin user.

Round 4 (AUD-051): converted from an in-file custom runner (``run_tests()``
returned False and named no assertion) to plain pytest. The fifteen checks are
ONE ordered story (login → refresh → rotation → a tester → the cap → logout),
so they share module state and run in file order, each naming its own
assertion. The wave-0 TEST-4 race fixes stand: every fixture email carries a
per-run suffix, the cap is derived from the live count, and teardown removes
only THIS run's users.
"""
from __future__ import annotations

import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from primeqa.app import app  # noqa: E402

ADMIN_EMAIL = "admin@primeqa.io"
ADMIN_PASSWORD = "changeme123"
TENANT_ID = 1
RUN = uuid.uuid4().hex[:8]
TESTER_EMAIL = f"tester-{RUN}@primeqa.io"

client = app.test_client()
admin_tokens: dict = {}
tester_tokens: dict = {}
created_user_ids: list = []


#: Every fixture email this suite has ever minted has this shape and no
#: product act mints it — so the shape IS the suite's marker.
FIXTURE_SHAPE = r"^(tester|user[0-9]+|overflow)-[0-9a-f]{8}@primeqa\.io$"


def _teardown_auth_test_users(*, every_run: bool = False):
    """Delete THIS run's fixture users (the ``-{RUN}@primeqa.io`` suffix), or
    — at setup, ``every_run`` — every user of the suite's fixture SHAPE: a run
    that crashed before its teardown leaves its cap-worth of users behind and
    the next run meets TENANT_CAP at step 8 (round 4, AUD-051: 15 such rows
    from one crashed run filled scratch's 20-user cap). Their refresh tokens
    and audit rows go first (foreign keys). Idempotent, raw SQL."""
    import psycopg2
    url = os.environ.get("DATABASE_URL")
    if not url:
        return
    where, arg = ("email ~ %s", FIXTURE_SHAPE) if every_run else ("email LIKE %s", f"%-{RUN}@primeqa.io")
    conn = psycopg2.connect(url)
    with conn:
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM refresh_tokens WHERE user_id IN (SELECT id FROM users WHERE {where})", (arg,))
            cur.execute(f"DELETE FROM activity_log WHERE user_id IN (SELECT id FROM users WHERE {where})", (arg,))
            cur.execute(f"DELETE FROM users WHERE {where}", (arg,))
    conn.close()


@pytest.fixture(scope="module", autouse=True)
def _this_runs_users_are_removed():
    _teardown_auth_test_users(every_run=True)     # self-healing: a crashed run's residue too
    yield
    _teardown_auth_test_users()


def _admin():
    if "access" not in admin_tokens:
        pytest.skip("the admin login step did not succeed; this step depends on it")
    return {"Authorization": f"Bearer {admin_tokens['access']}"}


def test_01_admin_login_returns_access_and_refresh_tokens():
    r = client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD,
                                             "tenant_id": TENANT_ID})
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.data[:200]}"
    data = r.get_json()
    assert "access_token" in data and "refresh_token" in data
    assert data["user"]["email"] == ADMIN_EMAIL
    assert data["user"]["role"] in ("admin", "superadmin"), data["user"]["role"]
    admin_tokens["access"], admin_tokens["refresh"] = data["access_token"], data["refresh_token"]


def test_02_login_with_wrong_password_returns_401():
    r = client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": "wrongpassword",
                                             "tenant_id": TENANT_ID})
    assert r.status_code == 401, f"Expected 401, got {r.status_code}"


def test_03_me_with_valid_token_returns_user_info():
    r = client.get("/api/auth/me", headers=_admin())
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.data[:200]}"
    data = r.get_json()
    assert data["email"] == ADMIN_EMAIL and data["role"] in ("admin", "superadmin")


def test_04_me_with_invalid_token_returns_401():
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer invalidtoken123"}).status_code == 401


def test_05_me_without_token_returns_401():
    assert client.get("/api/auth/me").status_code == 401


def test_06_token_refresh_returns_new_tokens():
    _admin()
    r = client.post("/api/auth/refresh", json={"refresh_token": admin_tokens["refresh"]})
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.data[:200]}"
    data = r.get_json()
    assert "access_token" in data and "refresh_token" in data
    assert data["refresh_token"] != admin_tokens["refresh"], "New refresh token should differ"
    admin_tokens["access"], admin_tokens["refresh"] = data["access_token"], data["refresh_token"]


def test_07_old_refresh_token_is_revoked_after_rotation():
    _admin()
    old_refresh = admin_tokens["refresh"]
    r = client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    data = r.get_json()
    admin_tokens["access"], admin_tokens["refresh"] = data["access_token"], data["refresh_token"]
    r2 = client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
    assert r2.status_code == 401, f"Expected 401 for a reused refresh token, got {r2.status_code}"


def test_08_admin_can_create_a_tester_user():
    r = client.post("/api/auth/users", headers=_admin(), json={
        "email": TESTER_EMAIL, "password": "tester123", "full_name": "Test User", "role": "tester"})
    assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.data[:200]}"
    data = r.get_json()
    assert data["role"] == "tester" and data["email"] == TESTER_EMAIL
    created_user_ids.append(data["id"])


def test_09_tester_can_log_in():
    r = client.post("/api/auth/login", json={"email": TESTER_EMAIL, "password": "tester123",
                                             "tenant_id": TENANT_ID})
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.data[:200]}"
    data = r.get_json()
    assert data["user"]["role"] == "tester"
    tester_tokens["access"], tester_tokens["refresh"] = data["access_token"], data["refresh_token"]


def _tester():
    if "access" not in tester_tokens:
        pytest.skip("the tester login step did not succeed; this step depends on it")
    return {"Authorization": f"Bearer {tester_tokens['access']}"}


def test_10_tester_is_blocked_from_listing_users():
    assert client.get("/api/auth/users", headers=_tester()).status_code == 403


def test_11_tester_is_blocked_from_creating_users():
    r = client.post("/api/auth/users", headers=_tester(), json={
        "email": "hacker@evil.com", "password": "hack123", "full_name": "Hacker", "role": "admin"})
    assert r.status_code == 403, f"Expected 403, got {r.status_code}"


def test_12_admin_can_list_users():
    r = client.get("/api/auth/users", headers=_admin())
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    assert len(r.get_json()) >= 2


def test_13_duplicate_email_is_rejected():
    r = client.post("/api/auth/users", headers=_admin(), json={
        "email": TESTER_EMAIL, "password": "dup123", "full_name": "Duplicate", "role": "tester"})
    assert r.status_code == 409, f"Expected 409, got {r.status_code}"


def test_14_user_cap_is_enforced_from_the_live_count():
    """Create uuid-suffixed users until the cap is hit: every create is a 201
    (under cap) or a 409 'maximum' (at cap), and the cap is reached within a
    generous bound — robust to the tenant's pre-existing active-user count."""
    hit_cap = False
    for i in range(60):
        r = client.post("/api/auth/users", headers=_admin(), json={
            "email": f"user{i}-{RUN}@primeqa.io", "password": "pass123",
            "full_name": f"User {i}", "role": "viewer"})
        if r.status_code == 201:
            created_user_ids.append(r.get_json()["id"])
            continue
        assert r.status_code == 409, f"create #{i} expected 201 or 409, got {r.status_code}: {r.data[:200]}"
        err = r.get_json().get("error", {})
        msg = err.get("message", "") if isinstance(err, dict) else str(err)
        assert "maximum" in msg.lower(), f"Expected the user-cap 'maximum' error, got: {r.data[:200]}"
        hit_cap = True
        break
    assert hit_cap, "the user cap was never enforced after 60 creates"


def test_15_logout_revokes_refresh_tokens():
    r = client.post("/api/auth/logout", headers=_tester())
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    r2 = client.post("/api/auth/refresh", json={"refresh_token": tester_tokens["refresh"]})
    assert r2.status_code == 401, f"Expected 401 after logout, got {r2.status_code}"
