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
from dataclasses import dataclass, field
from urllib.parse import urlparse

_log = logging.getLogger("primeqa.browser_worker.session")

# ---- run-level error taxonomy (arm G) -----------------------------------

BAD_CREDENTIAL = "BAD_CREDENTIAL"
MFA_FAILED = "MFA_FAILED"
MFA_REQUIRED_NOT_CONFIGURED = "MFA_REQUIRED_NOT_CONFIGURED"
LOGIN_PAGE_NOT_RECOGNIZED = "LOGIN_PAGE_NOT_RECOGNIZED"
SESSION_LOST = "SESSION_LOST"
CREDENTIAL_NOT_CONFIGURED = "CREDENTIAL_NOT_CONFIGURED"

# Re-running the same inputs cannot fix these -> failed_permanent.
_PERMANENT = {BAD_CREDENTIAL, MFA_REQUIRED_NOT_CONFIGURED,
              LOGIN_PAGE_NOT_RECOGNIZED, CREDENTIAL_NOT_CONFIGURED}


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


def _inventory(page) -> dict:
    return page.evaluate(_INVENTORY_JS)


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

    log = log or _log
    events: list = []
    deadline = time.monotonic() + max_wait_s

    def rem() -> float:
        return max(1.0, (deadline - time.monotonic()) * 1000)

    def settle(page):
        page.wait_for_load_state("load", timeout=rem())
        page.wait_for_load_state("networkidle", timeout=rem())

    page = context.new_page()
    step = "navigate"
    try:
        page.goto(base_url + start_path, wait_until="load", timeout=rem())
        page.wait_for_load_state("networkidle", timeout=rem())
        log.info("LOGIN_PAGE_LOADED")

        inv = _inventory(page)
        kind, sel = classify_inventory(inv)
        if kind != "login":
            raise LoginError(LOGIN_PAGE_NOT_RECOGNIZED,
                             f"step=initial kind={kind} {_summary(inv)}")
        page.fill(_sel(sel["username"]), creds.username)
        page.fill(_sel(sel["password"]), creds.password)
        step = "post_password"
        with page.expect_navigation(wait_until="load", timeout=rem()):
            page.click(_sel(sel["submit"]))
        events.append("LOGIN_SUBMITTED")
        log.info("LOGIN_SUBMITTED")
        settle(page)

        inv = _inventory(page)
        kind, sel = classify_inventory(inv)
        if kind == "login":
            raise LoginError(BAD_CREDENTIAL, "credential form re-presented")
        if kind == "mfa":
            if not creds.totp_seed:
                raise LoginError(MFA_REQUIRED_NOT_CONFIGURED,
                                 "verification form presented; seed unset")
            code = pyotp.TOTP(creds.totp_seed).now()   # computed now, used once
            page.fill(_sel(sel["code"]), code)
            del code
            step = "post_mfa"
            with page.expect_navigation(wait_until="load", timeout=rem()):
                page.click(_sel(sel["verify"]))
            events.append("MFA_SUBMITTED")
            log.info("MFA_SUBMITTED")
            settle(page)
            inv = _inventory(page)
            kind, _ = classify_inventory(inv)
            if kind in ("mfa", "login"):
                raise LoginError(MFA_FAILED, f"{kind} form after code submit")
        else:
            events.append("MFA_NOT_PRESENTED")
            log.info("MFA_NOT_PRESENTED")

        step = "landed"
        landed_path = urlparse(page.url).path
        want_path = urlparse(start_path).path
        if kind != "unknown" or not landed_path.startswith(want_path):
            raise LoginError(LOGIN_PAGE_NOT_RECOGNIZED,
                             f"step=landed kind={kind} path={landed_path}")
        log.info("LOGIN_LANDED")
        return LoginOutcome(ok=True, events=events, landed_url=page.url)
    except PlaywrightTimeoutError:
        raise LoginError(LOGIN_PAGE_NOT_RECOGNIZED, f"timeout@{step}")
    finally:
        page.close()
