"""THE ROUTE-AUTHORITY TABLE — every act route declares its minimum tier and
its owner rule, and a route with no entry FAILS the gate (triage round 2,
batch 1; born in D-497 for the six routes of the "guarded by convention"
class, now covering all 113 write (method, rule) pairs of the url_map).

Proven BEHAVIOURALLY against the live app, never by reading a decorator's
name (``functools.wraps`` copies names, not gates): for every entry with a
tier, a caller one tier below is refused by the gate — the web deny is a
redirect to ``/``, the API deny a 403 envelope, and for VIEWER-tier routes
the caller below is nobody (no token: 401 or a redirect to /login) — and a
caller AT the minimum tier is not refused by it (the body may then fail for
want of a database; that is not this gate's claim). Completeness runs both
ways: a live write route absent from the table fails, and a table entry
naming no live route fails (a moved or retired route cannot leave a stale
promise behind).

The OWNER RULE column is the second half of the rule D-497 gave waivers and
targets — the undo of a declared act is the declarer's or an admin's, with a
reason — stated per route and proven where it is judged: in the service
(tests/unit/test_authority_rules.py, tests/integration/test_authority_*.py)
or in the route's body. "shared" means the resource is tenant-wide by design
(requirements, releases, claims, sections); "self" means the caller's own row.
"""
from __future__ import annotations

import os
import re

import pytest

from primeqa.core.authz import Tier

pytestmark = pytest.mark.unit

SECRET = os.environ.setdefault("JWT_SECRET", "0123456789abcdef" * 4)
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "ab" * 32)

PUBLIC, VIEWER, MEMBER, ADMIN, SUPERADMIN = "PUBLIC", Tier.VIEWER, Tier.MEMBER, Tier.ADMIN, Tier.SUPERADMIN
ROLE_AT = {VIEWER: "viewer", MEMBER: "tester", ADMIN: "admin", SUPERADMIN: "superadmin"}
BELOW = {MEMBER: "viewer", ADMIN: "tester", SUPERADMIN: "admin"}

# Round 4 (AUD-025, the decision memo's ruling): the bookmark aliases of retired
# v1 surfaces are KEPT and are REDIRECT-ONLY — each answers a redirect to a live
# successor and nothing else. Not orphans: the inventory reads this set. The
# miss sweep (tests/integration/test_miss_is_404.py) reads the id-taking ones
# from here and asserts each lands on a 200.
REDIRECT_ONLY = {
    "/results": "/runs/substrate",
    "/results/<int:run_id>": "/runs/substrate",
    "/suites": "/requirements",
    "/suites/<int:suite_id>": "/requirements",
    "/tickets": "/requirements",
}

# (method, rule) -> (minimum tier, owner rule). The rule IS the entry; the test is its proof.
AUTHORITY = {
    # --- auth and the public edge ---------------------------------------------------
    ("POST", "/api/auth/login"): (PUBLIC, "none — the login"),
    ("POST", "/api/auth/logout"): (PUBLIC, "self — clears the caller's tokens"),
    ("POST", "/api/auth/refresh"): (PUBLIC, "self — the refresh token is the credential; an inactive account is refused"),
    ("POST", "/login"): (PUBLIC, "none — the login form"),
    ("POST", "/api/webhooks/ci-trigger"): (PUBLIC, "HMAC signature; refuses without a plan (D-494)"),
    ("POST", "/run"): (PUBLIC, "retired — redirects"),
    # --- users --------------------------------------------------------------------
    ("POST", "/api/auth/users"): (ADMIN, "tier ceiling in the service (F-1)"),
    ("PATCH", "/api/auth/users/<int:user_id>"): (ADMIN, "tier ceiling + decapitation guard in the service"),
    ("POST", "/api/users/<int:user_id>/activate"): (ADMIN, "the service (D-499)"),
    ("POST", "/api/users/<int:user_id>/deactivate"): (ADMIN, "self refused; last superadmin refused in the service (D-499)"),
    ("POST", "/api/users/me/active-env"): (VIEWER, "self"),
    ("POST", "/users/new"): (ADMIN, "tier ceiling in the service"),
    ("POST", "/users/<int:user_id>/edit"): (ADMIN, "the service"),
    ("POST", "/users/<int:user_id>/toggle-active"): (ADMIN, "self refused; the service (D-499)"),
    # --- environments, connections, groups (admin resources) -------------------------
    ("POST", "/api/environments"): (ADMIN, "shared"),
    ("PATCH", "/api/environments/<int:env_id>"): (ADMIN, "shared"),
    ("POST", "/api/environments/<int:env_id>/credentials"): (ADMIN, "shared"),
    ("POST", "/api/environments/<int:env_id>/test-connection"): (ADMIN, "shared"),
    ("POST", "/environments"): (ADMIN, "shared"),
    ("POST", "/environments/<int:env_id>/delete"): (ADMIN, "shared"),
    ("POST", "/environments/<int:env_id>/edit"): (ADMIN, "shared"),
    ("POST", "/environments/<int:env_id>/sync-substrate"): (ADMIN, "shared"),
    ("POST", "/environments/<int:env_id>/sync-substrate/requeue-enrichment"): (ADMIN, "shared"),
    ("POST", "/environments/<int:env_id>/test-connection"): (ADMIN, "shared"),
    ("POST", "/api/connections"): (ADMIN, "shared"),
    ("DELETE", "/api/connections/<int:conn_id>"): (ADMIN, "shared"),
    ("POST", "/api/connections/<int:conn_id>/test"): (ADMIN, "shared"),
    ("POST", "/connections"): (ADMIN, "shared"),
    ("POST", "/connections/<int:conn_id>/delete"): (ADMIN, "shared"),
    ("POST", "/connections/<int:conn_id>/edit"): (ADMIN, "shared"),
    ("POST", "/connections/<int:conn_id>/test"): (ADMIN, "shared"),
    ("POST", "/api/groups"): (ADMIN, "shared"),
    ("DELETE", "/api/groups/<int:group_id>"): (ADMIN, "shared"),
    ("POST", "/api/groups/<int:group_id>/environments"): (ADMIN, "shared"),
    ("DELETE", "/api/groups/<int:group_id>/environments/<int:env_id>"): (ADMIN, "shared"),
    ("POST", "/api/groups/<int:group_id>/members"): (ADMIN, "shared"),
    ("DELETE", "/api/groups/<int:group_id>/members/<int:user_id>"): (ADMIN, "shared"),
    ("POST", "/groups"): (ADMIN, "shared"),
    ("POST", "/groups/<int:group_id>/delete"): (ADMIN, "shared"),
    ("POST", "/groups/<int:group_id>/edit"): (ADMIN, "shared"),
    ("POST", "/groups/<int:group_id>/environments"): (ADMIN, "shared"),
    ("POST", "/groups/<int:group_id>/environments/<int:env_id>/remove"): (ADMIN, "shared"),
    ("POST", "/groups/<int:group_id>/members"): (ADMIN, "shared"),
    ("POST", "/groups/<int:group_id>/members/<int:user_id>/remove"): (ADMIN, "shared"),
    ("POST", "/api/facts/<int:environment_id>/seed"): (ADMIN, "shared"),
    ("POST", "/api/patterns/<int:pattern_id>/resolve"): (ADMIN, "shared"),
    # --- settings ------------------------------------------------------------------
    ("POST", "/settings/agent"): (SUPERADMIN, "tenant"),
    ("POST", "/settings/llm-models/enable"): (SUPERADMIN, "tenant"),
    ("POST", "/settings/llm-models/refresh"): (SUPERADMIN, "tenant"),
    ("POST", "/settings/tenant-tier/<int:tenant_id>"): (SUPERADMIN, "tenant"),
    ("POST", "/settings/quality-policy/activate"): (ADMIN, "governance (AUD-019)"),
    # --- requirements and sections (shared by design) --------------------------------
    ("POST", "/api/requirements"): (MEMBER, "shared"),
    ("PATCH", "/api/requirements/<int:req_id>"): (MEMBER, "shared"),
    ("DELETE", "/api/requirements/<int:req_id>"): (MEMBER, "shared"),
    ("POST", "/api/requirements/<int:req_id>/purge"): (ADMIN, "shared"),
    ("POST", "/api/requirements/<int:req_id>/restore"): (MEMBER, "shared"),
    ("POST", "/api/requirements/<int:req_id>/sync"): (MEMBER, "shared"),
    ("POST", "/api/requirements/import-jira"): (MEMBER, "shared"),
    ("POST", "/requirements/new"): (MEMBER, "shared"),
    ("POST", "/requirements/decorate"): (MEMBER, "shared"),
    ("POST", "/requirements/import-jira"): (MEMBER, "shared"),
    ("POST", "/requirements/<int:req_id>/edit"): (MEMBER, "shared"),
    ("POST", "/requirements/<int:req_id>/sync"): (MEMBER, "shared"),
    ("POST", "/requirements/<int:req_id>/approve-drafts"): (MEMBER, "shared"),
    ("POST", "/requirements/<int:req_id>/generate-substrate"): (MEMBER, "shared"),
    ("POST", "/requirements/<int:req_id>/plan"): (MEMBER, "shared — the plan records its planner"),
    ("POST", "/requirements/<int:req_id>/run-substrate"): (VIEWER, "RETIRED 410 (AUD-033, round 4): names its successor, the requirement plan"),
    ("POST", "/requirements/<int:req_id>/surfaces"): (MEMBER, "shared — the link records its declarer"),
    ("POST", "/requirements/<int:req_id>/surfaces/<uuid:link_id>/unlink"): (MEMBER, "declarer-or-admin, with a reason (AUD-037; the service + the table)"),
    ("POST", "/api/sections"): (ADMIN, "shared"),
    ("PATCH", "/api/sections/<int:section_id>"): (ADMIN, "shared"),
    ("DELETE", "/api/sections/<int:section_id>"): (ADMIN, "shared"),
    ("POST", "/api/sections/<int:section_id>/purge"): (ADMIN, "shared; refused with the count while children exist (AUD-035)"),
    ("POST", "/api/sections/<int:section_id>/restore"): (ADMIN, "shared"),
    # --- claims, claim sets, plans, runs ------------------------------------------------
    ("POST", "/claims/<uuid:test_id>/approve"): (MEMBER, "shared — the human approval act"),
    ("POST", "/claims/<uuid:test_id>/deprecate"): (MEMBER, "shared"),
    ("POST", "/claims/<uuid:test_id>/quarantine"): (MEMBER, "shared"),
    ("POST", "/claims/<uuid:test_id>/run"): (MEMBER, "refuses without a plan (D-494)"),
    ("POST", "/claims/<uuid:test_id>/run-async"): (MEMBER, "refuses without a plan (D-494)"),
    ("POST", "/claim-sets/<claim_set_id>/approve"): (MEMBER, "state-gated (a revoked set is refused)"),
    ("POST", "/plans"): (VIEWER, "RETIRED 410 (AUD-033, round 4): names its successors, the release plan and the schedule"),
    ("POST", "/plans/<uuid:plan_id>/run"): (MEMBER, "any member executes a recorded plan; once (D-486)"),
    ("POST", "/api/s3-generation-jobs"): (MEMBER, "shared"),
    ("POST", "/api/s3-generation-jobs/<int:job_id>/cancel"): (VIEWER, "creator-or-admin, judged in the route body"),
    ("POST", "/api/s4-execution-jobs"): (MEMBER, "refuses without a plan (D-494)"),
    ("POST", "/runs/substrate/repairs/<int:proposal_id>"): (ADMIN, "the repair decision; DERIVED-only apply at the chokepoint and the table (D-497)"),
    ("POST", "/runs/substrate/schedules"): (ADMIN, "shared"),
    ("POST", "/runs/substrate/schedules/<int:schedule_id>"): (ADMIN, "shared"),
    ("POST", "/ask"): (VIEWER, "read-only Q&A"),
    ("POST", "/milestones"): (MEMBER, "shared"),
    # --- releases ---------------------------------------------------------------------
    ("POST", "/api/releases"): (MEMBER, "shared"),
    ("PATCH", "/api/releases/<int:release_id>"): (MEMBER, "shared"),
    ("DELETE", "/api/releases/<int:release_id>"): (ADMIN, "shared"),
    ("POST", "/api/releases/<int:release_id>/requirements"): (MEMBER, "shared"),
    ("DELETE", "/api/releases/<int:release_id>/requirements/<int:req_id>"): (MEMBER, "shared"),
    ("POST", "/api/releases/<int:release_id>/requirements/bulk"): (MEMBER, "shared"),
    ("POST", "/api/releases/<int:release_id>/evaluate-decision"): (MEMBER, "the act every rule protects; refuses an empty or non-current scope (D-496)"),
    ("POST", "/api/releases/<int:release_id>/decisions/<int:decision_id>/finalize"): (ADMIN, "governance: the human final decision (AUD-036); an override needs a reason"),
    ("POST", "/api/releases/<int:release_id>/status-token"): (MEMBER, "owner-or-admin: releases.created_by (AUD-012), judged in the route"),
    ("DELETE", "/api/releases/<int:release_id>/status-token"): (MEMBER, "owner-or-admin: releases.created_by (AUD-012), judged in the route"),
    ("POST", "/releases"): (MEMBER, "shared"),
    ("POST", "/releases/<int:release_id>/evaluate-decision"): (MEMBER, "as the API"),
    ("POST", "/releases/<int:release_id>/decisions/<int:decision_id>/final"): (ADMIN, "governance: the human final decision (AUD-036) — the same tier as the API's finalize"),
    ("POST", "/releases/<int:release_id>/plan"): (MEMBER, "the plan records its planner"),
    ("POST", "/releases/<int:release_id>/run"): (MEMBER, "refuses without a plan (D-494)"),
    ("POST", "/releases/<int:release_id>/targets"): (MEMBER, "the target records its declarer"),
    ("POST", "/releases/<int:release_id>/targets/<int:environment_id>/remove"): (MEMBER, "declarer-or-admin, with a reason (AUD-023; the planner + the table)"),
    ("POST", "/releases/<int:release_id>/waivers"): (MEMBER, "the waiver records its reviewer and recorder"),
    ("POST", "/releases/<int:release_id>/waivers/<uuid:waiver_id>/revoke"): (MEMBER, "reviewer, recorder or admin, with a reason (AUD-020; the service + the table)"),
    ("POST", "/settings/waivers"): (MEMBER, "as above"),
    ("POST", "/settings/waivers/<uuid:waiver_id>/revoke"): (MEMBER, "reviewer, recorder or admin, with a reason (AUD-020)"),
    # --- shared dashboard links ----------------------------------------------------------
    ("POST", "/api/dashboard/share"): (MEMBER, "the link records its creator"),
    ("POST", "/api/dashboard/share/<int:link_id>/revoke"): (MEMBER, "creator-or-admin (AUD-039), judged in the route; the listing is scoped to the caller unless admin"),
}

FILL = {"int": "1", "uuid": "00000000-0000-4000-8000-000000000001", "string": "x", "path": "x", None: "1"}


def _live_write_pairs():
    from primeqa.app import app
    out = {}
    for r in app.url_map.iter_rules():
        if r.endpoint == "static":
            continue
        for m in r.methods:
            if m in ("POST", "PATCH", "DELETE", "PUT"):
                out[(m, str(r.rule))] = r
    return out


def _path(rule: str) -> str:
    return re.sub(r"<(?:([a-z]+):)?([a-z_]+)>", lambda m: FILL.get(m.group(1), "1"), rule)


def _client(role):
    import jwt
    from datetime import datetime, timedelta, timezone
    from primeqa.app import app
    c = app.test_client()
    # a Referer so a route that bounces back to `request.referrer or "/"` after
    # acting cannot be mistaken for the tier gate's own redirect to "/"
    hdr = {"X-CSRF-Token": "gate-csrf-token", "Referer": "http://localhost/from-the-gate"}
    c.set_cookie("csrf_token", "gate-csrf-token")   # the double-submit check is an equality
    if role is None:
        return c, hdr
    now = datetime.now(timezone.utc)
    token = jwt.encode({"sub": "99", "tenant_id": 1, "role": role, "email": "t@x", "full_name": "t",
                        "iat": now, "exp": now + timedelta(minutes=5)}, SECRET, algorithm="HS256")
    c.set_cookie("access_token", token)
    hdr["Authorization"] = "Bearer " + token
    return c, hdr


def _is_tier_deny(resp) -> bool:
    # the TIER gate's own deny: the API envelope "Insufficient permissions" (require_tier_api) — an
    # owner-rule 403 from a route body says something else and is not the gate speaking
    if resp.status_code == 403 and resp.is_json:
        err = resp.get_json().get("error") or {}
        return err.get("code") in ("FORBIDDEN", "AUTHORIZATION") and "Insufficient permissions" in (err.get("message") or "")
    return resp.status_code in (301, 302, 303) and resp.headers.get("Location", "").rstrip("/") in ("", "http://localhost")


def _is_anonymous_deny(resp) -> bool:
    return resp.status_code == 401 or (resp.status_code in (301, 302, 303) and "/login" in resp.headers.get("Location", ""))


@pytest.fixture(autouse=True)
def _active_account(monkeypatch):
    # D-499: every authenticated request reads users.is_active at the auth
    # chokepoint; this gate judges the TIER, so the account is stubbed active
    from primeqa.core import auth as A
    monkeypatch.setattr(A, "session_is_active", lambda uid, tid: True)


# --- completeness, both ways ------------------------------------------------------------

def test_every_live_write_route_has_an_entry():
    missing = sorted(k for k in _live_write_pairs() if k not in AUTHORITY)
    assert missing == [], ("write routes with NO authority entry — declare the minimum tier and the owner rule: %s" % missing)


def test_every_entry_names_a_live_route():
    live = _live_write_pairs()
    stale = sorted(k for k in AUTHORITY if k not in live)
    assert stale == [], f"entries naming no live route (moved or retired — update the table): {stale}"


# --- the tier, proven by calling -------------------------------------------------------

TIERED = sorted(((m, r), t) for (m, r), (t, _) in AUTHORITY.items() if t != PUBLIC)


@pytest.mark.parametrize("pair,tier", TIERED, ids=[f"{m} {r}" for (m, r), _ in TIERED])
def test_one_tier_below_is_refused_by_the_gate(pair, tier):
    method, rule = pair
    if tier == VIEWER:
        c, hdr = _client(None)                       # below a viewer is nobody
        r = c.open(_path(rule), method=method, headers=hdr, data={"reason": "x"})
        assert _is_anonymous_deny(r), f"an anonymous caller was not refused: {r.status_code} {r.headers.get('Location')}"
        return
    c, hdr = _client(BELOW[tier])
    r = c.open(_path(rule), method=method, headers=hdr, data={"reason": "x"})
    assert _is_tier_deny(r), f"{BELOW[tier]} was not refused by the tier gate: {r.status_code} {r.headers.get('Location')}"


@pytest.mark.parametrize("pair,tier", TIERED, ids=[f"{m} {r}" for (m, r), _ in TIERED])
def test_the_minimum_tier_passes_the_gate(pair, tier):
    method, rule = pair
    c, hdr = _client(ROLE_AT[tier])
    r = c.open(_path(rule), method=method, headers=hdr, data={"reason": "x"})
    assert not _is_tier_deny(r), f"{ROLE_AT[tier]} was refused by the tier gate on its own route: {r.status_code}"


# --- the gate must be shown to fail -----------------------------------------------------

def test_a_route_with_no_entry_fails_the_gate(monkeypatch):
    live = _live_write_pairs()
    victim = sorted(live)[0]
    trimmed = {k: v for k, v in AUTHORITY.items() if k != victim}
    monkeypatch.setattr("tests.unit.test_route_authority_table.AUTHORITY", trimmed)
    with pytest.raises(AssertionError, match="NO authority entry"):
        test_every_live_write_route_has_an_entry()


def test_a_stale_entry_fails_the_gate(monkeypatch):
    extended = dict(AUTHORITY); extended[("POST", "/no/such/route")] = (MEMBER, "stale")
    monkeypatch.setattr("tests.unit.test_route_authority_table.AUTHORITY", extended)
    with pytest.raises(AssertionError, match="no live route"):
        test_every_entry_names_a_live_route()


@pytest.mark.parametrize("rule, successor", sorted(REDIRECT_ONLY.items()))
def test_a_redirect_only_alias_answers_a_redirect_to_its_successor(rule, successor):
    """Round 4 (AUD-025): each kept alias redirects a signed-in viewer to its
    successor — and does nothing else (no page of its own)."""
    from primeqa.app import app
    assert any(r.rule == rule for r in app.url_map.iter_rules()), f"{rule} is not a live rule"
    c, _ = _client(VIEWER)
    r = c.get(rule.replace("<int:run_id>", "7").replace("<int:suite_id>", "7"))
    assert r.status_code in (301, 302, 303), (rule, r.status_code)
    assert r.headers.get("Location", "").split("?")[0].rstrip("/").endswith(successor), (rule, r.headers.get("Location"))
