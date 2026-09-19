"""The route-authority table (triage batch 2026-09-19; AUD-019's proposed
"expected-tier table so the gap cannot reopen").

For every ACT in the "guarded by convention" class the table states the
minimum tier, and this gate proves it BEHAVIOURALLY against the live app:
a caller one tier below is refused by the tier gate (the web deny is a
redirect to ``/``), and a caller at the minimum tier is NOT refused by it.
No DB is needed for the deny — the gate reads the JWT before the body — and
the allowed side asserts only that the response is not the tier deny (the
body may then fail for want of a database; that is not this gate's claim).

The names are read from the live url_map, so a route that moves or is
renamed fails loudly instead of being skipped. Nothing here reads a
decorator's name: ``functools.wraps`` copies names, not gates (the audit's
lesson), so the only honest read of a gate is to call it.
"""
from __future__ import annotations

import os

import pytest

from primeqa.core.authz import Tier

pytestmark = pytest.mark.unit

SECRET = os.environ.setdefault("JWT_SECRET", "0123456789abcdef" * 4)
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "ab" * 32)

ROLE_AT = {Tier.VIEWER: "viewer", Tier.MEMBER: "tester", Tier.ADMIN: "admin", Tier.SUPERADMIN: "superadmin"}
BELOW = {Tier.MEMBER: "viewer", Tier.ADMIN: "tester", Tier.SUPERADMIN: "admin"}

# (method, path, minimum tier) — the acts of the class. Add a row when a new
# act of this kind lands; the row IS the rule, the test is its proof.
TABLE = [
    ("POST", "/settings/quality-policy/activate", Tier.ADMIN),                 # AUD-019: activation is governance
    ("POST", "/settings/waivers/00000000-0000-4000-8000-000000000001/revoke", Tier.MEMBER),   # AUD-020: owner-or-admin is judged in the service
    ("POST", "/releases/1/waivers/00000000-0000-4000-8000-000000000001/revoke", Tier.MEMBER),
    ("POST", "/releases/1/targets/59/remove", Tier.MEMBER),                    # AUD-023: declarer-or-admin is judged in the planner
    ("POST", "/runs/substrate/repairs/1", Tier.ADMIN),                         # attack #5: the repair decision is admin's
    ("POST", "/api/releases/1/evaluate-decision", Tier.MEMBER),                # the act every rule above protects
]


def _client(role: str):
    import jwt
    from datetime import datetime, timedelta, timezone
    from primeqa.app import app
    now = datetime.now(timezone.utc)
    token = jwt.encode({"sub": "99", "tenant_id": 1, "role": role, "email": "t@x", "full_name": "t",
                        "iat": now, "exp": now + timedelta(minutes=5)}, SECRET, algorithm="HS256")
    c = app.test_client(); c.set_cookie("access_token", token)
    c.get("/login")                                    # mints the csrf cookie (DB-free)
    tok = None
    for k, v in getattr(c, "_cookies", {}).items():
        if "csrf_token" in str(k):
            tok = getattr(v, "value", None) or str(v).split("=")[-1]
    return c, {"X-CSRF-Token": tok or "", "Authorization": "Bearer " + token}


def _is_tier_deny(resp) -> bool:
    if resp.status_code == 403 and resp.is_json:
        return (resp.get_json().get("error") or {}).get("code") in ("FORBIDDEN", "AUTHORIZATION")
    return resp.status_code in (301, 302, 303) and resp.headers.get("Location", "").rstrip("/") in ("", "http://localhost")


def test_every_row_names_a_live_route():
    from primeqa.app import app
    rules = [(str(r.rule), r.methods) for r in app.url_map.iter_rules()]
    import re
    for method, path, _ in TABLE:
        hit = [r for r, m in rules if method in m and re.match("^" + re.sub(r"<[^>]+>", "[^/]+", r) + "$", path)]
        assert hit, f"{method} {path} matches no route — the table is stale"


@pytest.fixture(autouse=True)
def _active_account(monkeypatch):
    # D-499: every authenticated request now reads users.is_active at the auth
    # chokepoint; this gate judges the TIER, so the account is stubbed active
    # (a user 99 exists in no database — without the stub the chokepoint refuses first)
    from primeqa.core import auth as A
    monkeypatch.setattr(A, "session_is_active", lambda uid, tid: True)


@pytest.mark.parametrize("method,path,tier", TABLE, ids=[f"{m} {p}" for m, p, _ in TABLE])
def test_one_tier_below_is_refused_by_the_gate(method, path, tier):
    c, hdr = _client(BELOW[tier])
    r = c.open(path, method=method, headers=hdr, data={"reason": "x"})
    assert _is_tier_deny(r), f"{BELOW[tier]} was not refused by the tier gate: {r.status_code} {r.headers.get('Location')}"


@pytest.mark.parametrize("method,path,tier", TABLE, ids=[f"{m} {p}" for m, p, _ in TABLE])
def test_the_minimum_tier_passes_the_gate(method, path, tier):
    c, hdr = _client(ROLE_AT[tier])
    r = c.open(path, method=method, headers=hdr, data={"reason": "x"})
    assert not _is_tier_deny(r), f"{ROLE_AT[tier]} was refused by the tier gate on its own route: {r.status_code}"
