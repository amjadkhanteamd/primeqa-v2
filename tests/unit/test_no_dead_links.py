"""The dead-link sweep as a merge gate (AUD-002, repaired in the triage batch
of 2026-09-19), with the proof that it CAN fail.

The sweep it replaces matched `href="(/[^"{}\\s]*)"`: every href carrying a
Jinja expression was excluded by its own regex, so the release page's
"View Reasoning" (`/releases/{{ release.id }}/decision`, a route that never
existed) stayed green. A sweep that cannot be shown to fail is not
evidence — so before the repo is judged, this file plants a dead target of
EVERY kind the sweep claims to see in a temporary template tree and asserts
each one is reported: the two links the audit found (AUD-003, AUD-004,
verbatim), a dead form action, a dead htmx verb, a form posting to a
GET-only route, a `url_for` to a missing endpoint, a `url_for` missing a
required argument, a dead JS fetch, and an `{% if %}` branch that dies.
It also asserts what must NOT be reported: a live parameterised link, a
JS literal that appends an id after a trailing slash, a wholly dynamic base.
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

import pytest

pytestmark = pytest.mark.unit

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import deadlinks_gate as dl  # noqa: E402


@pytest.fixture(scope="module")
def url_map():
    from primeqa.app import app
    return app.url_map


def _plant(files: dict) -> str:
    d = tempfile.mkdtemp(prefix="deadlinks-")
    t = os.path.join(d, "templates")
    for rel, body in files.items():
        p = os.path.join(t, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(body)
    return t


#: AUD-044 (round 3): a literal segment the rule's CONVERTER refuses is a dead
#: target the router 404s on — the sweep matched `[^/]+` for every parameter and
#: called two live dashboard links alive for months. These plants hold the fix.
CONVERTER_PLANT = {
    "conv_uuid.html": '<a href="/claims/inbox">Review inbox</a>',
    "conv_int.html": '<a href="/releases/latest">Latest release</a>',
    "conv_int_mid.html": '<a href="/releases/abc/decisions/7/final">x</a>',
}
CONVERTER_LIVE_PLANT = {
    "conv_mixed.html": '<form method="POST" action="/releases/{{ r.id }}/waivers/{{ w.id }}/revoke"><button>x</button></form>',
    "conv_uuid_ok.html": '<a href="/claims/{{ c.test_id }}">open</a>',
    "conv_int_ok.html": '<a href="/releases/16">release 16</a>',
    "conv_real_uuid.html": '<a href="/claims/00000000-0000-4000-8000-000000000000">open</a>',
}

#: Round 3, part E: the graph reads URLs built in JS through htmx's programmatic
#: call and URLs parked in ANY data attribute. Both kinds were previously found
#: by eye (the audit's two "JS-built URL" notes), which is not a graph.
BUILT_URL_DEAD_PLANT = {
    "htmx_ajax_literal_dead.html": "<script>htmx.ajax('GET', '/no/such/panel', '#x')</script>",
    "htmx_ajax_verb_dead.html": "<script>htmx.ajax('DELETE', '/releases/' + r + '/', '#x')</script>",
    "data_attr_dead.html": '<div data-poll-url="/requirements/{{ r.id }}/no-such-status"></div>',
}
BUILT_URL_LIVE_PLANT = {
    "htmx_ajax_live.html": "<script>htmx.ajax('GET', '/claims/' + id + '/panel', '#x')</script>",
    "data_attr_live.html": '<div data-poll-url="/requirements/{{ r.id }}/test-plan-status"></div>',
}

DEAD_PLANT = {
    "aud003.html": '<a href="/releases/{{ release.id }}/decision">View Reasoning →</a>',
    "aud004.html": '<a href="/test-cases?section_id={{ node.id }}">View TCs</a>',
    "action.html": '<form method="POST" action="/no/such/route/{{ x.id }}"><button>go</button></form>',
    "hx.html": '<button hx-delete="/releases/{{ r.id }}/does-not-exist">x</button>',
    "method.html": '<form method="POST"\n  action="/releases/{{ r.id }}"><button>submit</button></form>',
    "url_for_missing.html": '<a href="{{ url_for(\'no_such_endpoint\') }}">x</a>',
    "url_for_args.html": '<a href="{{ url_for(\'views.releases_detail\') }}">x</a>',
    "js.html": "<script>fetch('/api/never/' + id + '/thing', {method: 'POST'})</script>",
    "branch.html": '<a href="/releases{% if r %}/{{ r.id }}/nope{% endif %}">x</a>',
}
LIVE_PLANT = {
    "live_param.html": '<a href="/releases/{{ release.id }}?tab=decision">ok</a>',
    "live_prefix.html": "<script>fetch('/api/sections/' + id, {method: 'DELETE'})</script>",
    "live_dynamic.html": '<a href="{{ href }}">x</a>',
    "live_root.html": '<a href="/">home</a>',
    "live_static.html": '<link href="/static/css/x.css">',
    "live_url_for.html": '<a href="{{ url_for(\'views.releases_detail\', release_id=r.id) }}">x</a>',
}


def test_the_sweep_reports_every_planted_dead_target(url_map):
    rep = dl.sweep(url_map, _plant(DEAD_PLANT), None)
    dead_by_file = {t.where.split("/")[-1].split(":")[0]: why for t, why in rep.dead}
    for f in DEAD_PLANT:
        assert f in dead_by_file, f"{f} was NOT reported dead: {rep.as_dict()}"
    # the two audit findings, verbatim, are red
    assert "no route matches" in dead_by_file["aud003.html"]
    assert "no route matches" in dead_by_file["aud004.html"]
    # the method check speaks
    assert "not for POST" in dead_by_file["method.html"]
    # url_for: a missing endpoint and a missing argument are both dead
    assert "no endpoint" in dead_by_file["url_for_missing.html"]
    assert "lacks release_id" in dead_by_file["url_for_args.html"]
    # a dead branch of an {% if %} is dead
    assert "no route matches" in dead_by_file["branch.html"]
    assert rep.alive == [] or all(t.raw.startswith("/static") for t in rep.alive)


def test_a_literal_the_converter_refuses_is_dead(url_map):
    """AUD-044: /claims/inbox cannot reach /claims/<uuid:test_id>; /releases/latest
    cannot reach /releases/<int:release_id>. The old sweep called both alive."""
    rep = dl.sweep(url_map, _plant(CONVERTER_PLANT), None)
    dead_by_file = {t.where.split("/")[-1].split(":")[0]: why for t, why in rep.dead}
    for f in CONVERTER_PLANT:
        assert f in dead_by_file, f"{f} was NOT reported dead: {rep.as_dict()}"
        assert "no route matches" in dead_by_file[f]


def test_a_converter_satisfying_target_is_alive(url_map):
    """And the fix does not cry wolf: a Jinja wildcard satisfies any converter,
    including a rule that MIXES an int and a uuid, and a real uuid or int literal
    resolves."""
    rep = dl.sweep(url_map, _plant(CONVERTER_LIVE_PLANT), None)
    assert rep.dead == [], rep.as_dict()["dead_list"]


def test_a_url_built_in_js_or_parked_in_a_data_attribute_is_judged(url_map):
    """AUD-025's two 'JS-built URL' notes were found by eye. They are graph
    members now: an htmx.ajax target and any data-* path resolve like any other,
    with the htmx verb as the method.

    A CONCATENATED URL ('/claims/' + id + '/panel') exposes only its prefix to
    any static reader, so the graph judges the prefix and says so — that is the
    honest limit, and the live plant below pins it."""
    rep = dl.sweep(url_map, _plant(BUILT_URL_DEAD_PLANT), None)
    dead_by_file = {t.where.split("/")[-1].split(":")[0]: why for t, why in rep.dead}
    for f in BUILT_URL_DEAD_PLANT:
        assert f in dead_by_file, f"{f} was NOT reported dead: {rep.as_dict()}"
    assert "no route matches" in dead_by_file["htmx_ajax_literal_dead.html"]
    assert "not for DELETE" in dead_by_file["htmx_ajax_verb_dead.html"]      # the verb is the method
    rep = dl.sweep(url_map, _plant(BUILT_URL_LIVE_PLANT), None)
    assert rep.dead == [], rep.as_dict()["dead_list"]
    assert {t.kind for t in rep.alive} == {"htmx-ajax", "data-poll-url"}


def test_the_drawers_panel_call_is_in_the_graph():
    """The concrete case: the requirement workspace's drawer fetches
    /claims/<uuid>/panel through htmx.ajax in static/js/drawer.js."""
    rep = dl.sweep_repo()
    hits = [t for t in rep.alive + rep.unresolvable if t.kind == "htmx-ajax"]
    assert hits, "the graph sees no htmx.ajax target at all"
    assert any("/claims/" in t.raw for t in hits), [t.raw for t in hits]


def test_the_sweep_does_not_cry_wolf(url_map):
    rep = dl.sweep(url_map, _plant(LIVE_PLANT), None)
    assert rep.dead == [], rep.as_dict()["dead_list"]
    kinds = {t.where.split("/")[-1].split(":")[0]: t for t in rep.unresolvable}
    assert set(kinds) == {"live_dynamic.html"}          # a dynamic base is counted, never believed alive
    assert len(rep.alive) == len(LIVE_PLANT) - 1


def test_the_repo_has_no_dead_links():
    """The gate: every internal target in primeqa/templates and primeqa/static
    resolves against the LIVE url_map, with its method. The full count is
    printed so a reviewer sees the denominator, not only the zero."""
    rep = dl.sweep_repo()
    d = rep.as_dict()
    print(f"dead-link sweep: {d['targets']} targets, {d['alive']} alive, "
          f"{d['unresolvable']} unresolvable, {d['dead']} dead")
    assert d["targets"] > 200, "the sweep collected almost nothing — it is not reading the tree"
    assert rep.dead == [], "dead internal targets:\n  " + "\n  ".join(
        f"{x['where']} [{x['kind']}] {x['target']} — {x['reason']}" for x in d["dead_list"])
