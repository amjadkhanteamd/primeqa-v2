"""Stabilisation amendment tests (2026-08-23): structural-quiet classifier
+ the real observer's behaviour under structural vs non-structural churn.

The pure classifier test always runs. The observer integration test is
gated on SPIKE_BROWSER=1 (it drives the actual JS in chromium).
"""

import os

import pytest

from primeqa.browser_worker.spike import _STRUCT_ATTRS, is_structural_mutation


# ---------- pure classifier (single source of truth for the JS) ----------

def test_childList_add_or_remove_is_structural():
    assert is_structural_mutation({"type": "childList", "addedCount": 1,
                                   "removedCount": 0}) is True
    assert is_structural_mutation({"type": "childList", "addedCount": 0,
                                   "removedCount": 2}) is True


def test_childList_empty_is_not_structural():
    assert is_structural_mutation({"type": "childList", "addedCount": 0,
                                   "removedCount": 0}) is False


def test_identity_attribute_changes_are_structural():
    for attr in _STRUCT_ATTRS:
        assert is_structural_mutation(
            {"type": "attributes", "attributeName": attr}) is True


def test_non_identity_attributes_are_not_structural():
    for attr in ("class", "style", "data-foo", "aria-busy", "value"):
        assert is_structural_mutation(
            {"type": "attributes", "attributeName": attr}) is False


def test_characterData_is_not_structural():
    assert is_structural_mutation({"type": "characterData"}) is False
    assert is_structural_mutation({"type": "attributes",
                                   "attributeName": None}) is False


# ---------- real observer under synthetic churn (browser-gated) ----------

@pytest.mark.skipif(os.environ.get("SPIKE_BROWSER") != "1",
                    reason="needs chromium (set SPIKE_BROWSER=1)")
def test_observer_ignores_nonstructural_churn_but_not_structural():
    from playwright.sync_api import sync_playwright

    from primeqa.browser_worker.spike import (_DOM_QUIET_MS, _STRUCT_ATTRS,
                                              _STRUCT_QUIET_JS, structural_quiet)
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        page = b.new_context().new_page()
        page.set_content("<main><p id='t'>x</p><div id='host'></div></main>")

        # Non-structural churn (text + class + style + data-*, 60ms cadence):
        # the observer must still reach quiet well within a generous budget.
        page.evaluate("""() => {
          const t = document.getElementById('t');
          window.__i = setInterval(() => {
            t.textContent = 'tick ' + Math.random();
            t.className = 'c' + Math.random();
            t.style.color = Math.random() > .5 ? 'red' : 'blue';
            t.setAttribute('data-x', String(Math.random()));
          }, 60);
        }""")
        quiet = structural_quiet(page, _DOM_QUIET_MS, 4000)
        page.evaluate("() => clearInterval(window.__i)")
        assert quiet is True, "non-structural churn wrongly blocked the scan"

        # Structural churn (add/remove nodes every 100ms): the observer must
        # NOT reach a 500ms quiet inside a 2s budget -> returns False.
        page.evaluate("""() => {
          const host = document.getElementById('host');
          window.__j = setInterval(() => {
            const n = document.createElement('span');
            n.setAttribute('role', 'note'); host.appendChild(n);
            if (host.children.length > 3) host.removeChild(host.firstChild);
          }, 100);
        }""")
        quiet2 = structural_quiet(page, _DOM_QUIET_MS, 2000)
        page.evaluate("() => clearInterval(window.__j)")
        assert quiet2 is False, "structural churn should prevent quiet"

        # After structural churn stops, quiet is reached again.
        assert structural_quiet(page, _DOM_QUIET_MS, 4000) is True
        b.close()


# ---------- navigation resilience (adversarial-review #3/#7/#8) ----------

def test_is_nav_destroyed_error_classifier():
    from playwright.sync_api import Error as PlaywrightError

    from primeqa.browser_worker.spike import is_nav_destroyed_error
    assert is_nav_destroyed_error(
        PlaywrightError("Execution context was destroyed, most likely "
                        "because of a navigation")) is True
    assert is_nav_destroyed_error(RuntimeError("navigation to X")) is True
    assert is_nav_destroyed_error(
        PlaywrightError("Protocol error (Runtime.evaluate): boom")) is False


class _FlakyGotoPage:
    def __init__(self, fails):
        self.fails = fails
        self.calls = 0

    def goto(self, url, wait_until=None, timeout=None):
        from playwright.sync_api import Error as PlaywrightError
        self.calls += 1
        if self.fails == "all" or self.calls <= self.fails:
            raise PlaywrightError("net::ERR_CONNECTION_REFUSED")
        return None


def test_navigate_with_retry_catches_network_errors_then_succeeds():
    from primeqa.browser_worker.spike import _navigate_with_retry
    page = _FlakyGotoPage(fails=1)         # first attempt errors, second ok
    ok, attempts = _navigate_with_retry(page, "http://x/", lambda: 30000)
    assert ok is True and attempts == 2


def test_navigate_with_retry_exhausts_without_escaping():
    from primeqa.browser_worker.spike import (_NAV_MAX_ATTEMPTS,
                                              _navigate_with_retry)
    page = _FlakyGotoPage(fails="all")     # never succeeds; must NOT raise
    ok, attempts = _navigate_with_retry(page, "http://x/", lambda: 30000)
    assert ok is False and attempts == _NAV_MAX_ATTEMPTS


class _FlakyEvalPage:
    def __init__(self, behaviors):
        self.behaviors = list(behaviors)
        self.load_waits = 0

    def evaluate(self, js, arg=None):
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PWTimeout
        b = self.behaviors.pop(0)
        if b == "destroy":
            raise PlaywrightError("Execution context was destroyed, most "
                                  "likely because of a navigation")
        if b == "timeout":
            raise PWTimeout("Timeout")
        if b == "other":
            raise PlaywrightError("Protocol error: boom")
        return b

    def wait_for_load_state(self, state, timeout=None):
        self.load_waits += 1


def test_structural_quiet_retries_once_after_navigation():
    from primeqa.browser_worker.spike import _DOM_QUIET_MS, structural_quiet
    page = _FlakyEvalPage(["destroy", True])   # nav mid-probe, then quiet
    assert structural_quiet(page, _DOM_QUIET_MS, 5000) is True
    assert page.load_waits == 1


def test_structural_quiet_false_on_second_destruction():
    from primeqa.browser_worker.spike import _DOM_QUIET_MS, structural_quiet
    page = _FlakyEvalPage(["destroy", "destroy"])
    assert structural_quiet(page, _DOM_QUIET_MS, 5000) is False


def test_structural_quiet_false_on_timeout_or_other_error():
    from primeqa.browser_worker.spike import _DOM_QUIET_MS, structural_quiet
    assert structural_quiet(_FlakyEvalPage(["timeout"]), _DOM_QUIET_MS, 5000) is False
    assert structural_quiet(_FlakyEvalPage(["other"]), _DOM_QUIET_MS, 5000) is False


def test_struct_attrs_cover_role_determinants():
    # #4: href (a->link) and type (input role) must be watched by the gate.
    from primeqa.browser_worker.spike import _STRUCT_ATTRS
    assert "href" in _STRUCT_ATTRS and "type" in _STRUCT_ATTRS
