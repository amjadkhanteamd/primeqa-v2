"""Session substrate — TOTP portal login + session checks (ui-session).

Design: docs/ui-testing/LLD_SESSION_SUBSTRATE.md. Hard rules:
  - secrets arrive ONLY as an in-memory Credentials value (read from env
    by the consumer); this module never logs, stores, or returns them;
  - log events by NAME only (LOGIN_SUBMITTED, MFA_SUBMITTED, ...);
  - the TOTP code is computed at submit time from the seed and used once;
  - detection is explicit — the page is inventoried once per step and
    classified by "exactly one" rules; anything else is
    LOGIN_PAGE_NOT_RECOGNIZED. No selector guessing, no heuristic retries.
"""

from __future__ import annotations

import logging
import re
import time

from playwright.sync_api import Error as PlaywrightError
from dataclasses import dataclass, field
from urllib.parse import urlparse

_log = logging.getLogger("primeqa.browser_worker.session")

# ---- run-level error taxonomy (arm G) -----------------------------------

BAD_CREDENTIAL = "BAD_CREDENTIAL"
MFA_FAILED = "MFA_FAILED"          # portal rejected a validly-computed code
MFA_REQUIRED_NOT_CONFIGURED = "MFA_REQUIRED_NOT_CONFIGURED"
LOGIN_PAGE_NOT_RECOGNIZED = "LOGIN_PAGE_NOT_RECOGNIZED"
SESSION_LOST = "SESSION_LOST"
CREDENTIAL_NOT_CONFIGURED = "CREDENTIAL_NOT_CONFIGURED"
# Productionisation (vault): distinct named classes for the vault read.
PERSONA_NOT_FOUND = "PERSONA_NOT_FOUND"
PERSONA_INACTIVE = "PERSONA_INACTIVE"
# totp_env is DEV-ONLY: refused under PLIMSOL_SERVICE_ROLE=browser-worker.
DEV_AUTH_MODE_REFUSED = "DEV_AUTH_MODE_REFUSED"
# The login page could not be loaded/reached (nav timeout after the bounded
# nav retries, or a network error) — distinct from LOGIN_PAGE_NOT_RECOGNIZED
# (page loaded, form unrecognized). This is PRE-submit, so retry is
# credential-safe: RETRYABLE.
PAGE_NOT_REACHED = "PAGE_NOT_REACHED"
# PORTAL_TOTP_SEED is present but not valid base32 (a code cannot be
# generated) — a deterministic configuration fault, not a portal rejection.
MFA_SEED_INVALID = "MFA_SEED_INVALID"

# Re-running the same inputs cannot fix these -> failed_permanent. All
# CREDENTIAL-REJECTION classes are permanent: a portal-rejected password or
# TOTP code will not be accepted on retry, and re-running resubmits a
# known-bad credential to the live auth endpoint (MFA-lockout risk) —
# forbidden by the single-attempt discipline. MFA_FAILED is therefore
# PERMANENT (reversing the earlier clock-skew-retry choice: the rare
# transient-code case is better handled by a human re-enqueue than by
# auto-resubmitting wrong codes). Only PAGE_NOT_REACHED (pre-submit) and
# SESSION_LOST (correct-credential session recovery) stay retryable —
# neither resubmits a WRONG credential.
_PERMANENT = {BAD_CREDENTIAL, MFA_FAILED, MFA_REQUIRED_NOT_CONFIGURED,
              LOGIN_PAGE_NOT_RECOGNIZED, CREDENTIAL_NOT_CONFIGURED,
              PERSONA_NOT_FOUND, PERSONA_INACTIVE, DEV_AUTH_MODE_REFUSED,
              MFA_SEED_INVALID}


class LoginError(Exception):
    """A run-level login failure. `code` is the taxonomy class; `detail`
    is a short NON-SECRET note (step name, form kind, counts)."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail

    @property
    def retryable(self) -> bool:
        return self.code not in _PERMANENT


@dataclass
class Credentials:
    username: str
    password: str = field(repr=False)
    totp_seed: str | None = field(default=None, repr=False)

    def __repr__(self) -> str:  # never leak values via repr/str/logging
        return "Credentials(<redacted>)"

    __str__ = __repr__


@dataclass
class LoginOutcome:
    ok: bool
    events: list
    landed_url: str


# ---- explicit detection ---------------------------------------------------

# One in-page pass: list VISIBLE inputs + buttons with attribute-borne
# identity, tagging each with data-plq=<n> so the fill/click targets
# exactly that element. Values are never read.
_INVENTORY_JS = """
() => {
  document.querySelectorAll('[data-plq]').forEach(
    e => e.removeAttribute('data-plq'));
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && st.visibility !== 'hidden'
      && st.display !== 'none';
  };
  const labelFor = (el) => {
    if (el.id) {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l) return l.textContent.trim();
    }
    const anc = el.closest('label');
    return anc ? anc.textContent.trim() : '';
  };
  let n = 0; const inputs = []; const buttons = [];
  document.querySelectorAll('input, button, [role="button"]').forEach(el => {
    if (!vis(el)) return;
    const tag = el.tagName;
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'INPUT' && !['submit', 'button', 'image', 'reset'].includes(type)) {
      if (type === 'hidden') return;
      n += 1; el.setAttribute('data-plq', String(n));
      inputs.push({idx: n, type: type || 'text', id: el.id || '',
                   name: el.getAttribute('name') || '',
                   placeholder: el.getAttribute('placeholder') || '',
                   aria_label: el.getAttribute('aria-label') || '',
                   label: labelFor(el)});
    } else {
      n += 1; el.setAttribute('data-plq', String(n));
      buttons.push({idx: n, text: (el.textContent || '').trim(),
                    value: el.getAttribute('value') || '',
                    id: el.id || '', type: type});
    }
  });
  return {inputs: inputs, buttons: buttons};
}
"""

_USER_RE = re.compile(r"user ?name|email|login", re.I)
_LOGIN_BTN_RE = re.compile(r"log ?in|sign ?in", re.I)
_CODE_RE = re.compile(r"verif|code|^tc$|otp", re.I)
_VERIFY_BTN_RE = re.compile(r"verify|continue|submit|next", re.I)
_TEXTLIKE = {"text", "email", ""}
_CODELIKE = {"text", "tel", "number", ""}


def _attr_match(rx, item: dict, keys) -> bool:
    return any(rx.search(item.get(k) or "") for k in keys)


def classify_inventory(inv: dict):
    """Pure. Returns ("login", {username, password, submit}) |
    ("mfa", {code, verify}) | ("unknown", None). Exactly-one rules; any
    ambiguity is "unknown"."""
    inputs = inv.get("inputs", [])
    buttons = inv.get("buttons", [])
    passwords = [i for i in inputs if i.get("type") == "password"]
    if len(passwords) == 1:
        users = [i for i in inputs if i.get("type") in _TEXTLIKE
                 and _attr_match(_USER_RE, i,
                                 ("id", "name", "placeholder",
                                  "aria_label", "label"))]
        submits = [b for b in buttons
                   if _attr_match(_LOGIN_BTN_RE, b, ("text", "value", "id"))]
        if len(users) == 1 and len(submits) == 1:
            return "login", {"username": users[0]["idx"],
                             "password": passwords[0]["idx"],
                             "submit": submits[0]["idx"]}
        return "unknown", None
    if len(passwords) == 0:
        codes = [i for i in inputs if i.get("type") in _CODELIKE
                 and _attr_match(_CODE_RE, i,
                                 ("id", "name", "placeholder",
                                  "aria_label", "label"))]
        verifies = [b for b in buttons
                    if _attr_match(_VERIFY_BTN_RE, b, ("text", "value", "id"))]
        if len(codes) == 1 and len(verifies) == 1:
            return "mfa", {"code": codes[0]["idx"],
                           "verify": verifies[0]["idx"]}
    return "unknown", None


def _sel(idx: int) -> str:
    return f'[data-plq="{idx}"]'


def _stable_classify_after_submit(page, rem, quiet_ms):
    """After a submit, re-inventory until a recognised form (login/mfa)
    appears, or the page settles with no form (an authenticated candidate).
    A recognised form is the ONLY early exit — no URL heuristic — so a
    delayed MFA render is never skipped, and a transiently-cleared form
    region (the premature-quiet race, #5) gets up to 4 settle rounds to
    re-render before we conclude 'unknown'."""
    from primeqa.browser_worker.spike import structural_quiet
    kind, sel, inv = "unknown", None, {}
    for _ in range(6):
        inv = _inventory(page)
        kind, sel = classify_inventory(inv)
        if kind in ("login", "mfa"):
            return kind, sel, inv
        if rem() <= 1:
            break
        structural_quiet(page, quiet_ms, min(rem(), 2000))
    return kind, sel, inv


def _inventory(page) -> dict:
    """Inventory the page, resilient to a navigation destroying the execution
    context mid-call (normal on an Aura SPA): wait for the new document and
    retry once. A second destruction re-raises (the login belt maps it)."""
    from primeqa.browser_worker.spike import is_nav_destroyed_error
    for attempt in range(2):
        try:
            return page.evaluate(_INVENTORY_JS)
        except PlaywrightError as exc:
            if attempt == 0 and is_nav_destroyed_error(exc):
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                except PlaywrightError:
                    raise
                continue
            raise


def _summary(inv: dict) -> str:
    return f"inputs={len(inv.get('inputs', []))} buttons={len(inv.get('buttons', []))}"


def session_is_lost(page) -> bool:
    """True when the landed page is a recognized LOGIN or MFA form."""
    kind, _ = classify_inventory(_inventory(page))
    return kind in ("login", "mfa")


def assert_session(page) -> None:
    """Landed-page check for authenticated surface scans."""
    if session_is_lost(page):
        raise LoginError(SESSION_LOST, f"login/mfa form at {urlparse(page.url).path}")


# ---- the login flow ---------------------------------------------------------

def login(context, base_url: str, start_path: str, creds: Credentials, *,
          max_wait_s: int = 30, log: logging.Logger | None = None) -> LoginOutcome:
    """One login per job. Raises LoginError(<class>) on any failure; the
    browser context is left authenticated on success."""
    import pyotp
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    from primeqa.browser_worker.spike import (
        _DOM_QUIET_MS, arm_change_observer, await_submit_outcome,
        _navigate_with_retry, structural_quiet)

    log = log or _log
    events: list = []
    deadline = time.monotonic() + max_wait_s

    def rem() -> float:
        return max(1.0, (deadline - time.monotonic()) * 1000)

    def settle(page):
        # structural-quiet settle (networkidle removed — Aura never idles;
        # non-structural churn must not starve the settle).
        structural_quiet(page, _DOM_QUIET_MS, rem())

    page = context.new_page()
    step = "navigate"
    try:
        nav_ok, _ = _navigate_with_retry(page, base_url + start_path, rem)
        if not nav_ok:
            raise LoginError(PAGE_NOT_REACHED, "nav_failed@navigate")
        settle(page)
        log.info("LOGIN_PAGE_LOADED")

        # Wait for the login form to render: Aura injects it after a JS-load
        # quiet gap that a single settle can return within (observed live —
        # the initial inventory otherwise reads inputs=0 buttons=0).
        kind, sel, inv = _stable_classify_after_submit(page, rem, _DOM_QUIET_MS)
        if kind != "login":
            raise LoginError(LOGIN_PAGE_NOT_RECOGNIZED,
                             f"step=initial kind={kind} {_summary(inv)}")
        page.fill(_sel(sel["username"]), creds.username)
        page.fill(_sel(sel["password"]), creds.password)
        step = "post_password"
        arm_change_observer(page)          # arm BEFORE the click (no race)
        page.click(_sel(sel["submit"]))
        events.append("LOGIN_SUBMITTED")
        log.info("LOGIN_SUBMITTED")
        # Tolerant of both full-navigation and in-place (XHR) validation, so
        # the re-inventory below sees the true outcome, not the pre-submit form.
        await_submit_outcome(page, _DOM_QUIET_MS, rem)

        kind, sel, inv = _stable_classify_after_submit(page, rem, _DOM_QUIET_MS)
        if kind == "login":
            # A 'login' here may be the transient login form still in the DOM
            # mid login->MFA navigation (observed live: correct creds route to
            # /_ui/identity/verification/ a beat later). Confirm with one more
            # settle before concluding a credential rejection.
            settle(page)
            kind, sel, inv = _stable_classify_after_submit(page, rem, _DOM_QUIET_MS)
        if kind == "login":
            raise LoginError(BAD_CREDENTIAL, "credential form re-presented")
        if kind == "mfa":
            if not creds.totp_seed:
                raise LoginError(MFA_REQUIRED_NOT_CONFIGURED,
                                 "verification form presented; seed unset")
            # Normalise a space/hyphen-grouped display seed, then compute the
            # code. An empty-after-normalise or non-base32 seed cannot produce
            # a usable code -> a coded PERMANENT MFA_SEED_INVALID (never an
            # uncoded escape / credential resubmit). NB pyotp.TOTP("").now()
            # returns a (bogus) code without raising, so the empty case must be
            # rejected explicitly rather than relying on the except.
            _seed = re.sub(r"[\s-]", "", creds.totp_seed).upper()
            if not _seed:
                raise LoginError(MFA_SEED_INVALID, "seed empty after normalisation")
            try:
                code = pyotp.TOTP(_seed).now()   # computed now, used once
            except Exception:                    # noqa: BLE001 — bad base32 etc.
                raise LoginError(MFA_SEED_INVALID, "seed not valid base32")
            page.fill(_sel(sel["code"]), code)
            del code
            step = "post_mfa"
            arm_change_observer(page)
            page.click(_sel(sel["verify"]))
            events.append("MFA_SUBMITTED")
            log.info("MFA_SUBMITTED")
            await_submit_outcome(page, _DOM_QUIET_MS, rem)
            kind, _, inv = _stable_classify_after_submit(page, rem, _DOM_QUIET_MS)
            if kind in ("mfa", "login"):
                # Confirm: a correct code may still be navigating to the portal
                # when first inventoried; only a persistent form is a rejection.
                settle(page)
                kind, _, inv = _stable_classify_after_submit(page, rem, _DOM_QUIET_MS)
            if kind in ("mfa", "login"):
                raise LoginError(MFA_FAILED, f"{kind} form after code submit")
        else:
            events.append("MFA_NOT_PRESENTED")
            log.info("MFA_NOT_PRESENTED")

        step = "landed"
        landed_path = urlparse(page.url).path
        # Authenticated iff no login/mfa form AND not still on a /login route
        # — the same lenient definition assert_session uses per surface (which
        # is the true auth gate; a false 'authenticated' here is caught when
        # the first surface scan redirects to login -> SESSION_LOST). The deep
        # surface path is NOT required: the portal may land on an intermediate
        # or home URL, and each surface is re-navigated by its own scan.
        if kind != "unknown" or "login" in landed_path.lower():
            raise LoginError(LOGIN_PAGE_NOT_RECOGNIZED,
                             f"step=landed kind={kind} path={landed_path}")
        log.info("LOGIN_LANDED")
        return LoginOutcome(ok=True, events=events, landed_url=page.url)
    except LoginError:
        raise
    except PlaywrightTimeoutError:
        raise LoginError(LOGIN_PAGE_NOT_RECOGNIZED, f"timeout@{step}")
    except PlaywrightError:
        # Residual Playwright fault (context destroyed, protocol error) after
        # the resilient settle/inventory: coded PERMANENT failure.
        raise LoginError(LOGIN_PAGE_NOT_RECOGNIZED, f"page_error@{step}")
    except Exception as exc:            # noqa: BLE001 — the contract catch-all
        # Nothing escapes login() uncoded (docstring contract). PERMANENT so a
        # deterministic fault (e.g. an unusable seed) never resubmits
        # credentials; detail carries the exception TYPE only, never its
        # message, which could echo secret input.
        raise LoginError(LOGIN_PAGE_NOT_RECOGNIZED,
                         f"error@{step}:{type(exc).__name__}")
    finally:
        page.close()
