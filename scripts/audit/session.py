"""Audit sessions: mint the cookies the four audit users would hold, and prime
CSRF the way a browser does, so a POST proves the ROUTE's gate and not the
CSRF layer. Scratch only — the secret is the scratch test secret."""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

import jwt

SECRET = os.environ.get("JWT_SECRET", "0123456789abcdef" * 4)

# (label) -> (user_id, tenant_id, role) — the rows planted on scratch
USERS = {
    "viewer_t1": (13, 1, "viewer"),
    "member_t1": (14, 1, "tester"),
    "admin_t1": (15, 1, "admin"),
    "member_t2": (12, 2, "tester"),
    "super_t1": (1, 1, "superadmin"),
}


def token(label: str, minutes: int = 60) -> str:
    uid, tid, role = USERS[label]
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": str(uid), "tenant_id": tid, "role": role,
                       "email": "%s@audit" % label, "full_name": "audit %s" % label,
                       "iat": now, "exp": now + timedelta(minutes=minutes)},
                      SECRET, algorithm="HS256")


def client_for(app, label: str):
    """A Flask test client logged in as the audit user, CSRF primed."""
    c = app.test_client()
    c.set_cookie("access_token", token(label), domain="localhost")
    return c


def csrf_of(client) -> str:
    """GET a page so the double-submit cookie is set; return its value."""
    r = client.get("/requirements", follow_redirects=True)
    for h in r.headers.getlist("Set-Cookie"):
        m = re.match(r"csrf_token=([^;]+)", h)
        if m:
            return m.group(1)
    for ck in client._cookies.values() if hasattr(client, "_cookies") else []:
        pass
    m = re.search(r'name="csrf_token" value="([^"]+)"', r.get_data(as_text=True))
    return m.group(1) if m else ""


def post(client, path, data=None, *, csrf=True, **kw):
    """POST with the CSRF token as both cookie and field, like a real form."""
    data = dict(data or {})
    if csrf:
        tok = csrf_of(client)
        if tok:
            data.setdefault("csrf_token", tok)
            client.set_cookie("csrf_token", tok, domain="localhost")
    return client.post(path, data=data, **kw)
