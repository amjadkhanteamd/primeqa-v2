"""Section 11: API direct testing + cross-tenant safety probes."""
from __future__ import annotations

import requests

from qa.browser import BASE_URL, DEFAULT_EMAIL, DEFAULT_PASSWORD


def _rec(findings, **kw):
    findings.append(kw)


def _login_api() -> str:
    """Return a JWT access token via /api/auth/login. None on failure."""
    try:
        r = requests.post(f"{BASE_URL}/api/auth/login",
                          json={"email": DEFAULT_EMAIL,
                                "password": DEFAULT_PASSWORD},
                          timeout=15)
        if r.ok:
            data = r.json()
            return data.get("access_token") or data.get("token")
    except Exception:
        pass
    return None


def run() -> list:
    findings: list = []
    token = _login_api()
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    # ---- 11.1.1 unauth probes on API ------------------------------------
    for path in ["/api/requirements", "/api/runs", "/api/test-cases",
                 "/api/releases", "/api/suites", "/api/auth/users"]:
        try:
            r = requests.get(f"{BASE_URL}{path}", timeout=15)
            status = r.status_code
            expected = status in (401, 403)
            _rec(findings,
                 id=f"11.1-unauth {path}",
                 title=f"Unauthenticated GET {path}",
                 severity="P0" if not expected else "P3",
                 status="PASS" if expected else "FAIL",
                 url=f"{BASE_URL}{path}",
                 expected="401 or 403",
                 actual=f"{status}: {r.text[:120]!r}",
                 category="Security")
        except Exception as e:
            _rec(findings, id=f"11.1-unauth {path}",
                 title=f"Unauthenticated GET {path}",
                 severity="P2", status="BLOCKED",
                 url=f"{BASE_URL}{path}", expected="401/403",
                 actual=f"exception: {e}", category="Security")

    if not token:
        _rec(findings, id="11.1.0",
             title="API login failed \u2014 skipping authenticated probes",
             severity="P1", status="BLOCKED",
             url=f"{BASE_URL}/api/auth/login",
             expected="access_token in body",
             actual="no token returned from POST /api/auth/login",
             category="Functionality")
        return findings

    # ---- 11.1.2 Wrong HTTP method -------------------------------------
    try:
        r = requests.get(f"{BASE_URL}/api/auth/login", timeout=15)
        status = r.status_code
        # Most frameworks return 405; Flask default is 405.
        ok = status in (405, 404)
        _rec(findings, id="11.1.2",
             title="GET on POST-only endpoint returns 405/404",
             severity="P3",
             status="PASS" if ok else "PARTIAL",
             url=f"{BASE_URL}/api/auth/login",
             expected="405 Method Not Allowed",
             actual=f"{status}: {r.text[:120]!r}",
             category="Functionality")
    except Exception as e:
        _rec(findings, id="11.1.2",
             title="GET on POST-only endpoint",
             severity="P2", status="BLOCKED",
             url=f"{BASE_URL}/api/auth/login",
             expected="405", actual=f"exception: {e}",
             category="Functionality")

    # ---- 11.1.3 Malformed JSON -----------------------------------------
    try:
        r = requests.post(f"{BASE_URL}/api/auth/login",
                          data="{not json at all",
                          headers={"Content-Type": "application/json"},
                          timeout=15)
        status = r.status_code
        ok = status in (400, 401)
        _rec(findings, id="11.1.3",
             title="Malformed JSON body rejected cleanly",
             severity="P2",
             status="PASS" if ok else "PARTIAL",
             url=f"{BASE_URL}/api/auth/login",
             expected="400 (bad request) or 401",
             actual=f"{status}: {r.text[:140]!r}",
             category="Functionality")
    except Exception as e:
        _rec(findings, id="11.1.3",
             title="Malformed JSON body",
             severity="P2", status="BLOCKED",
             url=f"{BASE_URL}/api/auth/login",
             expected="400", actual=f"exception: {e}",
             category="Functionality")

    # ---- 11.1.4 Non-existent IDs return 404 ----------------------------
    for path in ["/api/requirements/999999", "/api/runs/999999",
                 "/api/test-cases/999999"]:
        try:
            r = requests.get(f"{BASE_URL}{path}", headers=headers, timeout=15)
            status = r.status_code
            ok = status in (404, 403, 302)  # some web routes redirect for web views
            _rec(findings, id=f"11.1.4-{path}",
                 title=f"Non-existent id on {path}",
                 severity="P2" if status == 500 else "P3",
                 status="PASS" if ok else ("FAIL" if status == 500 else "PARTIAL"),
                 url=f"{BASE_URL}{path}",
                 expected="404 / 403",
                 actual=f"{status}: {r.text[:120]!r}",
                 category="Functionality")
        except Exception as e:
            _rec(findings, id=f"11.1.4-{path}",
                 title=f"Non-existent id on {path}",
                 severity="P2", status="BLOCKED",
                 url=f"{BASE_URL}{path}",
                 expected="404", actual=f"exception: {e}",
                 category="Functionality")

    # ---- 11.1.5 Empty body POST ----------------------------------------
    try:
        r = requests.post(f"{BASE_URL}/api/requirements",
                          headers={**headers, "Content-Type": "application/json"},
                          json={}, timeout=15)
        status = r.status_code
        ok = status in (400, 422, 405, 404)
        _rec(findings, id="11.1.5",
             title="POST /api/requirements with {} body",
             severity="P3" if ok else "P2",
             status="PASS" if ok else "PARTIAL",
             url=f"{BASE_URL}/api/requirements",
             expected="400/422/405",
             actual=f"{status}: {r.text[:160]!r}",
             category="Functionality")
    except Exception as e:
        _rec(findings, id="11.1.5",
             title="POST /api/requirements empty body",
             severity="P2", status="BLOCKED",
             url=f"{BASE_URL}/api/requirements",
             expected="400/422", actual=f"exception: {e}",
             category="Functionality")

    # ---- 11.2 Cross-tenant enumeration ----------------------------------
    # Fetch requirements to know MY tenant's IDs, then probe a high id that
    # is probably another tenant's (if multi-tenant).
    try:
        r = requests.get(f"{BASE_URL}/api/requirements", headers=headers, timeout=15)
        own_ids = []
        if r.ok:
            data = r.json()
            # Try common envelope shapes
            items = data.get("data") if isinstance(data, dict) else data
            if isinstance(items, list):
                own_ids = [row.get("id") for row in items if isinstance(row, dict)][:5]
        max_own = max(own_ids) if own_ids else 0

        probe_id = (max_own or 100) + 5000  # likely belongs to another tenant if one exists
        r2 = requests.get(f"{BASE_URL}/api/requirements/{probe_id}",
                          headers=headers, timeout=15)
        # Should return 404 (scoped) not 403 or data leak
        if r2.status_code == 404:
            _rec(findings, id="11.2.1",
                 title=f"Cross-tenant id (probed {probe_id}) returns 404 (tenant-scoped)",
                 severity="P0",
                 status="PASS",
                 url=f"{BASE_URL}/api/requirements/{probe_id}",
                 expected="404 (scoped query)",
                 actual=f"{r2.status_code}",
                 category="Security")
        elif r2.status_code in (401, 403):
            _rec(findings, id="11.2.1",
                 title="Cross-tenant id responds auth-style (not data leak)",
                 severity="P1",
                 status="PARTIAL",
                 url=f"{BASE_URL}/api/requirements/{probe_id}",
                 expected="404",
                 actual=f"{r2.status_code} \u2014 not a leak but an inconsistent shape",
                 category="Security")
        elif r2.status_code == 200:
            _rec(findings, id="11.2.1",
                 title="CROSS-TENANT DATA LEAK",
                 severity="P0",
                 status="FAIL",
                 url=f"{BASE_URL}/api/requirements/{probe_id}",
                 expected="404",
                 actual=f"200 returned data for non-owner id: {r2.text[:200]!r}",
                 category="Security")
        else:
            _rec(findings, id="11.2.1",
                 title="Cross-tenant probe returned unexpected status",
                 severity="P1",
                 status="PARTIAL",
                 url=f"{BASE_URL}/api/requirements/{probe_id}",
                 expected="404",
                 actual=f"{r2.status_code}: {r2.text[:160]!r}",
                 category="Security")
    except Exception as e:
        _rec(findings, id="11.2.1", title="Cross-tenant probe",
             severity="P1", status="BLOCKED",
             url=f"{BASE_URL}/api/requirements/<probe>",
             expected="404", actual=f"exception: {e}", category="Security")

    # ---- 11.2.2 Negative id probe (common auth-bypass pattern) ---------
    try:
        r = requests.get(f"{BASE_URL}/api/requirements/-1",
                         headers=headers, timeout=15)
        if r.status_code in (404, 400):
            _rec(findings, id="11.2.2",
                 title="Negative id handled cleanly",
                 severity="P3",
                 status="PASS",
                 url=f"{BASE_URL}/api/requirements/-1",
                 expected="404/400",
                 actual=str(r.status_code),
                 category="Security")
        elif r.status_code == 500:
            _rec(findings, id="11.2.2",
                 title="Negative id 500's (unchecked DB exception)",
                 severity="P2",
                 status="FAIL",
                 url=f"{BASE_URL}/api/requirements/-1",
                 expected="404/400",
                 actual=f"500: {r.text[:140]!r}",
                 category="Functionality")
        else:
            _rec(findings, id="11.2.2",
                 title="Negative id handled",
                 severity="P3",
                 status="PARTIAL",
                 url=f"{BASE_URL}/api/requirements/-1",
                 expected="404/400",
                 actual=str(r.status_code),
                 category="Security")
    except Exception as e:
        _rec(findings, id="11.2.2",
             title="Negative id probe",
             severity="P2", status="BLOCKED",
             url=f"{BASE_URL}/api/requirements/-1",
             expected="404", actual=f"exception: {e}", category="Security")

    # ---- 11.1.6 Health endpoint (superadmin observability) --------------
    try:
        r = requests.get(f"{BASE_URL}/api/_internal/health",
                         headers=headers, timeout=15)
        if r.ok:
            _rec(findings, id="11.1.6",
                 title="/api/_internal/health responds",
                 severity="P3",
                 status="PASS",
                 url=f"{BASE_URL}/api/_internal/health",
                 expected="200 JSON",
                 actual=f"{r.status_code}: {r.text[:160]!r}",
                 category="Functionality")
        elif r.status_code in (401, 403):
            _rec(findings, id="11.1.6",
                 title="Health endpoint restricted",
                 severity="P3",
                 status="PASS",
                 url=f"{BASE_URL}/api/_internal/health",
                 expected="200 for superadmin or 401/403",
                 actual=str(r.status_code),
                 category="Security")
        else:
            _rec(findings, id="11.1.6",
                 title="Health endpoint unexpected status",
                 severity="P2",
                 status="PARTIAL",
                 url=f"{BASE_URL}/api/_internal/health",
                 expected="200 for admin",
                 actual=f"{r.status_code}: {r.text[:140]!r}",
                 category="Functionality")
    except Exception as e:
        _rec(findings, id="11.1.6",
             title="Health endpoint probe",
             severity="P3", status="BLOCKED",
             url=f"{BASE_URL}/api/_internal/health",
             expected="200", actual=f"exception: {e}", category="Functionality")

    return findings


if __name__ == "__main__":
    for f in run():
        print(f"  [{f['status']:7}] {f['id']}: {f['title']}")
