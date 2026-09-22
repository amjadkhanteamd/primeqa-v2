"""A MISS IS A 404 (triage round 2, batch 3 — AUD-016 / AUD-024 / AUD-032):
an id that names nothing answers 404, never a 200 that says "not found" in
prose, never a 200 that blames the store, never a 302 that lands on a 404.

THE GUARD, generated — never listed — from the live url_map: every GET rule
that takes an id (a ``<uuid:…>``, ``<int:…>`` or string converter) outside
``/api/*`` and ``/static`` is fetched as the audit admin with an id that
names nothing (a fresh uuid4; an int of 2,000,000,000; the string
"no-such-thing"); the answer must be a 404 — with the page's own "not found"
template where one exists (the shared-dashboard route's shape), so monitors,
crawlers and CI tell a dead link from a page. A non-uuid where a uuid goes
is a 404 at the router. A route added tomorrow is swept the day it appears.

Runs against ``S3A3_TEST_DATABASE_URL`` with ``REPORT_PAGES=1`` (scratch).
"""
from __future__ import annotations

import os
import re
import uuid

import pytest

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
_ENABLED = os.environ.get("REPORT_PAGES") == "1" and bool(DB)
pytestmark = [pytest.mark.skipif(not _ENABLED, reason="set REPORT_PAGES=1 + S3A3_TEST_DATABASE_URL")]
if _ENABLED:
    os.environ["DATABASE_URL"] = DB

ADMIN = 15
NOWHERE = {"uuid": str(uuid.uuid4()), "int": "2000000000", "string": "no-such-thing", "default": "no-such-thing",
           "path": "no/such/thing"}
# routes whose id parameter is NOT a lookup of a row (named, never guessed)
NOT_A_LOOKUP = {
    "/static/<path:filename>",
}
# retired v1 id spaces: the id names a dropped table; the path redirects to its
# live successor (AUD-032's shape) — asserted below to land on a 200
# Round 4 (AUD-025): the id-taking bookmark aliases come from the authority
# table's REDIRECT_ONLY (one source); /runs/<int> is the round-2 redirect.
from tests.unit.test_route_authority_table import REDIRECT_ONLY  # noqa: E402

RETIRED_REDIRECTS = {rule: succ for rule, succ in REDIRECT_ONLY.items() if "<int:" in rule}
RETIRED_REDIRECTS["/runs/<int:run_id>"] = "/runs/substrate"
ERROR_PAGE = ("Something went wrong", "Traceback (most recent call last)")


def _client(role="admin", user_id=ADMIN):
    import jwt as pyjwt
    from primeqa.app import app
    tok = pyjwt.encode({"sub": str(user_id), "tenant_id": 1, "role": role, "email": f"u{user_id}@audit", "full_name": f"u{user_id}"},
                       os.environ["JWT_SECRET"], algorithm="HS256")
    c = app.test_client()
    c.set_cookie("access_token", tok)
    return c


def _id_page_routes():
    """Every GET rule outside /api and /static that takes a converter parameter."""
    from primeqa.app import app
    out = []
    for r in app.url_map.iter_rules():
        if "GET" not in r.methods or r.endpoint == "static" or r.rule.startswith("/api/") or r.rule in NOT_A_LOOKUP:
            continue
        if r.rule in RETIRED_REDIRECTS:
            continue
        params = re.findall(r"<(?:([a-z]+):)?([a-z_]+)>", r.rule)
        if not params:
            continue
        out.append((r.rule, r.endpoint, params))
    return sorted(out)


def _fill(rule, params):
    out = rule
    for conv, name in params:
        out = re.sub(r"<(?:[a-z]+:)?%s>" % name, NOWHERE.get(conv or "default", "no-such-thing"), out)
    return out


@pytest.fixture(scope="module")
def matrix():
    routes = _id_page_routes()
    assert len(routes) >= 20, f"the census collected {len(routes)} id-taking page routes — it is not reading the url_map"
    rows = []
    for rule, endpoint, params in routes:
        c = _client()
        resp = c.get(_fill(rule, params), follow_redirects=False)
        txt = resp.get_data(as_text=True)
        rows.append({"rule": rule, "status": resp.status_code, "location": resp.headers.get("Location", ""),
                     "error_page": any(m in txt for m in ERROR_PAGE), "head": re.sub(r"\s+", " ", txt)[:120]})
    return rows


def _fmt(r):
    return f"GET {r['rule']:66s} -> {r['status']} {r['location'][:40]} {r['head'][:70]}"


def test_an_id_that_names_nothing_is_a_404(matrix):
    print(f"\nmiss sweep: {len(matrix)} id-taking page routes")
    for r in matrix:
        print("  " + _fmt(r))
    bad = [r for r in matrix if r["status"] != 404]
    assert bad == [], "a miss that is not a 404:\n  " + "\n  ".join(_fmt(r) for r in bad)


def test_no_miss_is_an_error_page(matrix):
    bad = [r for r in matrix if r["status"] >= 500 or r["error_page"]]
    assert bad == [], "\n  ".join(_fmt(r) for r in bad)


# --- the named members ------------------------------------------------------------------

def test_aud_016_a_non_uuid_conformance_run_id_is_a_404_at_the_router():
    for bad in ("abc", "161"):
        r = _client().get(f"/runs/conformance/{bad}")
        assert r.status_code == 404, (bad, r.status_code)


def test_aud_016_a_conformance_run_that_does_not_exist_is_a_404_not_an_outage():
    r = _client().get(f"/runs/conformance/{uuid.uuid4()}")
    txt = r.get_data(as_text=True)
    assert r.status_code == 404
    assert "unavailable" not in txt, "a miss must not read as an outage"
    assert "not found" in txt.lower()


def test_aud_016_the_store_unavailable_sentence_is_for_a_store_error_only(monkeypatch):
    from primeqa.intelligence import ui_report_console as C
    monkeypatch.setattr(C, "run_report", lambda *a, **k: {"available": False})
    r = _client().get(f"/runs/conformance/{uuid.uuid4()}")
    assert r.status_code == 503 and "unavailable" in r.get_data(as_text=True)


@pytest.mark.parametrize("path,marker", [
    ("/claims/{u}", "not found"), ("/claims/{u}/panel", "not found"),
    ("/claims/{u}/run-status/2000000000", "not found"), ("/runs/{u}", "not found"),
])
def test_aud_024_a_claim_run_or_job_miss_is_a_404_with_the_same_template(path, marker):
    r = _client().get(path.format(u=uuid.uuid4()))
    txt = r.get_data(as_text=True).lower()
    assert r.status_code == 404, (path, r.status_code)
    assert marker in txt and not any(m.lower() in txt for m in ERROR_PAGE)


def test_every_retired_id_space_redirects_to_a_live_successor():
    """The named retired paths still answer — as a redirect that lands on a 200."""
    from primeqa.app import app
    rules = {r.rule for r in app.url_map.iter_rules()}
    c = _client()
    for rule, successor in RETIRED_REDIRECTS.items():
        assert rule in rules, f"{rule} is gone — drop it from RETIRED_REDIRECTS"
        r = c.get(_fill(rule, re.findall(r"<(?:([a-z]+):)?([a-z_]+)>", rule)), follow_redirects=False)
        assert r.status_code == 302 and r.headers["Location"].startswith(successor), (rule, r.status_code, r.headers.get("Location"))
        assert c.get(r.headers["Location"]).status_code == 200, (rule, "the successor does not answer 200")


def test_aud_032_suites_redirects_to_a_page_that_exists():
    c = _client()
    for path in ("/suites", "/suites/7"):
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 302 and r.headers["Location"].endswith("/requirements"), (path, r.headers.get("Location"))
        landed = c.get(r.headers["Location"])
        assert landed.status_code == 200, (path, landed.status_code)
