"""AUD-032 (triage round 2, batch 3): navigation names LIVE pages only.

The landing-page map and the registry's ``active_also_for`` prefixes are
held against the url_map: every landing path is a rule whose view RENDERS
(a preference for a redirect stub would land a user on a chain), and every
highlight prefix names a rule that answers. A retired path that lingers in
the landing map (the audit found /run, /runs/new, /suites) fails this gate
the day it is added back.
The retired paths themselves redirect to their live successor — /suites to
/requirements — and the successor answers 200 (the DB-real half is
tests/integration/test_miss_is_404.py).
"""
from __future__ import annotations

import inspect

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def app():
    from primeqa.app import app
    return app


def _renders(app, rule) -> bool:
    """A view that renders a page (or serves JSON) — never a bare redirect stub."""
    src = inspect.getsource(app.view_functions[rule.endpoint])
    return "render_template" in src or "jsonify" in src or "send_" in src


def _get_rules(app):
    return [r for r in app.url_map.iter_rules() if "GET" in r.methods and r.endpoint != "static"]


def test_every_landing_page_is_a_live_rendering_rule(app):
    from primeqa.core.navigation import _LANDING_PAGE_PERMISSION
    rules = {r.rule: r for r in _get_rules(app)}
    dead = []
    for path in _LANDING_PAGE_PERMISSION:
        r = rules.get(path)
        if r is None or not _renders(app, r):
            dead.append(path)
    assert dead == [], f"landing paths that are retired or redirect-only: {dead}"


def test_every_highlight_prefix_names_a_rule_that_answers(app):
    """A highlight prefix may name a rendering page (/claims/<uuid>) or a
    redirect that lands on one (6a: "including the redirected ones" — /results,
    /tickets); it may not name nothing at all."""
    from primeqa.core.navigation import SIDEBAR_ITEMS as NAV_REGISTRY
    rules = _get_rules(app)
    dead = []
    for item in NAV_REGISTRY:
        for prefix in item.get("active_also_for", ()):
            if not any(r.rule == prefix or r.rule.startswith(prefix + "/") for r in rules):
                dead.append((item["id"], prefix))
    assert dead == [], f"highlight prefixes that name no rule: {dead}"


def test_every_rendered_nav_url_is_a_live_rendering_rule(app):
    """Items the sidebar can RENDER point at live pages. The registry also
    keeps two never-rendered placeholders (Coverage Map, Audit Log: no backing
    page, gated on capabilities no role holds) — named here, not guessed."""
    from primeqa.core.navigation import SIDEBAR_ITEMS as NAV_REGISTRY
    placeholders = {"/coverage", "/audit-log"}
    rules = {r.rule: r for r in _get_rules(app)}
    dead = [item["url"] for item in NAV_REGISTRY
            if item.get("url") and item["url"] not in placeholders
            and (item["url"] not in rules or not _renders(app, rules[item["url"]]))]
    assert dead == [], f"nav items pointing at retired or redirect-only paths: {dead}"
    # the placeholders stay placeholders: still no rule answers them
    assert all(p not in rules for p in placeholders), "a placeholder now has a page — drop it from the exemption"


def test_the_retired_suites_path_redirects_to_its_live_successor(app):
    src = inspect.getsource(app.view_functions["views.suites_redirect"])
    assert 'redirect("/requirements")' in src and "/claims" not in src.split("redirect(")[-1]


def test_the_gate_sees_a_planted_retired_path(app, monkeypatch):
    from primeqa.core import navigation as N
    monkeypatch.setattr(N, "_LANDING_PAGE_PERMISSION", {**N._LANDING_PAGE_PERMISSION, "/suites": ()})
    with pytest.raises(AssertionError, match="/suites"):
        test_every_landing_page_is_a_live_rendering_rule(app)
