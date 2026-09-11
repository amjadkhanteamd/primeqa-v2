"""Step 5 pages (LLD_STEP_5 §b/§f): the decision tab renders the quality
decision card (the live preview, or the ruling-3 refusal when no policy is
active) and the read-only Settings policy page renders the seed with its
rules and the authoring note. Gated like test_report_pages (REPORT_PAGES=1 +
scratch, its own pytest invocation)."""
from __future__ import annotations

import os

import pytest

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
_ENABLED = os.environ.get("REPORT_PAGES") == "1" and bool(DB)
pytestmark = [pytest.mark.skipif(not _ENABLED, reason="set REPORT_PAGES=1 + S3A3_TEST_DATABASE_URL; run as its own pytest invocation")]
if _ENABLED:
    os.environ["DATABASE_URL"] = DB
    os.environ.setdefault("JWT_SECRET", "a" * 64)


def _client(role="tester"):
    import jwt as pyjwt
    from primeqa.app import app
    token = pyjwt.encode({"sub": "1", "tenant_id": 1, "role": role, "email": "amjad.khan@teamd.co.in"},
                         os.environ["JWT_SECRET"], algorithm="HS256")
    c = app.test_client(); c.set_cookie("access_token", token)
    return c


@pytest.fixture
def release():
    from sqlalchemy import create_engine, text
    e = create_engine(DB)
    with e.begin() as c:
        rid = c.execute(text("INSERT INTO releases (tenant_id, name, version_tag, status, created_by) "
                             "VALUES (1, 'Step 5 pages release', 'v-s5p', 'in_progress', 1) RETURNING id")).scalar()
    yield rid
    with e.begin() as c:
        c.execute(text("DELETE FROM release_decisions WHERE release_id = :r"), {"r": rid})
        c.execute(text("DELETE FROM releases WHERE id = :r"), {"r": rid})


def test_decision_tab_renders_the_quality_decision_card(release):
    # admin: the MEMBER environment picker joins public.group_environments, a table the
    # scratch copy lacks (the same limitation the Step 4 shoot worked under)
    c = _client(role="admin")
    r = c.get(f"/releases/{release}?tab=decision", follow_redirects=False)
    assert r.status_code == 200
    html = r.data.decode()
    assert 'data-testid="quality-decision-card"' in html
    # either the preview (an active policy on scratch) or the ruling-3 refusal — never a silent default
    assert ('data-testid="quality-policy-line"' in html) or ("No active quality policy" in html)
    assert 'data-testid="waiver-form"' in html and "never an adjudication" in html


def test_settings_quality_policy_page_is_read_only_and_names_the_cli(release):
    c = _client(role="admin")
    r = c.get("/settings/quality-policy", follow_redirects=False)
    assert r.status_code == 200
    html = r.data.decode()
    assert 'data-testid="quality-policy-page"' in html and "Authoring is CLI in v1" in html
    assert "Plimsol default" in html and html.count('data-testid="policy-rule"') in (0, 10)
    assert "failure_present" in html
    # a MEMBER is redirected (Admin tier)
    assert _client(role="tester").get("/settings/quality-policy", follow_redirects=False).status_code == 302
