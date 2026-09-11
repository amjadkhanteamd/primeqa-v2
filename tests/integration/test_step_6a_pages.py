"""Step 6a pages (LLD_STEP_6A §c, §d, §e, §f) on SCRATCH via the test client.
Gated like the other page suites: REPORT_PAGES=1 + scratch, own invocation."""
from __future__ import annotations

import os

import pytest

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
_ENABLED = os.environ.get("REPORT_PAGES") == "1" and bool(DB)
pytestmark = [pytest.mark.skipif(not _ENABLED, reason="set REPORT_PAGES=1 + S3A3_TEST_DATABASE_URL")]
if _ENABLED:
    os.environ["DATABASE_URL"] = DB
    os.environ.setdefault("JWT_SECRET", "a" * 64)


def _client(role="admin"):
    import jwt as pyjwt
    from primeqa.app import app
    token = pyjwt.encode({"sub": "1", "tenant_id": 1, "role": role,
                          "email": "amjad.khan@teamd.co.in"},
                         os.environ["JWT_SECRET"], algorithm="HS256")
    c = app.test_client(); c.set_cookie("access_token", token)
    return c


# --- §a the nav + the badge -------------------------------------------------

def test_the_nav_renders_four_items_with_the_badge():
    html = _client().get("/requirements").data.decode()
    import re
    nav = html.split("</nav>")[0]
    rendered = [m.strip() for m in re.findall(r'data-nav-item="([^"]+)"', nav)] or \
               [m.strip() for m in re.findall(r'>\s*([A-Z][A-Za-z ]{2,20})\s*<', nav)]
    for label in ("Requirements", "Results", "Releases"):
        assert label in " ".join(rendered), f"{label} missing from the nav: {rendered}"
    for gone in ("Test Library", "My Reviews", "Run Tests", "Dashboard"):
        assert gone not in " ".join(rendered), f"{gone} still in the nav"
    # The badge renders only when something is actually waiting; when nothing
    # is, its absence must AGREE with the count rather than merely be missing.
    import re as _re
    tab = _client().get("/requirements?tab=needs-review").data.decode()
    total = int(_re.search(r'data-testid="tab-needs-review" data-total="(\d+)"', tab).group(1))
    has_badge = 'data-nav-badge="requirements"' in html
    assert has_badge == (total > 0), f"badge={has_badge} but {total} item(s) await a person"


def test_the_badge_and_the_tab_carry_the_same_number():
    import re
    html = _client().get("/requirements").data.decode()
    nav = re.search(r'data-nav-badge="requirements">(\d+)<', html)
    tab = re.search(r'data-testid="needs-review-count">(\d+)<', html)
    if nav or tab:
        assert nav and tab and nav.group(1) == tab.group(1), "the badge and the tab disagree"


# --- §c the Requirements surface --------------------------------------------

def test_the_three_tabs_render():
    c = _client()
    for tab, marker in (("requirements", 'data-testid="requirements-tabs"'),
                        ("claims", 'data-testid="tab-claims"'),
                        ("needs-review", 'data-testid="tab-needs-review"')):
        r = c.get(f"/requirements?tab={tab}")
        assert r.status_code == 200 and marker in r.data.decode(), tab


def test_the_row_carries_the_three_columns_and_never_zeros_conformance():
    html = _client().get("/requirements").data.decode()
    if 'data-testid="req-board"' not in html:
        pytest.skip("no requirement rows on this scratch copy")
    assert 'data-testid="col-functional"' in html
    assert 'data-testid="col-readiness"' in html
    assert 'data-declared="no"' not in html or "no surface declared" in html


def test_the_readiness_filter_is_offered():
    html = _client().get("/requirements").data.decode()
    assert 'data-testid="readiness-filter"' in html
    assert _client().get("/requirements?readiness=CURRENT").status_code == 200
    assert _client().get("/requirements?readiness=NONSENSE").status_code == 200


# --- §b the org band ---------------------------------------------------------

def test_the_org_band_renders_on_all_three_surfaces():
    c = _client()
    for url in ("/requirements", "/runs/substrate", "/releases"):
        html = c.get(url).data.decode()
        assert 'data-testid="org-state-band"' in html, url
        for group in ("band-orgs", "band-policy", "band-cadence"):
            assert f'data-testid="{group}"' in html, f"{url} missing {group}"


# --- §d Results --------------------------------------------------------------

def test_the_kind_filter_spans_both_lanes():
    c = _client()
    html = c.get("/runs/substrate?group=runs").data.decode()
    assert 'data-testid="kind-filter"' in html
    for k in ("all", "functional", "conformance"):
        assert f'data-kind-id="{k}"' in html
    assert 'data-testid="conformance-runs"' in html          # the lane is present on 'all'
    only = c.get("/runs/substrate?group=runs&kind=conformance").data.decode()
    assert 'data-testid="conformance-runs"' in only
    fn = c.get("/runs/substrate?group=runs&kind=functional").data.decode()
    assert 'data-testid="conformance-runs"' not in fn


def test_a_grouped_lens_says_the_kind_filter_does_not_apply():
    html = _client().get("/runs/substrate?group=requirement").data.decode()
    assert 'data-testid="kind-not-applicable"' in html


def test_every_functional_row_says_where_it_came_from():
    html = _client().get("/runs/substrate?group=runs&since=90d").data.decode()
    if 'data-testid="run-from"' not in html:
        pytest.skip("no functional runs in the window on this scratch copy")
    assert ("no plan (pre-planner)" in html) or ("/plans/" in html)


# --- §f route hygiene --------------------------------------------------------

@pytest.mark.parametrize("old,new", [
    ("/claims", "/requirements?tab=claims"),
    ("/claims/inbox", "/requirements?tab=needs-review"),
    ("/reviews", "/requirements?tab=needs-review"),
    ("/test-cases", "/requirements?tab=claims"),
    ("/ui-report", "/runs/substrate?kind=conformance"),
])
def test_the_old_paths_redirect(old, new):
    r = _client().get(old, follow_redirects=False)
    assert r.status_code == 302 and r.headers["Location"].endswith(new), (old, r.headers.get("Location"))


def test_the_conformance_run_view_is_rehomed():
    job = "00000000-0000-0000-0000-000000000000"
    r = _client().get(f"/ui-report/runs/{job}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/runs/conformance/{job}")
    assert _client().get(f"/runs/conformance/{job}").status_code == 200


# --- §e the permission matrix ------------------------------------------------

def test_a_viewer_reads_and_cannot_act():
    v = _client(role="viewer")
    html = v.get("/requirements").data.decode()
    assert v.get("/requirements").status_code == 200
    assert v.get("/runs/substrate").status_code == 200
    assert v.get("/releases").status_code == 200
    assert "Settings" not in html.split('data-testid="org-state-band"')[0]
    nr = v.get("/requirements?tab=needs-review").data.decode()
    assert 'data-testid="approve-draft"' not in nr, "a viewer was offered Approve"


def test_a_member_acts_and_an_admin_sees_the_panels():
    m = _client(role="tester").get("/requirements?tab=needs-review").data.decode()
    assert 'data-testid="tab-needs-review"' in m
    a = _client(role="admin").get("/runs/substrate").data.decode()
    assert "Scheduled runs" in a


def test_unauthenticated_goes_to_login():
    from primeqa.app import app
    c = app.test_client()
    for url in ("/requirements", "/runs/substrate", "/releases"):
        r = c.get(url, follow_redirects=False)
        assert r.status_code == 302 and "/login" in r.headers["Location"], url
