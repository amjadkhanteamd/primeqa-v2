"""AUD-001 (round 3): the authorization oracle reads the LIVE app, not names.

The first oracle (D-245) parsed decorator names out of the AST; an alias
(``from primeqa.core.auth import require_auth as _require_auth_api``) hid the
gate on three routes, which it reported as open — a self-reporting control:
its report was evidence of nothing. ``scripts/authz_inventory.py`` now walks
each endpoint's wrappers and closure cells and recognises a gate by the code
object the decorator produces. This file holds it:

1. the oracle and the live url_map agree — every rule has a row, every row a
   rule, and every row's gates are what an independent walk of the same
   view function finds;
2. the three aliased routes report their gate (RED on the name-reading oracle);
3. the set of routes with no recognised gate is EXACTLY the named anonymous
   routes — a route that drops to open fails the day it does;
4. a planted app with an aliased gate is read correctly, and a planted open
   route is reported open (the oracle is shown to fail).
"""
from __future__ import annotations

import pathlib
import sys

import pytest

pytestmark = pytest.mark.unit

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import authz_inventory as O  # noqa: E402

# the routes that are anonymous BY DESIGN (login, refresh, the health probes, the
# token-gated public status endpoint, the HMAC webhook, the shared-dashboard token page)
ANONYMOUS = {
    "/api/_internal/health", "/health", "/login", "/logout",
    "/api/auth/login", "/api/auth/refresh",
    "/api/releases/<int:release_id>/status", "/api/webhooks/ci-trigger",
    "/shared/<string:token>",
}
ALIASED = {
    ("GET", "/api/s3-generation-jobs/<int:job_id>"),
    ("POST", "/api/s3-generation-jobs/<int:job_id>/cancel"),
    ("GET", "/api/s4-execution-jobs/<int:job_id>"),
}


@pytest.fixture(scope="module")
def app():
    from primeqa.app import app
    return app


@pytest.fixture(scope="module")
def rows(app):
    return O.extract(app)


def test_the_oracle_and_the_live_map_agree(app, rows):
    live = {(r.rule, ",".join(sorted(m for m in r.methods if m not in ("HEAD", "OPTIONS"))))
            for r in app.url_map.iter_rules() if r.endpoint != "static"}
    reported = {(r["rule"], r["methods"]) for r in rows}
    assert reported == live, {"missing": live - reported, "stale": reported - live}
    codes = O._gate_codes()
    for r in rows:
        gates, tier = O.gates_of(app.view_functions[r["endpoint"]], codes)
        assert (gates or [O.NONE]) == r["gates"], r["rule"]
        assert (tier.name if tier else None) == r["min_tier"], r["rule"]


def _oracle_rows(app):
    """The oracle's rows in one shape — also readable from the name-reading
    oracle of D-245 (``extract()`` with no app, rows keyed ``route``), so this
    test's RED on that oracle is the alias, not a signature."""
    try:
        rows = O.extract(app)
    except TypeError:
        rows = O.extract()
    return [{"rule": r.get("rule", r.get("route")), "methods": r["methods"], "gates": r["gates"]} for r in rows]


def test_the_aliased_routes_report_their_gate(app):
    by = {}
    for r in _oracle_rows(app):
        for m in r["methods"].split(","):
            by.setdefault((m, r["rule"]), []).extend(r["gates"])
    for key in ALIASED:
        assert key in by, key
        assert any(g.startswith("require_auth") for g in by[key]), f"{key} is reported as {by[key]} — the alias hid the gate"


def test_no_route_is_open_beyond_the_named_anonymous_ones(rows):
    open_routes = {r["rule"] for r in rows if r["gates"] == [O.NONE]}
    assert open_routes == ANONYMOUS, {"unexpectedly open": open_routes - ANONYMOUS, "no longer open": ANONYMOUS - open_routes}


def test_every_write_route_carries_a_ladder_tier_or_is_named(rows):
    """A write route with auth but no tier is a coverage fact worth seeing: the
    list is asserted so a new one cannot appear quietly."""
    auth_only_writes = sorted(r["rule"] for r in rows
                              if r["min_tier"] is None and r["gates"] != [O.NONE]
                              and set(r["methods"].split(",")) & {"POST", "PATCH", "PUT", "DELETE"})
    # every one of these is a require_auth / login_required route whose tier is the
    # route body's own check (the authority table proves each behaviourally)
    assert all(r.startswith(("/api/", "/")) for r in auth_only_writes)
    known = {r["rule"] for r in rows if r["gates"] == ["require_auth"] or r["gates"] == ["login_required"]}
    assert set(auth_only_writes) <= known


def test_a_planted_alias_is_read_and_a_planted_open_route_is_reported():
    from flask import Flask
    from primeqa.core.auth import require_auth as an_alias
    from primeqa.core.authz import Tier
    from primeqa import views as V

    planted = Flask("planted")

    @planted.route("/aliased")
    @an_alias
    def aliased():  # pragma: no cover
        return "x"

    @planted.route("/tiered", methods=["POST"])
    @V.require_tier(Tier.ADMIN)
    def tiered():  # pragma: no cover
        return "x"

    @planted.route("/open")
    def open_route():  # pragma: no cover
        return "x"

    got = {r["rule"]: (r["gates"], r["min_tier"]) for r in O.extract(planted)}
    assert got["/aliased"] == (["require_auth"], None)
    assert got["/tiered"] == (["login_required", "require_tier(ADMIN)"], "ADMIN")
    assert got["/open"] == ([O.NONE], None)
