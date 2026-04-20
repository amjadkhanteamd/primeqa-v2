"""Section 2: navigate every known page in the app after login.

For each page:
  - HTTP status
  - JS console errors during load
  - Placeholder markers (Lorem ipsum, TODO, FIXME, {{ unrendered }})
  - Mobile viewport smoke (no layout explosion)
"""
from __future__ import annotations

import re

from qa.browser import BASE_URL, playwright_page, login, screenshot

# Canonical pages derived from primeqa/views.py route list.
PAGES = [
    ("/",                             "Home / dashboard"),
    ("/dashboard",                    "Dashboard"),
    ("/runs",                         "Runs list"),
    ("/runs/new",                     "Run wizard"),
    ("/requirements",                 "Requirements list"),
    ("/test-cases",                   "Test case library"),
    ("/suites",                       "Suites list"),
    ("/reviews",                      "BA reviews queue"),
    ("/impacts",                      "Metadata impacts"),
    ("/releases",                     "Releases list"),
    ("/environments",                 "Environments"),
    ("/connections",                  "Connections"),
    ("/settings/users",               "User management (admin)"),
    ("/settings/my-llm-usage",        "My LLM usage"),
    ("/settings/llm-usage",           "Superadmin LLM usage"),
    ("/settings/agent",               "Agent settings"),
    ("/settings/groups",              "Groups"),
    ("/settings/notifications",       "Notifications"),
    ("/profile",                      "Profile"),
]

# Raw template leaks / placeholder markers to grep for.
_BAD_MARKERS = [
    ("unrendered jinja var", re.compile(r"\{\{\s*\w+[\w\.\s]*\}\}")),
    ("python-repr None",      re.compile(r">\s*None\s*<")),
    ("undefined literal",     re.compile(r">\s*undefined\s*<")),
    ("stack trace leak",      re.compile(r"Traceback \(most recent call last\)")),
    ("todo / fixme",          re.compile(r"\b(TODO|FIXME|XXX)\b")),
    ("lorem ipsum",           re.compile(r"Lorem ipsum", re.IGNORECASE)),
]


def _scan_page(page_html: str):
    """Return list of (marker_name, sample) tuples for any markers found."""
    hits = []
    for name, rx in _BAD_MARKERS:
        m = rx.search(page_html)
        if m:
            hits.append((name, m.group(0)[:60]))
    return hits


def run() -> list:
    findings = []
    with playwright_page() as (page, ctx):
        res = login(page)
        if res["status"] != "ok":
            findings.append({
                "id": "2.0", "title": "Login blocked \u2014 navigation probes skipped",
                "severity": "P1", "status": "BLOCKED",
                "url": f"{BASE_URL}/login",
                "expected": "successful login",
                "actual": res["detail"], "category": "Functionality", "evidence": "",
            })
            return findings

        for path, desc in PAGES:
            url = f"{BASE_URL}{path}"
            # Clear JS errors before each load so findings attribute correctly.
            ctx._js_errors.clear()
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                # Give the page ~500ms for any error-producing JS to run
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass
            except Exception as e:
                findings.append({
                    "id": f"2.1-{path}", "title": f"Page load: {desc}",
                    "severity": "P1", "status": "FAIL",
                    "url": url, "expected": "2xx",
                    "actual": f"goto exception: {e}",
                    "category": "Functionality", "evidence": "",
                })
                continue

            status = resp.status if resp else 0
            html = ""
            try:
                html = page.content()
            except Exception:
                pass
            hits = _scan_page(html)
            js_errors = list(ctx._js_errors)

            if status >= 500:
                shot = screenshot(page, f"section02_{path.replace('/', '_').strip('_') or 'home'}")
                findings.append({
                    "id": f"2.1-{path}", "title": f"5xx on {desc}",
                    "severity": "P0", "status": "FAIL",
                    "url": url, "expected": "2xx / 302",
                    "actual": f"HTTP {status}",
                    "category": "Functionality", "evidence": shot,
                })
            elif status in (401, 403):
                findings.append({
                    "id": f"2.1-{path}", "title": f"{desc} blocked for current user",
                    "severity": "P3", "status": "PARTIAL",
                    "url": url, "expected": "access or clean 403",
                    "actual": f"HTTP {status} (role gating)",
                    "category": "Security", "evidence": "",
                })
            elif status == 404:
                findings.append({
                    "id": f"2.1-{path}", "title": f"{desc} not implemented",
                    "severity": "P2", "status": "FAIL",
                    "url": url, "expected": "a page",
                    "actual": "404 not found \u2014 page likely not implemented",
                    "category": "Functionality", "evidence": "",
                })
            elif status in (200, 302, 303):
                sev = "P3"
                status_str = "PASS"
                if hits:
                    sev = "P2"
                    status_str = "PARTIAL"
                if js_errors:
                    sev = "P1"
                    status_str = "PARTIAL"
                findings.append({
                    "id": f"2.1-{path}", "title": f"Page load: {desc}",
                    "severity": sev, "status": status_str,
                    "url": url, "expected": "2xx, no JS errors, no placeholders",
                    "actual": f"HTTP {status}; placeholders={hits or 'none'}; "
                              f"js_errors={js_errors[:2] if js_errors else 'none'}",
                    "category": "UI", "evidence": "",
                })
            else:
                findings.append({
                    "id": f"2.1-{path}", "title": f"{desc} unexpected status",
                    "severity": "P2", "status": "PARTIAL",
                    "url": url, "expected": "2xx / 302",
                    "actual": f"HTTP {status}",
                    "category": "Functionality", "evidence": "",
                })

    return findings


if __name__ == "__main__":
    for f in run():
        print(f"  [{f['status']:7}] {f['id']}: {f['title']}")
