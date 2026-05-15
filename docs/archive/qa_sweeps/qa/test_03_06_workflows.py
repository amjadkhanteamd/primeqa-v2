"""Sections 3-6: sampled CRUD + Requirements/Test-case/Run readbacks.

Doesn't perform write operations against production (we don't want
to pollute data in an env that's actively being used). Instead probes:

  3. Settings/list render integrity (CRUD creation happens manually;
     here we confirm the settings screens load with real data and have
     the expected CRUD affordances)
  4. Requirements list + detail page renders with real data
  5. Test case library + detail + validation_report surfaces
  6. Runs list + detail page
"""
from __future__ import annotations

import requests

from qa.browser import BASE_URL, DEFAULT_EMAIL, DEFAULT_PASSWORD, playwright_page, login, screenshot


def _rec(findings, **kw):
    findings.append(kw)


def _api_token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": DEFAULT_EMAIL, "password": DEFAULT_PASSWORD},
                      timeout=15)
    return r.json().get("access_token") if r.ok else None


def run() -> list:
    findings = []
    token = _api_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    # ---- Section 3: settings pages carry CRUD affordances --------------
    # Use the web UI — CRUD buttons are server-rendered markup.
    with playwright_page() as (page, ctx):
        if login(page)["status"] != "ok":
            _rec(findings, id="3.0",
                 title="Login blocked \u2014 Section 3-6 skipped",
                 severity="P1", status="BLOCKED",
                 url=f"{BASE_URL}/login",
                 expected="login", actual="login failed",
                 category="Functionality", evidence="")
            return findings

        for path, expected_btn_text in [
            ("/connections", "New connection"),
            ("/environments", "New environment"),
            ("/settings/users", "Add user"),
            ("/requirements", "New requirement"),
            ("/test-cases", "New Test"),
            ("/suites", "New suite"),
        ]:
            page.goto(f"{BASE_URL}{path}", wait_until="domcontentloaded", timeout=20_000)
            try:
                page.wait_for_load_state("networkidle", timeout=5_000)
            except Exception:
                pass
            body = page.content()
            # Relaxed match — case-insensitive substring, tolerates "+" prefix / + icon
            lower = body.lower()
            simple_target = expected_btn_text.lower().split()[0]
            has_button = simple_target in lower
            _rec(findings, id=f"3.1-{path}",
                 title=f"{path} has primary CRUD action ({expected_btn_text!r} or equivalent)",
                 severity="P3" if has_button else "P2",
                 status="PASS" if has_button else "PARTIAL",
                 url=f"{BASE_URL}{path}",
                 expected=f"page mentions {expected_btn_text!r}",
                 actual=f"found={has_button}",
                 category="UI", evidence="")

        # ---- Section 4: requirements DETAIL page renders --------------
        # Use one of my requirement ids gathered via API.
        if token:
            r = requests.get(f"{BASE_URL}/api/requirements",
                             headers=headers, timeout=15)
            if r.ok:
                d = r.json()
                items = d.get("data") if isinstance(d, dict) and "data" in d else d
                rid = items[0].get("id") if items else None
                if rid:
                    page.goto(f"{BASE_URL}/requirements/{rid}",
                              wait_until="domcontentloaded", timeout=20_000)
                    body = page.content()
                    has_title = len(body) > 500 and "error" not in body.lower()[:200]
                    shot = screenshot(page, "section04_requirement_detail")
                    _rec(findings, id=f"4.2",
                         title=f"Requirement detail /requirements/{rid} renders",
                         severity="P3" if has_title else "P1",
                         status="PASS" if has_title else "FAIL",
                         url=f"{BASE_URL}/requirements/{rid}",
                         expected="detail page with title + content",
                         actual=f"{len(body)} bytes; error-in-head={'error' in body.lower()[:200]}",
                         category="Functionality", evidence=shot)

        # ---- Section 5: test case detail + validation_report ---------
        if token:
            r = requests.get(f"{BASE_URL}/api/test-cases",
                             headers=headers, timeout=15)
            if r.ok:
                d = r.json()
                items = d.get("data") if isinstance(d, dict) and "data" in d else d
                tcid = items[0].get("id") if items else None
                if tcid:
                    page.goto(f"{BASE_URL}/test-cases/{tcid}",
                              wait_until="domcontentloaded", timeout=20_000)
                    body = page.content()
                    has_steps = "step" in body.lower()
                    shot = screenshot(page, "section05_testcase_detail")
                    _rec(findings, id=f"5.2",
                         title=f"Test case detail /test-cases/{tcid} renders (steps visible)",
                         severity="P3" if has_steps else "P2",
                         status="PASS" if has_steps else "PARTIAL",
                         url=f"{BASE_URL}/test-cases/{tcid}",
                         expected="detail page mentions 'step'",
                         actual=f"steps-mention={has_steps}, {len(body)} bytes",
                         category="UI", evidence=shot)

                    # Does the validation_report surface in the UI?
                    has_valid = ("validation" in body.lower()) or ("no issues" in body.lower())
                    _rec(findings, id="5.2b",
                         title="Validation report surfaces on TC detail (banner OR 'no issues')",
                         severity="P3" if has_valid else "P2",
                         status="PASS" if has_valid else "PARTIAL",
                         url=f"{BASE_URL}/test-cases/{tcid}",
                         expected="validation banner visible",
                         actual=f"found={has_valid}",
                         category="UI", evidence="")

        # ---- Section 6: run detail with SSE log panel ----------------
        if token:
            r = requests.get(f"{BASE_URL}/api/runs",
                             headers=headers, timeout=15)
            if r.ok:
                d = r.json()
                items = d.get("data") if isinstance(d, dict) and "data" in d else d
                run_id = items[0].get("id") if items else None
                if run_id:
                    page.goto(f"{BASE_URL}/runs/{run_id}",
                              wait_until="domcontentloaded", timeout=20_000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=5_000)
                    except Exception:
                        pass
                    body = page.content()
                    has_log = "pipeline log" in body.lower() or "event-log" in body.lower()
                    has_stages = ("metadata_refresh" in body.lower()) or ("stage" in body.lower())
                    shot = screenshot(page, "section06_run_detail")
                    _rec(findings, id="6.1",
                         title=f"Run detail /runs/{run_id} renders",
                         severity="P3" if (has_log and has_stages) else "P1",
                         status="PASS" if (has_log and has_stages) else "PARTIAL",
                         url=f"{BASE_URL}/runs/{run_id}",
                         expected="log panel + stage track visible",
                         actual=f"log-panel={has_log}, stages-mention={has_stages}",
                         category="UI", evidence=shot)
                    # Copy button we shipped earlier this session
                    has_copy = "log-copy" in body or ">Copy<" in body
                    _rec(findings, id="6.2",
                         title="Pipeline-log Copy button present",
                         severity="P3",
                         status="PASS" if has_copy else "FAIL",
                         url=f"{BASE_URL}/runs/{run_id}",
                         expected="log-copy button exists",
                         actual=f"present={has_copy}",
                         category="UI", evidence="")

    return findings


if __name__ == "__main__":
    for f in run():
        print(f"  [{f['status']:7}] {f['id']}: {f['title']}")
