"""Round 4, part D (AUD-033): two retired act routes answer 410 Gone with one
line naming the successor — for every signed-in caller alike — and an
anonymous caller still meets the login redirect. RED on main (the routes
planned / refused with a redirect)."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import jwt
import pytest

pytestmark = pytest.mark.unit
SECRET = os.environ.setdefault("JWT_SECRET", "0123456789abcdef" * 4)
RETIRED = {("/plans", "/releases/<id>/plan"),
           ("/requirements/7/run-substrate", "/requirements/<id>/plan")}


@pytest.fixture(scope="module")
def app():
    from primeqa.app import app
    return app


def _client(app, role):
    c = app.test_client()
    now = datetime.now(timezone.utc)
    tok = jwt.encode({"sub": "1", "tenant_id": 1, "role": role, "email": f"{role}@x", "full_name": role,
                      "iat": now, "exp": now + timedelta(minutes=5)}, SECRET, algorithm="HS256")
    c.set_cookie("access_token", tok)
    c.set_cookie("csrf_token", "t")
    return c


@pytest.mark.parametrize("path, successor", sorted(RETIRED))
@pytest.mark.parametrize("role", ["viewer", "tester", "admin"])
def test_a_retired_route_answers_410_and_names_its_successor(app, monkeypatch, path, successor, role):
    import primeqa.core.auth as A                       # the auth chokepoint (D-499), no DB here
    monkeypatch.setattr(A, "session_is_active", lambda *a, **k: True)
    r = _client(app, role).post(path, data={"csrf_token": "t", "environment_id": "4"})
    assert r.status_code == 410, f"{role} POST {path}: {r.status_code} {r.headers.get('Location')}"
    assert successor in r.get_data(as_text=True)
    assert "retired" in r.get_data(as_text=True).lower()


@pytest.mark.parametrize("path", sorted(p for p, _ in RETIRED))
def test_an_anonymous_caller_still_meets_the_login_redirect(app, path):
    c = app.test_client()
    c.set_cookie("csrf_token", "t")                     # past CSRF; the login gate is the subject
    r = c.post(path, data={"csrf_token": "t"})
    assert r.status_code in (301, 302, 303) and "/login" in r.headers.get("Location", ""), (r.status_code, r.headers.get("Location"))
