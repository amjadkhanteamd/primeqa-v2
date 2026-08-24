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

import hashlib
import re
import resource
import sys
import time
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

_VENDOR_DIR = Path(__file__).parent / "vendor"
_AXE_PATH = _VENDOR_DIR / "axe.min.js"

# The vendored file opens with a banner like: /*! axe v4.13.0
_AXE_VERSION_RE = re.compile(r"/\*!\s*axe\s+v(\d+\.\d+\.\d+)")

# Structural quiet period the DOM must hold before we scan (ui-session
# stabilisation amendment, 2026-08-23). "Structural" = exactly the changes
# the fingerprint measures; non-structural churn (text, class/style, data-*,
# characterData) never blocks a scan. See LLD_SESSION_SUBSTRATE.md.
_DOM_QUIET_MS = 500

# The fingerprint-identity attributes the gate treats as STRUCTURAL: the
# name determinants (aria-label/alt/title/placeholder) and the role
# determinants (role, plus href on <a> and type on <input>, which
# _FINGERPRINT_JS.roleOf reads). Single source of truth: interpolated into
# the observer JS AND used by the pure is_structural_mutation() spec. NOTE:
# label textContent also feeds an accessible NAME, but it is characterData
# and deliberately NOT watched — a dynamic accessible name is DE-18
# NOT_COMPARABLE by design, not a stabilisation stall.
_STRUCT_ATTRS = ("role", "aria-label", "alt", "title", "placeholder",
                 "href", "type")

# Navigation policy: Experience Cloud (Aura) holds persistent/streaming
# connections, so `load`/`networkidle` may never fire. Navigate on
# `domcontentloaded` with a bounded retry (fresh re-navigation on timeout).
_NAV_TIMEOUT_MS = 20000      # per-attempt domcontentloaded cap
_NAV_MAX_ATTEMPTS = 3        # 1 initial + 2 retries


def is_structural_mutation(mut: dict) -> bool:
    """Pure spec mirrored by the observer JS below. A mutation is structural
    iff it adds/removes an ELEMENT node, or changes a fingerprint-identity
    attribute on an existing node. The fingerprint walks element children
    only (`el.children`), so text-node add/remove (e.g. `textContent=`),
    characterData, class / style / data-* / aria-busy changes are all
    NON-structural and never restart the quiet timer. `addedCount` /
    `removedCount` are ELEMENT counts."""
    t = mut.get("type")
    if t == "childList":
        return bool(mut.get("addedCount", 0)) or bool(mut.get("removedCount", 0))
    if t == "attributes":
        return mut.get("attributeName") in _STRUCT_ATTRS
    return False


# Resolves true when the DOM holds `quietMs` free of STRUCTURAL mutations,
# false when timeoutMs elapses first. The observer is scoped to childList +
# the identity attributes (attributeFilter) and does NOT watch characterData,
# so non-structural churn cannot fire it; the explicit classify is defence in
# depth and keeps the runtime rule identical to is_structural_mutation().
_STRUCT_QUIET_JS = """
([quietMs, timeoutMs, structAttrs]) => new Promise((resolve) => {
  let done = false;
  let timer = null;
  const arm = () => { if (timer) clearTimeout(timer);
                      timer = setTimeout(() => finish(true), quietMs); };
  const hasElement = (nodes) => {
    for (const n of nodes) { if (n.nodeType === 1) return true; }
    return false;
  };
  const structural = (r) => {
    if (r.type === 'childList')
      return hasElement(r.addedNodes) || hasElement(r.removedNodes);
    if (r.type === 'attributes')
      return structAttrs.indexOf(r.attributeName) !== -1;
    return false;
  };
  const observer = new MutationObserver((records) => {
    if (done) return;
    for (const r of records) { if (structural(r)) { arm(); break; } }
  });
  const finish = (quietReached) => {
    if (done) return;
    done = true; observer.disconnect(); resolve(quietReached);
  };
  observer.observe(document, {
    subtree: true, childList: true, attributes: true,
    attributeFilter: structAttrs, characterData: false,
  });
  timer = setTimeout(() => finish(true), quietMs);
  setTimeout(() => finish(false), timeoutMs);
})
"""


def is_nav_destroyed_error(exc: Exception) -> bool:
    """True for the benign 'execution context was destroyed by a navigation'
    Playwright error — a normal SPA transition, not a fault."""
    msg = str(exc).lower()
    return ("execution context was destroyed" in msg
            or "context was destroyed" in msg
            or "navigation" in msg)


def _navigate_with_retry(page, url, remaining_ms_fn):
    """domcontentloaded navigation with bounded retry. Catches BOTH timeouts
    and non-timeout navigation errors (net::ERR_*, aborted redirects) so a
    nav fault never escapes raw. Retry is NAVIGATION ONLY. Returns
    (ok, attempts_used)."""
    for attempt in range(_NAV_MAX_ATTEMPTS):
        rem = remaining_ms_fn()
        if rem <= 1:
            return False, attempt
        try:
            page.goto(url, wait_until="domcontentloaded",
                      timeout=min(_NAV_TIMEOUT_MS, int(rem)))
            return True, attempt + 1
        except PlaywrightError:
            continue
    return False, _NAV_MAX_ATTEMPTS


_ARM_CHANGE_JS = """
(structAttrs) => {
  window.__plqChanged = false;
  if (window.__plqObs) { try { window.__plqObs.disconnect(); } catch (e) {} }
  const hasElement = (nodes) => {
    for (const n of nodes) { if (n.nodeType === 1) return true; }
    return false;
  };
  const structural = (r) => {
    if (r.type === 'childList')
      return hasElement(r.addedNodes) || hasElement(r.removedNodes);
    if (r.type === 'attributes')
      return structAttrs.indexOf(r.attributeName) !== -1;
    return false;
  };
  const o = new MutationObserver((recs) => {
    for (const r of recs) { if (structural(r)) { window.__plqChanged = true; break; } }
  });
  o.observe(document, {
    subtree: true, childList: true, attributes: true,
    attributeFilter: structAttrs, characterData: false,
  });
  window.__plqObs = o;
}
"""

# Cap for waiting for a submit's FIRST visible effect (nav or in-place render)
# before settling. Bounded further by the caller's remaining budget.
_ACTION_SETTLE_CAP_MS = 15000


def arm_change_observer(page) -> None:
    """Install a one-shot structural-change flag BEFORE an action (click), so
    the outcome is detectable whether the submit navigates or validates
    in-place. Best-effort — a failure here never raises into the caller."""
    try:
        page.evaluate(_ARM_CHANGE_JS, list(_STRUCT_ATTRS))
    except PlaywrightError:
        pass


def await_submit_outcome(page, quiet_ms, remaining_ms_fn) -> None:
    """After a submit click (with arm_change_observer already installed), wait
    for the submit's first visible effect — a NAVIGATION (URL change) OR an
    in-place structural change (the __plqChanged flag) OR a destroyed context
    — then structural-quiet the resulting page. The URL check matters because
    a full-page navigation loads a new document where __plqChanged is
    undefined; polling the flag alone would burn the whole cap on a classic
    full-nav login (adversarial-review #7/#8)."""
    old_url = page.url
    cap = min(int(remaining_ms_fn()), _ACTION_SETTLE_CAP_MS)
    try:
        page.wait_for_function(
            "(o) => window.location.href !== o || window.__plqChanged === true",
            arg=old_url, timeout=max(1, cap))
    except PlaywrightTimeoutError:
        pass  # no detectable change (rare) — settle the page as-is
    except PlaywrightError:
        pass  # context destroyed == navigation happened — settle the new page
    structural_quiet(page, quiet_ms, remaining_ms_fn())


def structural_quiet(page, quiet_ms, remaining_ms) -> bool:
    """True when the page holds quiet_ms free of structural mutations within
    the remaining budget; False otherwise. networkidle is NOT used. If the
    execution context is destroyed by a navigation mid-probe (normal on an
    Aura SPA), wait for the new document and retry the probe ONCE; any other
    error, or a second destruction, returns False (never escapes)."""
    budget = max(1, int(remaining_ms))
    for attempt in range(2):
        try:
            return bool(page.evaluate(
                _STRUCT_QUIET_JS, [quiet_ms, budget, list(_STRUCT_ATTRS)]))
        except PlaywrightTimeoutError:
            return False
        except PlaywrightError as exc:
            if attempt == 0 and is_nav_destroyed_error(exc):
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=budget)
                except PlaywrightError:
                    return False
                continue
            return False
    return False


# Semantic-structure walk for the normalised fingerprint (ui-s2.4).
# Emits a node per semantic element (explicit role, implicit role of a
# fixed tag map, or custom element); bare div/span wrappers collapse —
# their children attach to the nearest emitted ancestor. Accessible names
# are attribute-borne (+ label association for form controls): they are
# what assistive technology announces. Text content, ids, classes and
# styles are never read. Hashing happens python-side (see
# _fingerprint_from_tree) so the page needs no crypto access.
_FINGERPRINT_JS = """
() => {
  const SKIP = new Set(['SCRIPT','STYLE','TEMPLATE','NOSCRIPT']);
  const IMPLICIT = {
    NAV:'navigation', MAIN:'main', HEADER:'banner', FOOTER:'contentinfo',
    FORM:'form', IMG:'img', BUTTON:'button', SELECT:'combobox',
    TEXTAREA:'textbox', LABEL:'label', DIALOG:'dialog', SECTION:'region',
    ARTICLE:'article', ASIDE:'complementary', UL:'list', OL:'list',
    LI:'listitem', TABLE:'table', TR:'row', TD:'cell', TH:'columnheader',
    H1:'heading', H2:'heading', H3:'heading', H4:'heading',
    H5:'heading', H6:'heading',
  };
  const INPUT_ROLES = {checkbox:'checkbox', radio:'radio', button:'button',
    submit:'button', reset:'button', range:'slider', number:'spinbutton',
    search:'searchbox'};
  function roleOf(el) {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit;
    const tag = el.tagName;
    if (tag === 'A') return el.hasAttribute('href') ? 'link' : null;
    if (tag === 'INPUT') {
      const t = (el.getAttribute('type') || 'text').toLowerCase();
      if (t === 'hidden') return null;
      return INPUT_ROLES[t] || 'textbox';
    }
    return IMPLICIT[tag] || null;
  }
  function accName(el) {
    for (const attr of ['aria-label', 'alt', 'title', 'placeholder']) {
      const v = el.getAttribute(attr);
      if (v) return v;
    }
    const tag = el.tagName;
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') {
      if (el.id) {
        const lab = document.querySelector(
          'label[for="' + CSS.escape(el.id) + '"]');
        if (lab) return lab.textContent.trim();
      }
      const anc = el.closest('label');
      if (anc) return anc.textContent.trim();
    }
    return '';
  }
  function walk(el) {
    const out = [];
    for (const child of el.children) {
      if (SKIP.has(child.tagName)) continue;
      const role = roleOf(child);
      const custom = child.tagName.includes('-')
        ? child.tagName.toLowerCase() : '';
      const kids = walk(child);
      if (role !== null || custom) {
        out.push({role: role || '', name: accName(child),
                  tag: custom, children: kids});
      } else {
        out.push(...kids);
      }
    }
    return out;
  }
  return {children: walk(document.body)};
}
"""

_SUMMARY_NAMED_CAP = 50


def _hash_fingerprint_node(node: dict) -> str:
    # Child digests SORTED: sibling reordering is invisible; ancestry
    # still binds because a moved child changes two parents' digests.
    child_hashes = sorted(
        _hash_fingerprint_node(c) for c in node.get("children", []))
    canonical = (f"{node.get('role', '')}|{node.get('name', '')}|"
                 f"{node.get('tag', '')}|[{','.join(child_hashes)}]")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _fingerprint_from_tree(tree: dict) -> dict:
    """sha256 + compact summary from the in-page walk's tree. An
    OBSERVATION about page structure — judged nowhere in this module."""
    root = {"role": "", "name": "", "tag": "",
            "children": tree.get("children", [])}
    sha = _hash_fingerprint_node(root)

    roles: dict[str, int] = {}
    named: list[list[str]] = []
    count = 0
    stack = list(tree.get("children", []))
    while stack:
        n = stack.pop()
        count += 1
        key = n.get("tag") or n.get("role") or ""
        roles[key] = roles.get(key, 0) + 1
        if n.get("name"):
            named.append([key, n["name"]])
        stack.extend(n.get("children", []))
    named.sort()
    return {"sha256": sha,
            "summary": {"element_count": count, "roles": roles,
                        "named": named[:_SUMMARY_NAMED_CAP]}}


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


def scan_page(url: str, *, viewport=(1440, 900), max_wait_s: int = 30,
              quiet_ms: int = _DOM_QUIET_MS, locale: str | None = None,
              context=None, landed_check=None) -> dict:
    """Scan one page and return the engine observations.

    Phase timings are measured separately with time.monotonic(). The
    navigate + stabilise phases share a single max_wait_s budget; if the
    page never reaches a quiet_ms mutation-quiet state inside that budget
    the result is status="NOT_REACHED" with the timings gathered so far —
    an unstable page is never scanned.

    context: an existing (e.g. pre-authenticated) browser context owned by
    the caller — the scan opens a page in it and closes only that page;
    no launch, so timings omit the "launch" phase. landed_check: optional
    callable(page) run after stabilise (the session-lost check); any
    exception it raises propagates unchanged.
    """
    timings_ms: dict[str, float] = {}
    deadline = time.monotonic() + max_wait_s

    if context is not None:
        return _scan_in_context(
            context, context.browser.version, url, viewport=viewport,
            quiet_ms=quiet_ms, deadline=deadline, timings_ms=timings_ms,
            locale=locale, landed_check=landed_check)

    with sync_playwright() as pw:
        # Phase a: launch
        t0 = time.monotonic()
        browser = pw.chromium.launch(headless=True)
        browser_version = browser.version
        context_kwargs = {"viewport": {"width": viewport[0],
                                       "height": viewport[1]}}
        if locale:
            context_kwargs["locale"] = locale
        own_context = browser.new_context(**context_kwargs)
        timings_ms["launch"] = round((time.monotonic() - t0) * 1000, 1)
        try:
            return _scan_in_context(
                own_context, browser_version, url, viewport=viewport,
                quiet_ms=quiet_ms, deadline=deadline, timings_ms=timings_ms,
                locale=locale, landed_check=landed_check)
        finally:
            own_context.close()
            browser.close()


def _scan_in_context(context, browser_version: str, url: str, *, viewport,
                     quiet_ms: int, deadline: float, timings_ms: dict,
                     locale: str | None, landed_check) -> dict:
    def _remaining_ms() -> float:
        # Floor at 1 ms: playwright reads timeout=0 as "no timeout", which
        # would turn an exhausted budget into an unbounded wait.
        return max(1.0, (deadline - time.monotonic()) * 1000)

    base = {
        "url": url,
        "browser_version": browser_version,
        "axe_version": axe_version(),
        "viewport": {"width": viewport[0], "height": viewport[1]},
        "locale": locale,
    }
    page = context.new_page()
    page.set_viewport_size({"width": viewport[0], "height": viewport[1]})
    try:
        # Phase b: navigate — domcontentloaded + bounded retry (Aura may
        # never fire load/networkidle; retry is navigation-only).
        t0 = time.monotonic()
        nav_ok, nav_attempts = _navigate_with_retry(page, url, _remaining_ms)
        timings_ms["navigate"] = round((time.monotonic() - t0) * 1000, 1)
        base["navigate_attempts"] = nav_attempts
        if not nav_ok:
            return {"status": "NOT_REACHED", "timings_ms": timings_ms,
                    "peak_rss_mb": _peak_rss_mb(), **base}

        # Phase c: stabilise — STRUCTURAL-quiet gate (networkidle removed;
        # non-structural churn never blocks the scan).
        t0 = time.monotonic()
        quiet_reached = structural_quiet(page, quiet_ms, _remaining_ms())
        timings_ms["stabilise"] = round((time.monotonic() - t0) * 1000, 1)
        if not quiet_reached:
            return {"status": "NOT_REACHED", "timings_ms": timings_ms,
                    "peak_rss_mb": _peak_rss_mb(), **base}

        # Landed-page check (session substrate): may raise; propagates.
        if landed_check is not None:
            landed_check(page)

        # Capture phase (ui-s2.5): full-page PNG of the stabilised page as
        # the engine is about to see it — an observation artifact, held in
        # memory (bytes returned, never written to disk here).
        t0 = time.monotonic()
        screenshot_png = page.screenshot(full_page=True)
        timings_ms["capture"] = round((time.monotonic() - t0) * 1000, 1)

        # Phase d: inject the vendored engine. NOT via add_script_tag — that
        # appends a DOM <script>, which a strict Content-Security-Policy
        # (Salesforce Experience Cloud) blocks. page.evaluate runs the source
        # in the main world over CDP, which is not subject to the page's
        # script-src CSP; the vendored file is still the only source, read
        # from local disk, never fetched.
        t0 = time.monotonic()
        page.evaluate(_AXE_PATH.read_text(encoding="utf-8"))
        timings_ms["inject"] = round((time.monotonic() - t0) * 1000, 1)

        # Phase e: run the engine, collect its raw JSON
        t0 = time.monotonic()
        raw = page.evaluate("axe.run(document)")
        timings_ms["axe_run"] = round((time.monotonic() - t0) * 1000, 1)

        # Phase f: normalised semantic fingerprint (ui-s2.4) — an
        # observation of page structure, judged nowhere here.
        t0 = time.monotonic()
        tree = page.evaluate(_FINGERPRINT_JS)
        fingerprint = _fingerprint_from_tree(tree)
        timings_ms["fingerprint"] = round((time.monotonic() - t0) * 1000, 1)
    finally:
        page.close()

    return {
        "status": "OK",
        "timings_ms": timings_ms,
        "peak_rss_mb": _peak_rss_mb(),
        "fingerprint": fingerprint,
        "screenshot_png": screenshot_png,          # bytes; callers pop before JSON
        "screenshot_bytes": len(screenshot_png),
        "engine_observations": {
            "violations_count": len(raw.get("violations", [])),
            "passes_count": len(raw.get("passes", [])),
            "incomplete_count": len(raw.get("incomplete", [])),
            "violations": raw.get("violations", []),
        },
        **base,
    }
