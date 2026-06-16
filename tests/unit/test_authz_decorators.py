"""Unit tests for the Phase-2 transitional role-gate decorators (D-245).

DB-free: a minimal Flask app + monkeypatched user resolution proves the
wiring — below-tier is rejected, at-tier is allowed — for both the web
(``require_tier``, redirect-on-deny) and API (``require_tier_api``, 403) gates.
"""
from functools import wraps

import pytest
from flask import Flask, request

from primeqa.core.authz import Tier


@pytest.fixture
def app():
    return Flask(__name__)


def _set_user(role):
    request.user = {"id": 1, "tenant_id": 1, "role": role}


class TestRequireTierWeb:
    """``require_tier`` (views.py) — redirect('/') on deny."""

    def _gated(self, monkeypatch, role, min_tier):
        import primeqa.views as views
        monkeypatch.setattr(views, "get_current_user",
                            lambda: {"id": 1, "tenant_id": 1, "role": role})

        @views.require_tier(min_tier)
        def protected():
            return "OK"

        return protected

    def test_at_tier_allows(self, app, monkeypatch):
        fn = self._gated(monkeypatch, "tester", Tier.MEMBER)  # tester == Member
        with app.test_request_context("/"):
            assert fn() == "OK"

    def test_below_tier_redirects(self, app, monkeypatch):
        fn = self._gated(monkeypatch, "viewer", Tier.MEMBER)  # viewer < Member
        with app.test_request_context("/"):
            resp = fn()
            assert getattr(resp, "status_code", None) == 302  # redirect('/')

    def test_admin_gate_blocks_member(self, app, monkeypatch):
        fn = self._gated(monkeypatch, "tester", Tier.ADMIN)
        with app.test_request_context("/"):
            assert getattr(fn(), "status_code", None) == 302

    def test_superadmin_passes_admin_gate(self, app, monkeypatch):
        fn = self._gated(monkeypatch, "superadmin", Tier.ADMIN)
        with app.test_request_context("/"):
            assert fn() == "OK"

    def test_ba_is_member(self, app, monkeypatch):
        fn = self._gated(monkeypatch, "ba", Tier.MEMBER)
        with app.test_request_context("/"):
            assert fn() == "OK"


class TestRequireTierApi:
    """``require_tier_api`` (core/auth.py) — 403 envelope on deny."""

    def _gated(self, monkeypatch, role, min_tier):
        import primeqa.core.auth as auth_mod

        def fake_require_auth(f):
            @wraps(f)
            def d(*a, **k):
                _set_user(role)
                return f(*a, **k)
            return d

        monkeypatch.setattr(auth_mod, "require_auth", fake_require_auth)

        @auth_mod.require_tier_api(min_tier)
        def protected():
            return "OK"

        return protected

    def test_at_tier_allows(self, app, monkeypatch):
        fn = self._gated(monkeypatch, "tester", Tier.MEMBER)
        with app.test_request_context("/"):
            assert fn() == "OK"

    def test_below_tier_403(self, app, monkeypatch):
        fn = self._gated(monkeypatch, "viewer", Tier.MEMBER)
        with app.test_request_context("/"):
            resp = fn()
            # json_error returns (envelope, status) or a Response; both expose 403
            status = resp[1] if isinstance(resp, tuple) else getattr(resp, "status_code", None)
            assert status == 403

    def test_admin_gate_blocks_member(self, app, monkeypatch):
        fn = self._gated(monkeypatch, "tester", Tier.ADMIN)
        with app.test_request_context("/"):
            resp = fn()
            status = resp[1] if isinstance(resp, tuple) else getattr(resp, "status_code", None)
            assert status == 403

    def test_admin_allows_admin(self, app, monkeypatch):
        fn = self._gated(monkeypatch, "admin", Tier.ADMIN)
        with app.test_request_context("/"):
            assert fn() == "OK"
