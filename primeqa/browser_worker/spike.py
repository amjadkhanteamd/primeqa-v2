"""Phase 2.1 spike — manual page scanner (dormant; no product wiring).

Launches headless chromium, stabilises the page, injects the VENDORED axe
engine (never fetched at run time), and returns the engine's raw output.

Naming contract: everything the axe engine reports is an ENGINE OBSERVATION.
This module records observations; it draws no conclusions from them.

Boundaries (deliberate, per the spike brief):
  - no DB access
  - no imports from primeqa outside this package
  - stdlib + playwright only
"""

from __future__ import annotations

import re
import resource
import sys
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

_VENDOR_DIR = Path(__file__).parent / "vendor"
_AXE_PATH = _VENDOR_DIR / "axe.min.js"

# The vendored file opens with a banner like: /*! axe v4.13.0
_AXE_VERSION_RE = re.compile(r"/\*!\s*axe\s+v(\d+\.\d+\.\d+)")

# Quiet period the DOM must hold with zero mutations before we scan.
_DOM_QUIET_MS = 500

# Resolves true when the DOM has been mutation-free for quietMs, false when
# timeoutMs elapses first. Observing document with full coverage so any
# late-arriving async render restarts the quiet timer.
_DOM_QUIET_JS = """
([quietMs, timeoutMs]) => new Promise((resolve) => {
  let done = false;
  let timer = null;
  const observer = new MutationObserver(() => {
    if (done) return;
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => finish(true), quietMs);
  });
  const finish = (quietReached) => {
    if (done) return;
    done = true;
    observer.disconnect();
    resolve(quietReached);
  };
  observer.observe(document, {
    subtree: true, childList: true, attributes: true, characterData: true,
  });
  timer = setTimeout(() => finish(true), quietMs);
  setTimeout(() => finish(false), timeoutMs);
})
"""


def axe_version() -> str:
    """Read the engine version out of the vendored file's banner."""
    head = _AXE_PATH.read_text(encoding="utf-8", errors="replace")[:200]
    match = _AXE_VERSION_RE.search(head)
    if not match:
        raise RuntimeError(f"vendored axe banner not recognised: {_AXE_PATH}")
    return match.group(1)


def _peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # ru_maxrss is bytes on macOS, kilobytes on Linux.
    divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
    return round(peak / divisor, 1)


def scan_page(url: str, *, viewport=(1440, 900), max_wait_s: int = 30) -> dict:
    """Scan one page and return the engine observations.

    Phase timings are measured separately with time.monotonic(). The
    navigate + stabilise phases share a single max_wait_s budget; if the
    page never reaches a 500 ms mutation-quiet state inside that budget the
    result is status="NOT_REACHED" with the timings gathered so far — an
    unstable page is never scanned.
    """
    timings_ms: dict[str, float] = {}
    deadline = time.monotonic() + max_wait_s

    def _remaining_ms() -> float:
        # Floor at 1 ms: playwright reads timeout=0 as "no timeout", which
        # would turn an exhausted budget into an unbounded wait.
        return max(1.0, (deadline - time.monotonic()) * 1000)

    with sync_playwright() as pw:
        # Phase a: launch
        t0 = time.monotonic()
        browser = pw.chromium.launch(headless=True)
        browser_version = browser.version
        context = browser.new_context(
            viewport={"width": viewport[0], "height": viewport[1]}
        )
        page = context.new_page()
        timings_ms["launch"] = round((time.monotonic() - t0) * 1000, 1)

        base = {
            "url": url,
            "browser_version": browser_version,
            "axe_version": axe_version(),
            "viewport": {"width": viewport[0], "height": viewport[1]},
        }

        try:
            # Phase b: navigate, wait for load
            t0 = time.monotonic()
            try:
                page.goto(url, wait_until="load", timeout=_remaining_ms())
            except PlaywrightTimeoutError:
                timings_ms["navigate"] = round((time.monotonic() - t0) * 1000, 1)
                return {
                    "status": "NOT_REACHED",
                    "timings_ms": timings_ms,
                    "peak_rss_mb": _peak_rss_mb(),
                    **base,
                }
            timings_ms["navigate"] = round((time.monotonic() - t0) * 1000, 1)

            # Phase c: stabilise — networkidle, then a DOM-mutation quiet
            # period, both inside the remaining max_wait_s budget.
            t0 = time.monotonic()
            quiet_reached = False
            try:
                page.wait_for_load_state("networkidle", timeout=_remaining_ms())
                quiet_reached = page.evaluate(
                    _DOM_QUIET_JS, [_DOM_QUIET_MS, max(1, int(_remaining_ms()))]
                )
            except PlaywrightTimeoutError:
                quiet_reached = False
            timings_ms["stabilise"] = round((time.monotonic() - t0) * 1000, 1)
            if not quiet_reached:
                return {
                    "status": "NOT_REACHED",
                    "timings_ms": timings_ms,
                    "peak_rss_mb": _peak_rss_mb(),
                    **base,
                }

            # Phase d: inject the vendored engine from the local file
            t0 = time.monotonic()
            page.add_script_tag(path=str(_AXE_PATH))
            timings_ms["inject"] = round((time.monotonic() - t0) * 1000, 1)

            # Phase e: run the engine, collect its raw JSON
            t0 = time.monotonic()
            raw = page.evaluate("axe.run(document)")
            timings_ms["axe_run"] = round((time.monotonic() - t0) * 1000, 1)
        finally:
            context.close()
            browser.close()

    return {
        "status": "OK",
        "timings_ms": timings_ms,
        "peak_rss_mb": _peak_rss_mb(),
        "engine_observations": {
            "violations_count": len(raw.get("violations", [])),
            "passes_count": len(raw.get("passes", [])),
            "incomplete_count": len(raw.get("incomplete", [])),
            "violations": raw.get("violations", []),
        },
        **base,
    }
