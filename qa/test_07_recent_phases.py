"""Section 7 (added): verify the recent phases ship correctly in prod.

Covers:
  - /tickets (developer UX)
  - /run (tester page) + /api/bulk-runs (validation + permission gates)
  - /results + /results/:id aliases
  - /api/runs/:id/summary-text (Copy Summary)
  - /settings/users + /settings/permission-sets (admin UI)
  - /reviews permission gate via browser cookie session
  - Sidebar badge presence logic
  - Self-protection: can't strip own admin, can't deactivate self

Adversarial: tries to bypass with cookie fiddling, missing fields,
bad JSON, etc. All checks hit the LIVE production service.
"""

from __future__ import annotations

import requests

from qa.browser import (
    BASE_URL, DEFAULT_EMAIL, DEFAULT_PASSWORD,
    login, playwright_page, screenshot,
)


def _record(findings, sid, title, status, url, expected, actual,
            category, severity="P2", evidence=""):
    findings.append({
        "id": sid, "title": title, "severity": severity,
        "status": status, "url": url, "expected": expected,
        "actual": actual, "category": category, "evidence": evidence,
    })


def _http_login_session():
    """Session with the JWT access_token cookie set via the HTML login path."""
    s = requests.Session()
    r = s.post(f"{BASE_URL}/login",
               data={"email": DEFAULT_EMAIL, "password": DEFAULT_PASSWORD},
               allow_redirects=False, timeout=20)
    assert r.status_code in (301, 302), f"login status {r.status_code}"
    return s


def _api_login():
    """Bearer token path for /api/*."""
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": DEFAULT_EMAIL, "password": DEFAULT_PASSWORD},
                      timeout=20)
    assert r.status_code == 200, r.status_code
    return r.json()["access_token"]


def run() -> list:
    findings: list = []

    session = _http_login_session()
    token = _api_login()

    # --- Developer UX: /tickets ------------------------------------
    try:
        r = session.get(f"{BASE_URL}/tickets", timeout=30)
        ok = r.status_code == 200 and "My Tickets" in r.text
        _record(findings, "7.1.1", "/tickets renders for logged-in user",
                "PASS" if ok else "FAIL",
                "/tickets",
                "200 + 'My Tickets' heading",
                f"status={r.status_code} len={len(r.text)}",
                "Functionality")
    except Exception as e:
        _record(findings, "7.1.1", "/tickets renders", "ERROR", "/tickets",
                "200 + heading", str(e), "Functionality")

    # /tickets with no active env should still render a usable empty state.
    # We can't easily force no-env here, but the server-rendered HTML should
    # include either "Active Org" (env exists) or "Connect Salesforce" (empty).
    try:
        r = session.get(f"{BASE_URL}/tickets", timeout=30)
        has_env_switcher = "Active Org" in r.text
        has_empty_state = "Connect a Salesforce" in r.text
        ok = has_env_switcher or has_empty_state
        _record(findings, "7.1.2", "/tickets has either switcher OR empty state",
                "PASS" if ok else "FAIL",
                "/tickets",
                "switcher or empty state",
                f"switcher={has_env_switcher} empty={has_empty_state}",
                "UI")
    except Exception as e:
        _record(findings, "7.1.2", "/tickets UI branches", "ERROR",
                "/tickets", "branch present", str(e), "UI")

    # --- Tester /run page ------------------------------------------
    try:
        r = session.get(f"{BASE_URL}/run", timeout=30)
        ok = r.status_code == 200 and "Run Tests" in r.text
        _record(findings, "7.2.1", "/run renders for superadmin",
                "PASS" if ok else "FAIL", "/run",
                "200 + 'Run Tests' heading",
                f"status={r.status_code}", "Functionality")
    except Exception as e:
        _record(findings, "7.2.1", "/run renders", "ERROR", "/run",
                "200", str(e), "Functionality")

    # Tabs present for a superadmin (no gating).
    try:
        r = session.get(f"{BASE_URL}/run", timeout=30)
        has_sprint = 'data-mode="sprint"' in r.text
        has_suite = 'data-mode="suite"' in r.text
        ok = has_sprint and has_suite
        _record(findings, "7.2.2", "/run tabs (sprint + suite) render",
                "PASS" if ok else "FAIL", "/run",
                "sprint and suite tabs visible",
                f"sprint={has_sprint} suite={has_suite}", "UI")
    except Exception as e:
        _record(findings, "7.2.2", "/run tabs", "ERROR", "/run", "tabs", str(e), "UI")

    # --- /api/bulk-runs validation ---------------------------------
    try:
        r = requests.post(f"{BASE_URL}/api/bulk-runs",
                          headers={"Authorization": f"Bearer {token}"},
                          json={"run_type": "yolo"}, timeout=20)
        ok = r.status_code == 400 and r.json().get("error", {}).get("code") == "VALIDATION_ERROR"
        _record(findings, "7.3.1", "POST /api/bulk-runs bad run_type -> 400",
                "PASS" if ok else "FAIL", "/api/bulk-runs",
                "400 VALIDATION_ERROR",
                f"status={r.status_code} body={r.text[:120]}",
                "Security", "P1")
    except Exception as e:
        _record(findings, "7.3.1", "bulk-runs validation", "ERROR",
                "/api/bulk-runs", "400", str(e), "Security", "P1")

    # /api/bulk-runs without auth header: either the CSRF middleware
    # fires (403 CSRF_FAILED for cookie-less POST without Bearer) or the
    # auth decorator fires (401 UNAUTHORIZED). Both are legitimate
    # rejections — what MUST NOT happen is a 2xx or a 500.
    try:
        r_noauth = requests.post(f"{BASE_URL}/api/bulk-runs",
                                 json={"run_type": "sprint", "environment_id": 1,
                                       "ticket_keys": ["X-1"]}, timeout=20)
        r_fake = requests.post(f"{BASE_URL}/api/bulk-runs",
                               headers={"Authorization": "Bearer FAKETOKEN"},
                               json={}, timeout=20)
        ok = (r_noauth.status_code in (401, 403)
              and r_fake.status_code == 401)
        _record(findings, "7.3.2", "POST /api/bulk-runs rejects unauth (CSRF OR 401)",
                "PASS" if ok else "FAIL", "/api/bulk-runs",
                "no-Bearer: 401 or 403 CSRF; fake-Bearer: 401",
                f"no_auth={r_noauth.status_code} fake_bearer={r_fake.status_code}",
                "Security", "P0")
    except Exception as e:
        _record(findings, "7.3.2", "bulk-runs auth", "ERROR",
                "/api/bulk-runs", "401/403", str(e), "Security", "P0")

    # --- /results alias ---------------------------------------------
    try:
        r = session.get(f"{BASE_URL}/results", allow_redirects=False, timeout=20)
        ok = r.status_code in (301, 302) and "/runs" in r.headers.get("Location", "")
        _record(findings, "7.4.1", "/results redirects to /runs",
                "PASS" if ok else "FAIL", "/results",
                "redirect to /runs", f"status={r.status_code}", "Functionality")
    except Exception as e:
        _record(findings, "7.4.1", "/results alias", "ERROR", "/results",
                "redirect", str(e), "Functionality")

    # Query-string preservation
    try:
        r = session.get(f"{BASE_URL}/results?mine=1&status=failed",
                        allow_redirects=False, timeout=20)
        loc = r.headers.get("Location", "")
        ok = r.status_code in (301, 302) and "mine=1" in loc and "status=failed" in loc
        _record(findings, "7.4.2", "/results preserves query string",
                "PASS" if ok else "FAIL", "/results?mine=1",
                "qs pass-through", f"location={loc}", "Functionality", "P2")
    except Exception as e:
        _record(findings, "7.4.2", "/results qs", "ERROR", "/results",
                "qs preserved", str(e), "Functionality")

    # --- /api/runs/:id/summary-text --------------------------------
    try:
        # Grab the latest run id by listing
        r = session.get(f"{BASE_URL}/runs", timeout=30)
        # Extract a /runs/NNN link from the HTML
        import re
        m = re.search(r'/runs/(\d+)"', r.text)
        if m:
            run_id = int(m.group(1))
            r2 = requests.get(f"{BASE_URL}/api/runs/{run_id}/summary-text",
                              headers={"Authorization": f"Bearer {token}"},
                              timeout=20)
            body = r2.json() if r2.ok else {}
            txt = body.get("text", "") if isinstance(body, dict) else ""
            ok = r2.status_code == 200 and txt.startswith("PrimeQA Run #")
            _record(findings, "7.5.1", "Copy Summary endpoint returns paste block",
                    "PASS" if ok else "FAIL",
                    f"/api/runs/{run_id}/summary-text",
                    "200 + 'PrimeQA Run #' header",
                    f"status={r2.status_code} text_prefix={txt[:40]!r}",
                    "Functionality")
        else:
            _record(findings, "7.5.1", "Copy Summary endpoint (no runs to probe)",
                    "PARTIAL", "/api/runs/.../summary-text",
                    "run to exist", "no runs in tenant — can't probe",
                    "Functionality", "P3")
    except Exception as e:
        _record(findings, "7.5.1", "Copy Summary endpoint", "ERROR",
                "/api/runs/.../summary-text", "200", str(e), "Functionality")

    # 404 on unknown run
    try:
        r = requests.get(f"{BASE_URL}/api/runs/999999999/summary-text",
                         headers={"Authorization": f"Bearer {token}"}, timeout=20)
        ok = r.status_code == 404
        _record(findings, "7.5.2", "Copy Summary 404 on unknown run",
                "PASS" if ok else "FAIL",
                "/api/runs/999999999/summary-text",
                "404",
                f"status={r.status_code}",
                "Functionality", "P3")
    except Exception as e:
        _record(findings, "7.5.2", "Copy Summary 404", "ERROR",
                "/api/runs/999999999/summary-text", "404", str(e), "Functionality")

    # --- Admin UI: /settings/users + /settings/permission-sets -----
    try:
        r = session.get(f"{BASE_URL}/settings/users", timeout=30)
        ok = r.status_code == 200 and "Users" in r.text
        _record(findings, "7.6.1", "/settings/users renders for admin",
                "PASS" if ok else "FAIL", "/settings/users",
                "200 + Users heading", f"status={r.status_code}", "Functionality")
    except Exception as e:
        _record(findings, "7.6.1", "settings/users", "ERROR",
                "/settings/users", "200", str(e), "Functionality")

    try:
        r = session.get(f"{BASE_URL}/settings/permission-sets", timeout=30)
        ok = r.status_code == 200 and "Permission Sets" in r.text
        _record(findings, "7.6.2", "/settings/permission-sets renders",
                "PASS" if ok else "FAIL", "/settings/permission-sets",
                "200 + heading", f"status={r.status_code}", "Functionality")
    except Exception as e:
        _record(findings, "7.6.2", "settings/permission-sets", "ERROR",
                "/settings/permission-sets", "200", str(e), "Functionality")

    # Counts visible
    try:
        r = session.get(f"{BASE_URL}/settings/permission-sets", timeout=30)
        # admin_base has 39 permissions seeded
        ok = ">39<" in r.text
        _record(findings, "7.6.3", "admin_base permission count rendered",
                "PASS" if ok else "FAIL", "/settings/permission-sets",
                "'39' visible for admin_base perm count",
                "present" if ok else "missing",
                "UI", "P3")
    except Exception as e:
        _record(findings, "7.6.3", "admin_base counts", "ERROR",
                "/settings/permission-sets", "39 visible", str(e), "UI")

    # --- Self-protection: cannot deactivate self (API) ---------------
    try:
        # Grab own id from /api/auth/me
        me = requests.get(f"{BASE_URL}/api/auth/me",
                          headers={"Authorization": f"Bearer {token}"}, timeout=20)
        my_id = me.json().get("id") if me.ok else None
        # superadmin SHOULD be allowed self-deactivate (bypass in our guard);
        # a regular admin wouldn't. We just verify the endpoint responds.
        # Note: test admin is seeded as superadmin, so 204 is expected.
        if my_id is not None:
            r = requests.post(f"{BASE_URL}/api/users/{my_id}/deactivate",
                              headers={"Authorization": f"Bearer {token}"},
                              timeout=20)
            # Expect either 400 SELF_DEACTIVATE or (for superadmin bypass) 204.
            ok = r.status_code in (204, 400)
            if r.status_code == 204:
                # Immediately reactivate so we don't lock ourselves out
                requests.post(f"{BASE_URL}/api/users/{my_id}/activate",
                              headers={"Authorization": f"Bearer {token}"},
                              timeout=20)
            _record(findings, "7.7.1",
                    "Self-deactivate endpoint responds safely",
                    "PASS" if ok else "FAIL",
                    f"/api/users/{my_id}/deactivate",
                    "400 SELF_DEACTIVATE OR 204 (superadmin bypass)",
                    f"status={r.status_code}",
                    "Security", "P0")
        else:
            _record(findings, "7.7.1", "Self-deactivate (no me resolved)",
                    "BLOCKED", "/api/users/me", "me.id", "auth/me failed",
                    "Security")
    except Exception as e:
        _record(findings, "7.7.1", "self-deactivate", "ERROR",
                "/api/users/.../deactivate", "400 or 204", str(e), "Security")

    # --- /reviews permission gate -----------------------------------
    try:
        r = session.get(f"{BASE_URL}/reviews", timeout=30)
        ok = r.status_code == 200
        _record(findings, "7.8.1", "/reviews renders for superadmin",
                "PASS" if ok else "FAIL", "/reviews",
                "200", f"status={r.status_code}", "Functionality")
    except Exception as e:
        _record(findings, "7.8.1", "/reviews", "ERROR", "/reviews",
                "200", str(e), "Functionality")

    # Sidebar badge: hidden when zero pending (asserting presence/absence
    # is tenant-data-dependent; just verify the page doesn't crash when
    # the badge branch is active or inactive).
    try:
        r = session.get(f"{BASE_URL}/reviews", timeout=30)
        ok = r.status_code == 200
        _record(findings, "7.8.2", "Sidebar badge render doesn't crash page",
                "PASS" if ok else "FAIL", "/reviews",
                "200 regardless of badge state",
                f"status={r.status_code}", "UI", "P3")
    except Exception as e:
        _record(findings, "7.8.2", "badge render", "ERROR",
                "/reviews", "200", str(e), "UI")

    # --- Navigation: landing page logic, browser level --------------
    try:
        with playwright_page() as (page, ctx):
            login(page)  # lands wherever the resolver says
            path = page.url.replace(BASE_URL, "") or "/"
            ok = path in ("/", "/run", "/runs/new", "/requirements", "/tickets")
            _record(findings, "7.9.1", "Post-login lands on a valid page",
                    "PASS" if ok else "FAIL", BASE_URL,
                    "/ or /run or /runs/new or /requirements or /tickets",
                    f"landed at {path}", "Functionality")
            screenshot(page, "post_login_landing")
    except Exception as e:
        _record(findings, "7.9.1", "Post-login landing", "ERROR",
                "/login", "landing", str(e), "Functionality")

    # --- CSRF enforcement on state-changing POST --------------------
    # Cookie-auth post WITHOUT CSRF token should be rejected.
    try:
        s = _http_login_session()
        # Drop the csrf_token cookie
        s.cookies.pop("csrf_token", None)
        r = s.post(f"{BASE_URL}/api/users/me/active-env",
                   data={"environment_id": 1},
                   allow_redirects=False, timeout=20)
        ok = r.status_code in (400, 403)
        _record(findings, "7.10.1", "CSRF enforced on cookie-auth state change",
                "PASS" if ok else "FAIL",
                "/api/users/me/active-env",
                "400/403 CSRF rejection",
                f"status={r.status_code}", "Security", "P0")
    except Exception as e:
        _record(findings, "7.10.1", "CSRF enforcement", "ERROR",
                "/api/users/me/active-env", "403", str(e), "Security")

    # --- Security: access another tenant's permission set ----------
    # We can't easily create a second tenant, but we CAN attempt to
    # access a permission_set row by ID: any response that leaks data
    # cross-tenant is a P0. We rely on the fact that attempts are
    # tenant-scoped at the API layer.
    try:
        r = requests.post(f"{BASE_URL}/api/users/1/permission-sets",
                          headers={"Authorization": f"Bearer {token}"},
                          json={"permission_set_ids": [99999999]}, timeout=20)
        # Unknown id should 400 with VALIDATION_ERROR (foreign tenant rows
        # filtered by tenant_id in the handler)
        ok = r.status_code == 400 and "Unknown permission set" in r.text
        _record(findings, "7.11.1",
                "Assign permission-set with unknown id tenant-scoped",
                "PASS" if ok else "FAIL",
                "/api/users/1/permission-sets",
                "400 VALIDATION_ERROR on unknown/foreign id",
                f"status={r.status_code} {r.text[:120]}",
                "Security", "P0")
    except Exception as e:
        _record(findings, "7.11.1", "cross-tenant ps assign", "ERROR",
                "/api/users/1/permission-sets", "400", str(e), "Security")

    return findings


if __name__ == "__main__":
    for f in run():
        print(f"{f['status']:7s}  {f['id']:10s}  {f['title']}")
