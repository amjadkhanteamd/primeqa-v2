"""Report-slice PAGE tests — the three pages through the real Flask app.

Gated on REPORT_PAGES=1 AND S3A3_TEST_DATABASE_URL, and run as its OWN
pytest invocation: importing ``primeqa.app`` binds the app's engines to
``DATABASE_URL``, so this module points DATABASE_URL at the scratch DSN
BEFORE that import. Mixing it into a session whose other modules bound
engines elsewhere would read the wrong database — hence the separate
invocation (documented in VERIFICATION_REPORT_SLICE.md)."""
from __future__ import annotations

import os

import pytest

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
_ENABLED = os.environ.get("REPORT_PAGES") == "1" and bool(DB)
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _ENABLED, reason="set REPORT_PAGES=1 + "
                       "S3A3_TEST_DATABASE_URL; run as its own pytest "
                       "invocation"),
]

if _ENABLED:
    os.environ["DATABASE_URL"] = DB
    os.environ.setdefault("JWT_SECRET", "a" * 64)

B1 = "471a9c35-13d6-466a-bd7b-38b809f9aac6"
P1 = "c70fa8e6-888d-4a6f-9087-f18fd8ef3196"


def _client(role="admin"):
    import jwt as pyjwt

    from primeqa.app import app
    token = pyjwt.encode({"sub": "1", "tenant_id": 1, "role": role,
                          "email": "amjad.khan@teamd.co.in"},
                         os.environ["JWT_SECRET"], algorithm="HS256")
    c = app.test_client()
    c.set_cookie("access_token", token)
    return c


def test_a_member_sees_all_three_pages_and_a_viewer_is_redirected():
    c = _client()
    for path, marker in (
            ("/ui-report", b"UI conformance runs"),
            (f"/ui-report/runs/{B1}?standard=WCAG22", b"Verdict listing"),
            (f"/ui-report/compare?baseline={P1}&candidate={B1}",
             b"Release comparison"),
            (f"/ui-report/coverage?job={B1}", b"N of M, per standard")):
        r = c.get(path)
        assert r.status_code == 200, (path, r.status_code)
        assert marker in r.data, path
    v = _client(role="viewer")
    for path in ("/ui-report", f"/ui-report/runs/{B1}"):
        r = v.get(path)
        assert r.status_code == 302 and r.headers["Location"].endswith("/")


def test_b_run_page_carries_the_honesty_header_and_fail_rows():
    c = _client()
    r = c.get(f"/ui-report/runs/{B1}?standard=WCAG22&verdict=FAIL")
    assert r.status_code == 200
    body = r.data.decode()
    assert "ratified_catalogue" in body and "21 of 55" in body
    assert "PLM-A11Y-071" in body and "PLM-A11Y-030" in body
    assert "axe-core" in body
    # the signed URL is minted only by the on-demand fragment — never
    # inlined into the page render
    assert "X-Amz-Signature" not in body


def test_c_compare_page_groups_the_taxonomy_and_names_the_drift():
    c = _client()
    r = c.get(f"/ui-report/compare?baseline={P1}&candidate={B1}")
    body = r.data.decode()
    assert "NOT_COMPARABLE" in body and "142" in body
    assert "STILL_FAILING" in body and "NEW_CLAIM" in body
    assert "Tool dimension moved" in body
    assert "catalogue_release_id" in body
    # the unstored direction renders the honest empty state
    r2 = c.get(f"/ui-report/compare?baseline={B1}&candidate={P1}")
    assert b"no recorded comparison" in r2.data


def test_d_coverage_page_shows_n_of_m_and_the_refusal_panel():
    c = _client()
    r = c.get(f"/ui-report/coverage?job={B1}")
    body = r.data.decode()
    for frag in ("21", "55", "50", "38", "CUSTOM:acme",
                 "What we cannot test for you", "NOT_COVERED"):
        assert frag in body, frag


def test_e_evidence_fragment_degrades_honestly_and_is_member_gated():
    c = _client()
    surface = ("orgfarm-4399654d2d-dev-ed.develop.my.site.com"
               "%7C%2Fs%7Ccustomer%7C-%7C-")
    r = c.get(f"/ui-report/evidence?job={B1}&surface={surface}")
    assert r.status_code == 200
    assert (b"not configured" in r.data) or (b"http" in r.data)
    v = _client(role="viewer")
    assert v.get(f"/ui-report/evidence?job={B1}&surface={surface}"
                 ).status_code == 302
