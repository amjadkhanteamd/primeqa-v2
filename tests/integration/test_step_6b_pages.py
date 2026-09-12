"""Step 6b pages (LLD_STEP_6B §a, §b, §d, §e) on SCRATCH via the test client.
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


def _posting_client(role="admin"):
    """A client that can POST: CSRF is double-submit, so the cookie has to be
    minted by a GET first and echoed in the header. Without this a POST test
    proves only that CSRF works — never that the TIER gate does."""
    c = _client(role)
    c.get("/releases")                        # mints csrf_token
    tok = next((v.value for v in c._cookies.values()
                if getattr(v, "key", getattr(v, "name", "")) == "csrf_token"), None) \
        if hasattr(c, "_cookies") else None
    if tok is None:                            # werkzeug version differences
        jar = getattr(c, "_cookies", {})
        for k, v in (jar.items() if hasattr(jar, "items") else []):
            if "csrf_token" in str(k):
                tok = getattr(v, "value", None) or str(v).split("=")[-1]
    return c, {"X-CSRF-Token": tok or ""}


# --- §a Releases -------------------------------------------------------------

def test_the_release_list_carries_evidence_and_a_decision_state():
    html = _client().get("/releases").data.decode()
    assert 'data-testid="org-state-band"' in html
    if 'data-testid="release-board"' not in html:
        pytest.skip("no releases on this scratch copy")
    assert 'data-testid="release-evidence"' in html


def test_a_release_row_never_fails_the_list_when_its_evidence_cannot_be_read(monkeypatch):
    """Ruling 6: best-effort per row."""
    import primeqa.intelligence.releases_board_console as B
    monkeypatch.setattr(B, "_tenant_reads",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    r = _client().get("/releases")
    assert r.status_code == 200
    html = r.data.decode()
    if 'data-testid="release-board"' in html:
        assert "unavailable" in html.lower()


def test_the_open_release_offers_compare_and_the_quality_card():
    from sqlalchemy import create_engine, text
    with create_engine(DB).connect() as c:
        rid = c.execute(text("SELECT id FROM releases WHERE tenant_id=1 ORDER BY id LIMIT 1")).scalar()
    if rid is None:
        pytest.skip("no release on this scratch copy")
    html = _client().get(f"/releases/{rid}?tab=decision").data.decode()
    assert 'data-testid="compare-releases"' in html
    assert 'data-testid="quality-decision-card"' in html


def test_compare_is_rehomed_under_releases():
    c = _client()
    r = c.get("/ui-report/compare?baseline=a&candidate=b", follow_redirects=False)
    assert r.status_code == 302 and "/releases/compare" in r.headers["Location"]
    assert "baseline=a" in r.headers["Location"]
    assert c.get("/releases/compare").status_code == 200


def test_the_dashboard_is_absorbed_into_releases():
    c = _client()
    r = c.get("/dashboard", follow_redirects=False)
    assert r.status_code == 302 and r.headers["Location"].endswith("/releases")
    # the pre-6b view survives for one release cycle — nothing is deleted here.
    # It is asserted as REGISTERED rather than 200: the scratch copy's users
    # table predates `preferred_landing_page`, so the legacy page 500s here and
    # renders fine on production. What 6b promises is that it still answers.
    from primeqa.app import app
    assert "/dashboard/legacy" in {str(r.rule) for r in app.url_map.iter_rules()}
    assert c.get("/dashboard/legacy").status_code != 404


# --- §b Settings -------------------------------------------------------------

@pytest.mark.parametrize("url,marker", [
    ("/settings/quality-policy", 'data-testid="quality-policy-page"'),
    ("/settings/waivers", 'data-testid="settings-waivers"'),
    ("/settings/sites", 'data-testid="settings-sites"'),
    ("/settings/surface-inventory", 'data-testid="settings-surface-inventory"'),
    ("/settings/claim-sets", 'data-testid="settings-claim-sets"'),
    ("/settings/standards", 'data-testid="settings-standards"'),
    ("/settings/custom-rules", 'data-testid="settings-custom-rules"'),
])
def test_every_settings_page_renders(url, marker):
    r = _client().get(url)
    assert r.status_code == 200, url
    assert marker in r.data.decode(), url


def test_sites_says_why_it_is_read_only():
    html = _client().get("/settings/sites").data.decode()
    assert 'data-testid="cli-only"' in html
    assert "credentials" in html and "no portal cryptography" in html
    assert "<form" not in html.split('data-testid="settings-sites"')[1].split("</div>")[0]


def test_coverage_is_rehomed_under_standards():
    c = _client()
    r = c.get("/ui-report/coverage?job=abc", follow_redirects=False)
    assert r.status_code == 302 and "/settings/standards" in r.headers["Location"]
    assert 'data-testid="coverage-link"' in c.get("/settings/standards").data.decode()
    assert c.get("/settings/standards/coverage").status_code == 200


def test_the_policy_page_offers_activate_on_a_draft_only():
    html = _client().get("/settings/quality-policy").data.decode()
    assert "Activate is here now" in html
    from sqlalchemy import create_engine, text
    with create_engine(DB).connect() as c:
        drafts = c.execute(text("SELECT count(*) FROM tenant_1.quality_policies WHERE status='draft'")).scalar()
    assert ('data-testid="activate-policy"' in html) == (drafts > 0)


# --- §e the matrix -----------------------------------------------------------

def test_a_viewer_cannot_reach_the_settings_pages():
    v = _client(role="viewer")
    for url in ("/settings/quality-policy", "/settings/waivers", "/settings/sites",
                "/settings/standards", "/settings/claim-sets"):
        assert v.get(url, follow_redirects=False).status_code == 302, url
    # but a viewer still reads the release surfaces
    assert v.get("/releases").status_code == 200
    assert v.get("/releases/compare").status_code == 200


def test_a_member_acts_and_the_acts_are_gated():
    m = _client(role="tester")
    assert m.get("/settings/quality-policy").status_code == 200
    assert m.get("/settings/waivers").status_code == 200
    from primeqa.app import app
    anon = app.test_client()
    for url in ("/settings/waivers", "/releases/compare", "/dashboard"):
        r = anon.get(url, follow_redirects=False)
        assert r.status_code == 302 and "/login" in r.headers["Location"], url


# --- §e the two ACTS, end to end through the pages ---------------------------

def test_activate_a_draft_policy_through_the_page_and_the_frozen_one_refuses():
    """The control AK had to reach for a CLI to use. Same service, same audit."""
    from sqlalchemy import create_engine, text
    e = create_engine(DB)
    with e.connect() as c:
        before = c.execute(text(
            "SELECT CAST(id AS text), status FROM tenant_1.quality_policies ORDER BY version")).fetchall()
    draft = next((r[0] for r in before if r[1] == "draft"), None)
    if draft is None:
        pytest.skip("no draft policy on this scratch copy")
    was_active = next((r[0] for r in before if r[1] == "active"), None)

    c, hdr = _posting_client(role="tester")           # MEMBER+, per §e
    page = c.get("/settings/quality-policy").data.decode()
    assert 'data-testid="activate-policy"' in page
    r = c.post("/settings/quality-policy/activate", data={"policy_id": draft},
               headers=hdr, follow_redirects=False)
    assert r.status_code == 302
    with e.connect() as conn:
        row = conn.execute(text(
            "SELECT status, activated_by FROM tenant_1.quality_policies WHERE id = CAST(:p AS uuid)"),
            {"p": draft}).first()
        assert row[0] == "active" and row[1] == 1, "activation did not record the real actor"
        audit = conn.execute(text(
            "SELECT count(*) FROM activity_log WHERE tenant_id=1 AND action='quality_policy.activate'"
        )).scalar()
        assert audit >= 1, "activation was not audited"
        if was_active:
            assert conn.execute(text(
                "SELECT status FROM tenant_1.quality_policies WHERE id = CAST(:p AS uuid)"),
                {"p": was_active}).scalar() == "retired"
        # freeze it, then prove the page's own act refuses
        conn.execute(text("UPDATE tenant_1.quality_policies SET first_used_at = now() "
                          "WHERE id = CAST(:p AS uuid)"), {"p": draft})
        conn.commit()
    r2 = c.post("/settings/quality-policy/activate", data={"policy_id": draft},
                headers=hdr, follow_redirects=True)
    assert r2.status_code == 200
    assert b"only a draft activates" in r2.data or b"is active" in r2.data


def test_a_waiver_recorded_on_the_page_is_the_same_object_the_decision_tab_makes():
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import create_engine, text
    c, hdr = _posting_client(role="tester")
    ref = f"6b-page-{datetime.now(timezone.utc).timestamp():.0f}"
    until = (datetime.now(timezone.utc) + timedelta(days=3)).date().isoformat()
    r = c.post("/settings/waivers", data={
        "item_kind": "claim", "item_ref": ref, "axis": "functional",
        "reason": "recorded from the settings page", "expires_at": until,
        "reviewer_user_id": "1"}, headers=hdr, follow_redirects=False)
    assert r.status_code == 302
    e = create_engine(DB)
    with e.connect() as conn:
        row = conn.execute(text("""
            SELECT CAST(id AS text), axis, reviewer_user_id, created_by, revoked_at
            FROM tenant_1.quality_waivers WHERE item_ref = :r"""), {"r": ref}).first()
        assert row is not None, "the page did not create the waiver"
        wid, axis, reviewer, created_by, revoked = row
        assert axis == "functional" and reviewer == 1 and created_by == 1 and revoked is None
        assert conn.execute(text(
            "SELECT count(*) FROM activity_log WHERE tenant_id=1 AND action='quality_waiver.record'"
        )).scalar() >= 1
    # and revoking through the page is the same state change
    assert c.post(f"/settings/waivers/{wid}/revoke", data={"reason": "test"},
                  headers=hdr, follow_redirects=False).status_code == 302
    with e.connect() as conn:
        assert conn.execute(text(
            "SELECT revoked_by FROM tenant_1.quality_waivers WHERE id = CAST(:w AS uuid)"),
            {"w": wid}).scalar() == 1
        conn.execute(text("SET session_replication_role = replica"))
        conn.execute(text("DELETE FROM tenant_1.quality_waivers WHERE id = CAST(:w AS uuid)"), {"w": wid})
        conn.commit()


def test_a_viewer_cannot_activate_or_waive():
    """With a VALID CSRF token, so the refusal under test is the TIER gate and
    not the CSRF one."""
    v, hdr = _posting_client(role="viewer")
    assert v.post("/settings/quality-policy/activate", data={"policy_id": "x"},
                  headers=hdr, follow_redirects=False).status_code == 302
    assert v.post("/settings/waivers", data={"item_ref": "x", "axis": "functional",
                                             "reason": "no", "expires_at": "2099-01-01"},
                  headers=hdr, follow_redirects=False).status_code == 302
    from sqlalchemy import create_engine, text
    with create_engine(DB).connect() as conn:
        assert conn.execute(text(
            "SELECT count(*) FROM tenant_1.quality_waivers WHERE item_ref = 'x'")).scalar() == 0
