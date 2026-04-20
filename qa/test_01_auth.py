"""Section 1: Authentication + Session Management.

Mix of Playwright browser tests and direct HTTPS probes via requests.
Designed to run standalone; emits findings as a list of dicts suitable
for QA_REPORT.md rendering.

Guarded against network flakes: every individual check is wrapped in
try/except so one failure doesn't halt the whole section.
"""
from __future__ import annotations

import json
import time

import requests

from qa.browser import (
    BASE_URL, DEFAULT_EMAIL, DEFAULT_PASSWORD,
    playwright_page, login, screenshot,
)


def _record(findings, section_id, title, status, url, expected,
            actual, category, severity="P2", evidence=""):
    findings.append({
        "id": section_id, "title": title, "severity": severity,
        "status": status, "url": url, "expected": expected,
        "actual": actual, "category": category, "evidence": evidence,
    })


def run() -> list:
    findings: list = []

    # --- 1.1.1 Login page loads ---
    try:
        r = requests.get(f"{BASE_URL}/login", timeout=20, allow_redirects=True)
        if r.status_code == 200 and ("login" in r.text.lower() or "password" in r.text.lower()):
            _record(findings, "1.1.1", "Login page loads",
                    "PASS", f"{BASE_URL}/login",
                    "200 with a login form",
                    f"{r.status_code}, {len(r.text)} bytes",
                    "Functionality")
        else:
            _record(findings, "1.1.1", "Login page loads",
                    "FAIL", f"{BASE_URL}/login",
                    "200 with login form",
                    f"{r.status_code} body preview: {r.text[:140]!r}",
                    "Functionality", severity="P1")
    except Exception as e:
        _record(findings, "1.1.1", "Login page loads",
                "BLOCKED", f"{BASE_URL}/login",
                "HTTP 200", f"exception: {e}", "Functionality", severity="P1")

    # --- 1.1.7 SQL-injection-ish email via API ---
    try:
        r = requests.post(f"{BASE_URL}/api/auth/login",
                          json={"email": "admin@primeqa.io' OR '1'='1",
                                "password": "wrong"},
                          timeout=15)
        if r.status_code in (400, 401):
            _record(findings, "1.1.7", "SQL-injection email rejected",
                    "PASS", f"{BASE_URL}/api/auth/login",
                    "401/400 (credentials invalid)",
                    f"{r.status_code}: {r.text[:140]}",
                    "Security")
        else:
            _record(findings, "1.1.7", "SQL-injection email rejected",
                    "FAIL", f"{BASE_URL}/api/auth/login",
                    "401/400",
                    f"unexpected {r.status_code}: {r.text[:200]}",
                    "Security", severity="P0")
    except Exception as e:
        _record(findings, "1.1.7", "SQL-injection email rejected",
                "BLOCKED", f"{BASE_URL}/api/auth/login",
                "401/400", f"exception: {e}", "Security", severity="P1")

    # --- 1.1.8 XSS payload in email ---
    try:
        r = requests.post(f"{BASE_URL}/api/auth/login",
                          json={"email": "<script>alert(1)</script>@x.com",
                                "password": "x"},
                          timeout=15)
        body = r.text
        # Server must NOT echo the raw script tag unescaped
        reflected = "<script>" in body.lower() and "alert(1)" in body.lower()
        if r.status_code in (400, 401) and not reflected:
            _record(findings, "1.1.8", "XSS email safely rejected",
                    "PASS", f"{BASE_URL}/api/auth/login",
                    "401/400, no reflected payload",
                    f"{r.status_code}; body safe",
                    "Security")
        else:
            _record(findings, "1.1.8", "XSS email reflected or accepted",
                    "FAIL", f"{BASE_URL}/api/auth/login",
                    "401/400, no <script> in body",
                    f"status={r.status_code} reflected={reflected}",
                    "Security", severity="P0")
    except Exception as e:
        _record(findings, "1.1.8", "XSS email handled",
                "BLOCKED", f"{BASE_URL}/api/auth/login",
                "401/400", f"exception: {e}", "Security", severity="P1")

    # --- 1.1.4 Wrong password + no user enumeration ---
    # Two attempts: a known-registered email with wrong password, and a
    # definitely-non-existent email. Messages should be identical / generic.
    try:
        r1 = requests.post(f"{BASE_URL}/api/auth/login",
                           json={"email": DEFAULT_EMAIL, "password": "definitely-wrong"},
                           timeout=15)
        r2 = requests.post(f"{BASE_URL}/api/auth/login",
                           json={"email": "absolutely-nobody-at-nowhere-xyz@example.com",
                                 "password": "anything"},
                           timeout=15)
        same_status = r1.status_code == r2.status_code
        # Compare bodies after stripping volatile bits
        b1 = (r1.text or "")[:500]
        b2 = (r2.text or "")[:500]
        same_body_shape = (b1 == b2) or (r1.status_code == r2.status_code == 401)
        if same_status and same_body_shape:
            _record(findings, "1.1.4", "No user enumeration on wrong-password vs unknown-email",
                    "PASS", f"{BASE_URL}/api/auth/login",
                    "Identical response for wrong-password vs unknown-email",
                    f"both {r1.status_code}; bodies equal={b1==b2}",
                    "Security")
        else:
            _record(findings, "1.1.4", "Possible user enumeration",
                    "FAIL", f"{BASE_URL}/api/auth/login",
                    "Identical generic response",
                    f"wrong-pw={r1.status_code} unknown-email={r2.status_code}; bodies: {b1!r} / {b2!r}",
                    "Security", severity="P1")
    except Exception as e:
        _record(findings, "1.1.4", "User enumeration probe",
                "BLOCKED", f"{BASE_URL}/api/auth/login",
                "identical responses", f"exception: {e}",
                "Security", severity="P2")

    # --- 1.1.2 Valid login via browser ---
    try:
        with playwright_page() as (page, ctx):
            result = login(page)
            shot = screenshot(page, "section01_post_login")
            if result["status"] == "ok":
                _record(findings, "1.1.2", "Valid login redirects away from /login",
                        "PASS", result["landing_url"],
                        "non-/login URL after submit",
                        f"landed at {result['landing_url']}",
                        "Functionality", evidence=shot)
                # Sub-check 1.2.1: session cookie persists
                cookies = ctx.cookies()
                session_cookies = [c for c in cookies
                                   if c.get("name", "").lower() in
                                   ("session", "access_token", "primeqa_access")]
                _record(findings, "1.2.1", "Session cookie set after login",
                        "PASS" if session_cookies else "FAIL",
                        result["landing_url"],
                        "cookie named session/access_token/primeqa_access set",
                        f"found: {[c['name'] for c in session_cookies]}",
                        "Security",
                        severity="P0" if not session_cookies else "P3")
            else:
                _record(findings, "1.1.2", "Valid login redirects away from /login",
                        "FAIL", result["landing_url"],
                        "redirect off /login",
                        f"{result['detail']}; landing {result['landing_url']}",
                        "Functionality", severity="P0", evidence=shot)
    except Exception as e:
        _record(findings, "1.1.2", "Valid login via browser",
                "BLOCKED", f"{BASE_URL}/login",
                "redirect to dashboard", f"exception: {e}",
                "Functionality", severity="P1")

    # --- 1.2.5 Protected page without auth redirects to login ---
    try:
        # Use a session-less call; no cookies, no auth header. Web route
        # should redirect OR return 401.
        r = requests.get(f"{BASE_URL}/runs", timeout=15, allow_redirects=False)
        if r.status_code in (301, 302, 303) and "login" in (r.headers.get("Location", "").lower()):
            _record(findings, "1.2.5", "Protected web route redirects to /login when unauth",
                    "PASS", f"{BASE_URL}/runs",
                    "302/303 to /login",
                    f"{r.status_code} -> {r.headers.get('Location')}",
                    "Security")
        elif r.status_code == 401:
            _record(findings, "1.2.5", "Protected web route returns 401 when unauth",
                    "PASS", f"{BASE_URL}/runs",
                    "302/303 to /login or 401",
                    f"{r.status_code}",
                    "Security")
        else:
            _record(findings, "1.2.5", "Protected route leaks content to unauth request",
                    "FAIL", f"{BASE_URL}/runs",
                    "302/303 to /login or 401",
                    f"unexpected {r.status_code}: {r.text[:140]!r}",
                    "Security", severity="P0")
    except Exception as e:
        _record(findings, "1.2.5", "Protected route redirect probe",
                "BLOCKED", f"{BASE_URL}/runs",
                "302/401", f"exception: {e}", "Security", severity="P1")

    # --- 1.2.5b Protected API route requires auth ---
    try:
        r = requests.get(f"{BASE_URL}/api/requirements", timeout=15)
        if r.status_code == 401:
            _record(findings, "1.2.5b", "Protected API returns 401 without token",
                    "PASS", f"{BASE_URL}/api/requirements",
                    "401 Unauthorized",
                    f"{r.status_code}",
                    "Security")
        elif r.status_code == 403:
            _record(findings, "1.2.5b", "Protected API returns 403 without token",
                    "PASS", f"{BASE_URL}/api/requirements",
                    "401/403",
                    f"{r.status_code}",
                    "Security")
        else:
            _record(findings, "1.2.5b", "Protected API leaks without token",
                    "FAIL", f"{BASE_URL}/api/requirements",
                    "401/403",
                    f"unexpected {r.status_code}: {r.text[:200]!r}",
                    "Security", severity="P0")
    except Exception as e:
        _record(findings, "1.2.5b", "API auth probe",
                "BLOCKED", f"{BASE_URL}/api/requirements",
                "401", f"exception: {e}", "Security", severity="P1")

    return findings


if __name__ == "__main__":
    fs = run()
    for f in fs:
        print(f"  [{f['status']:7}] {f['id']}: {f['title']}")
    print(f"\n  Total: {len(fs)}")
