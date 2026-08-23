"""Session-substrate tests — taxonomy + redaction with a FAKE page (no
browser), session-lost check, env absence, manifest auth descriptor
round-trip (DB-gated). All credential values here are obviously fake.
"""

import logging
import os
from contextlib import contextmanager

import pytest

SPIKE_DB = os.environ.get("SPIKE_DATABASE_URL")

FAKE_USER = "user@example.invalid"
FAKE_PASS = "not-a-real-password-7f3k"
FAKE_SEED = "JBSWY3DPEHPK3PXP"            # RFC 6238 test-vector seed, public


def _inp(idx, type_="text", **attrs):
    base = {"idx": idx, "type": type_, "id": "", "name": "",
            "placeholder": "", "aria_label": "", "label": ""}
    base.update(attrs)
    return base


def _btn(idx, text="", value="", id_="", type_="submit"):
    return {"idx": idx, "text": text, "value": value, "id": id_, "type": type_}


LOGIN_INV = {"inputs": [_inp(1, placeholder="Username"),
                        _inp(2, "password", placeholder="Password")],
             "buttons": [_btn(3, text="Log in")]}
MFA_INV = {"inputs": [_inp(1, id="tc", name="tc", label="Verification Code")],
           "buttons": [_btn(2, value="Verify", id_="save")]}
HOME_INV = {"inputs": [_inp(1, id="search", name="q", placeholder="Search...")],
            "buttons": [_btn(2, text="Search", type_="button")]}
TWO_PASSWORDS_INV = {"inputs": [_inp(1, placeholder="Username"),
                                _inp(2, "password"), _inp(3, "password")],
                     "buttons": [_btn(4, text="Log in")]}
TWO_LOGIN_BUTTONS_INV = {"inputs": LOGIN_INV["inputs"],
                         "buttons": [_btn(3, text="Log in"),
                                     _btn(4, text="Log in with SSO")]}


class FakePage:
    """Drives session.login without a browser: canned inventories per
    evaluate() call, canned URLs per navigation."""

    def __init__(self, inventories, urls):
        self._inventories = list(inventories)
        self._urls = list(urls)
        self.url = "about:blank"
        self.fills = []
        self.clicks = []
        self.closed = False

    def goto(self, url, **kw):
        self.url = self._urls.pop(0) if self._urls else url

    def wait_for_load_state(self, *a, **kw):
        pass

    def evaluate(self, js):
        return self._inventories.pop(0)

    def fill(self, selector, value):
        self.fills.append((selector, value))

    def click(self, selector):
        self.clicks.append(selector)
        if self._urls:
            self.url = self._urls.pop(0)

    @contextmanager
    def expect_navigation(self, **kw):
        yield

    def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, page):
        self.page = page

    def new_page(self):
        return self.page


BASE = "https://portal.example.invalid"


def _creds(seed=FAKE_SEED):
    from primeqa.browser_worker.session import Credentials
    return Credentials(FAKE_USER, FAKE_PASS, seed)


def _login(inventories, urls, creds=None):
    from primeqa.browser_worker.session import login
    page = FakePage(inventories, urls)
    out = login(FakeContext(page), BASE, "/s/", creds or _creds())
    return out, page


# ---------- classification matrix ----------

def test_classify_matrix():
    from primeqa.browser_worker.session import classify_inventory as c
    assert c(LOGIN_INV)[0] == "login"
    assert c(LOGIN_INV)[1] == {"username": 1, "password": 2, "submit": 3}
    assert c(MFA_INV) == ("mfa", {"code": 1, "verify": 2})
    assert c(HOME_INV) == ("unknown", None)
    assert c(TWO_PASSWORDS_INV) == ("unknown", None)      # ambiguity refused
    assert c(TWO_LOGIN_BUTTONS_INV) == ("unknown", None)  # ambiguity refused
    assert c({"inputs": [], "buttons": []}) == ("unknown", None)


# ---------- login flow + taxonomy ----------

def test_login_success_with_mfa():
    out, page = _login([LOGIN_INV, MFA_INV, HOME_INV],
                       [f"{BASE}/s/login/", f"{BASE}/verify", f"{BASE}/s/"])
    assert out.ok and out.events == ["LOGIN_SUBMITTED", "MFA_SUBMITTED"]
    assert page.closed
    # the code was filled into the MFA field: 6 digits, used once
    code_fills = [v for sel, v in page.fills if sel == '[data-plq="1"]'
                  and v.isdigit()]
    assert len(code_fills) == 1 and len(code_fills[0]) == 6


def test_login_success_without_mfa_prompt():
    out, _ = _login([LOGIN_INV, HOME_INV], [f"{BASE}/s/login/", f"{BASE}/s/"])
    assert out.ok and out.events == ["LOGIN_SUBMITTED", "MFA_NOT_PRESENTED"]


def test_bad_credential_is_permanent():
    from primeqa.browser_worker.session import BAD_CREDENTIAL, LoginError
    with pytest.raises(LoginError) as ei:
        _login([LOGIN_INV, LOGIN_INV], [f"{BASE}/s/login/", f"{BASE}/s/login/"])
    assert ei.value.code == BAD_CREDENTIAL and ei.value.retryable is False


def test_mfa_failed_is_retryable():
    from primeqa.browser_worker.session import MFA_FAILED, LoginError
    with pytest.raises(LoginError) as ei:
        _login([LOGIN_INV, MFA_INV, MFA_INV],
               [f"{BASE}/s/login/", f"{BASE}/verify", f"{BASE}/verify"])
    assert ei.value.code == MFA_FAILED and ei.value.retryable is True


def test_mfa_required_not_configured():
    from primeqa.browser_worker.session import (
        MFA_REQUIRED_NOT_CONFIGURED, LoginError)
    with pytest.raises(LoginError) as ei:
        _login([LOGIN_INV, MFA_INV], [f"{BASE}/s/login/", f"{BASE}/verify"],
               creds=_creds(seed=None))
    assert ei.value.code == MFA_REQUIRED_NOT_CONFIGURED
    assert ei.value.retryable is False


def test_login_page_not_recognized_initial_and_landed():
    from primeqa.browser_worker.session import (
        LOGIN_PAGE_NOT_RECOGNIZED, LoginError)
    with pytest.raises(LoginError) as ei:
        _login([HOME_INV], [f"{BASE}/s/"])
    assert ei.value.code == LOGIN_PAGE_NOT_RECOGNIZED
    assert "step=initial" in ei.value.detail
    with pytest.raises(LoginError) as ei2:
        _login([LOGIN_INV, HOME_INV], [f"{BASE}/s/login/", f"{BASE}/elsewhere"])
    assert ei2.value.code == LOGIN_PAGE_NOT_RECOGNIZED
    assert "step=landed" in ei2.value.detail


def test_session_lost_check():
    from primeqa.browser_worker.session import (
        SESSION_LOST, LoginError, assert_session)
    lost = FakePage([LOGIN_INV], [f"{BASE}/s/login/"])
    lost.url = f"{BASE}/s/login/"
    with pytest.raises(LoginError) as ei:
        assert_session(lost)
    assert ei.value.code == SESSION_LOST and ei.value.retryable is True
    fine = FakePage([HOME_INV], [])
    fine.url = f"{BASE}/s/"
    assert_session(fine)   # no raise


def test_credential_not_configured_from_env(monkeypatch):
    from primeqa.browser_worker.consume import _read_credentials
    from primeqa.browser_worker.session import (
        CREDENTIAL_NOT_CONFIGURED, LoginError)
    for v in ("PORTAL_USERNAME", "PORTAL_PASSWORD", "PORTAL_TOTP_SEED"):
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(LoginError) as ei:
        _read_credentials()
    assert ei.value.code == CREDENTIAL_NOT_CONFIGURED
    assert ei.value.retryable is False


# ---------- redaction ----------

def test_no_secret_reaches_logs_or_reprs(caplog):
    import pyotp
    caplog.set_level(logging.DEBUG, logger="primeqa.browser_worker.session")
    before = pyotp.TOTP(FAKE_SEED).now()
    out, page = _login([LOGIN_INV, MFA_INV, HOME_INV],
                       [f"{BASE}/s/login/", f"{BASE}/verify", f"{BASE}/s/"])
    after = pyotp.TOTP(FAKE_SEED).now()
    assert out.ok
    text = caplog.text
    for secret in (FAKE_PASS, FAKE_SEED, before, after, FAKE_USER):
        assert secret not in text, f"secret material leaked into logs"
    # events are logged by NAME
    assert "LOGIN_SUBMITTED" in text and "MFA_SUBMITTED" in text
    # reprs never carry values
    assert FAKE_PASS not in repr(_creds()) and FAKE_SEED not in str(_creds())
    from primeqa.browser_worker.session import LoginError
    assert FAKE_PASS not in str(LoginError("BAD_CREDENTIAL", "detail"))


# ---------- manifest auth descriptor (DB-gated) ----------

@pytest.mark.skipif(not SPIKE_DB, reason="set SPIKE_DATABASE_URL")
def test_manifest_auth_descriptor_round_trip():
    from sqlalchemy import text

    from primeqa.browser_worker import manifest as m
    from primeqa.browser_worker.queue import open_tenant_session

    s = open_tenant_session(1, SPIKE_DB)
    try:
        s.execute(text("DELETE FROM s4_ui_inspection_jobs"))
        s.commit()
        payload = {"surfaces": [{"key": "k", "url": f"{BASE}/s/"}],
                   "pins": {}, "stabilisation": {},
                   "execution": {"mode": "manual-spike"},
                   "auth": {"mode": "totp_env"}}
        mid = m.create_manifest(s, payload)
        job_id = m.enqueue_for_manifest(s, mid)
        row = s.execute(text("""
            SELECT payload FROM s4_ui_inspection_jobs WHERE id = :id
        """), {"id": job_id}).fetchone()
        assert row[0]["auth"] == {"mode": "totp_env"}
        assert set(row[0]) == {"surfaces", "stabilisation", "auth"}
        # the descriptor is the ONLY auth material anywhere in the rows
        blob = str(row[0]) + str(m.get_manifest(s, mid)["payload"])
        assert "password" not in blob.lower() and "seed" not in blob.lower()

        bad = dict(payload, auth={"mode": "basic"})
        mid2 = m.create_manifest(s, bad)
        with pytest.raises(ValueError):
            m.enqueue_for_manifest(s, mid2)
    finally:
        s.close()
