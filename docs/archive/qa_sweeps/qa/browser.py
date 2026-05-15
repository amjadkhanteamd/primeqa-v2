"""Playwright helpers for the PrimeQA QA sweep.

Provides:

    with playwright_page(viewport="desktop") as page:
        login(page, email, password)
        page.goto("...")
        screenshot(page, "name")

Screenshots land in qa/screenshots/.
"""
from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from typing import Iterator, Tuple

from playwright.sync_api import sync_playwright, Page, BrowserContext

BASE_URL = os.environ.get("PRIMEQA_BASE_URL", "https://primeqa-v2-production.up.railway.app")

# Default test credentials. Override via PRIMEQA_TEST_EMAIL / _PASSWORD envs.
# These match the seeded-admin pattern from migrations/001 + CLAUDE.md.
DEFAULT_EMAIL = os.environ.get("PRIMEQA_TEST_EMAIL", "admin@primeqa.io")
DEFAULT_PASSWORD = os.environ.get("PRIMEQA_TEST_PASSWORD", "changeme123")

SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

VIEWPORTS = {
    "desktop": {"width": 1280, "height": 720},
    "mobile":  {"width": 375,  "height": 812},
}


@contextmanager
def playwright_page(viewport: str = "desktop",
                     *, storage_state: str = None) -> Iterator[Tuple[Page, BrowserContext]]:
    """Yield (page, context). Caller can reuse context across multiple
    page navigations; storage_state lets tests persist login cookies.
    """
    vp = VIEWPORTS.get(viewport, VIEWPORTS["desktop"])
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport=vp,
            storage_state=storage_state,
            ignore_https_errors=False,
        )
        # Catch unhandled JS errors per-page; tests can read ctx._js_errors.
        context._js_errors = []  # attach custom attr
        page = context.new_page()

        def _on_page_error(err):
            try:
                context._js_errors.append(str(err))
            except Exception:
                pass

        def _on_console_msg(msg):
            if msg.type == "error":
                try:
                    context._js_errors.append(f"console: {msg.text}")
                except Exception:
                    pass

        page.on("pageerror", _on_page_error)
        page.on("console", _on_console_msg)

        try:
            yield page, context
        finally:
            browser.close()


def login(page: Page, email: str = DEFAULT_EMAIL,
          password: str = DEFAULT_PASSWORD, *,
          wait_for_redirect: bool = True) -> dict:
    """Navigate to /login and authenticate.

    Returns a dict:
        {"status": "ok"|"failed", "landing_url": <final URL>, "detail": <message>}

    Never raises \u2014 callers handle failure gracefully.
    """
    try:
        page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded", timeout=30_000)
    except Exception as e:
        return {"status": "failed", "landing_url": page.url, "detail": f"goto: {e}"}

    # Locate the email + password fields via best-effort selectors.
    try:
        if page.locator("input[name=email]").count():
            page.fill("input[name=email]", email)
        elif page.locator("input[type=email]").count():
            page.fill("input[type=email]", email)
        else:
            return {"status": "failed", "landing_url": page.url,
                    "detail": "no email input found"}
        if page.locator("input[name=password]").count():
            page.fill("input[name=password]", password)
        elif page.locator("input[type=password]").count():
            page.fill("input[type=password]", password)
        else:
            return {"status": "failed", "landing_url": page.url,
                    "detail": "no password input found"}
        # Submit the form
        submit = page.locator("button[type=submit]").first
        if submit.count():
            submit.click()
        else:
            page.keyboard.press("Enter")
    except Exception as e:
        return {"status": "failed", "landing_url": page.url, "detail": f"form fill: {e}"}

    if wait_for_redirect:
        try:
            # Wait up to 10s for the URL to change away from /login.
            page.wait_for_url(lambda url: "/login" not in url, timeout=10_000)
        except Exception:
            # Stayed on /login \u2014 probably a validation error shown inline.
            pass

    on_login = "/login" in (page.url or "")
    return {
        "status": "failed" if on_login else "ok",
        "landing_url": page.url,
        "detail": "stayed on /login" if on_login else "redirected",
    }


def screenshot(page: Page, name: str) -> str:
    """Save a screenshot under qa/screenshots/<name>.png and return the path."""
    safe = name.replace("/", "_").replace(" ", "_")
    path = os.path.join(SCREENSHOT_DIR, f"{safe}.png")
    try:
        page.screenshot(path=path, full_page=True)
    except Exception as e:
        # Some sandbox setups disable full_page \u2014 fall back.
        try:
            page.screenshot(path=path)
        except Exception:
            print(f"screenshot failed: {e}", file=sys.stderr)
            return ""
    return path


def expect_http(status: int, allowed) -> bool:
    if isinstance(allowed, int):
        return status == allowed
    return status in allowed
