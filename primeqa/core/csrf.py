"""Double-submit-cookie CSRF protection.

Installed via `install(app)` in app.py. Three moving parts:

1. `after_request` sets a `csrf_token` cookie (httponly=False — the
   client JS needs to read it and echo it back) if none is present.
   The token is 32 bytes of os.urandom, base64-url-safe encoded.

2. `before_request` rejects state-changing requests (POST/PUT/PATCH/
   DELETE) that don't carry a token matching the cookie, UNLESS the
   request is on a safelisted path (login, refresh, webhooks) or an
   /api/* endpoint (those use Bearer-token auth which is immune to
   classic CSRF). The token can arrive via:
     - `X-CSRF-Token` header   (preferred for AJAX; added by csrf.js)
     - `csrf_token` form field (for server-rendered HTML forms)
     - `csrf_token` query arg  (rare — used by some link-based actions)

3. Template context processor exposes `csrf_token` + `csrf_input` so
   forms can drop `{{ csrf_input | safe }}` inline and get a hidden
   input rendered.

Not Flask-WTF. Flask-WTF would pull `wtforms` for form generation we
don't use. This is ~60 lines, does exactly what we need, and is easy
to read.

The token is NOT tied to user identity — double-submit works purely
on "the attacker can't read our cookies." A logged-out user gets a
CSRF token too, which is correct: login uses it too.

Tested exceptions (no CSRF required):
    /login                 (user has no cookie yet — chicken/egg)
    /api/auth/login        (same)
    /api/auth/refresh      (same)
    /api/webhooks/*        (CI/CD webhooks use HMAC, not CSRF)
    /health, /api/_internal/health
    All /api/* requests that carry `Authorization: Bearer ...`
      (JWT Bearer is already unforgeable cross-origin)
"""

from __future__ import annotations

import base64
import logging
import os
import secrets
from functools import lru_cache
from typing import Iterable

log = logging.getLogger(__name__)


COOKIE_NAME = "csrf_token"
HEADER_NAME = "X-CSRF-Token"
FORM_FIELD = "csrf_token"

# Paths that skip CSRF entirely (startswith match).
_SAFE_PATH_PREFIXES = (
    "/login",
    "/static/",
    "/health",
    "/api/_internal/",
    "/api/auth/login",
    "/api/auth/refresh",
    "/api/webhooks/",
)

# Methods that mutate state.
_UNSAFE_METHODS = frozenset(["POST", "PUT", "PATCH", "DELETE"])


def _generate_token() -> str:
    """32 random bytes, URL-safe base64. ~43 chars. Good enough for
    128-bit security; the double-submit protocol doesn't require more."""
    raw = secrets.token_bytes(32)
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _path_is_safe(path: str) -> bool:
    return any(path.startswith(p) for p in _SAFE_PATH_PREFIXES)


def _has_bearer_auth(request) -> bool:
    """/api/* with a Bearer token doesn't need CSRF — Bearer auth
    requires a header the attacker can't set cross-origin."""
    if not request.path.startswith("/api/"):
        return False
    auth = request.headers.get("Authorization", "")
    return auth.startswith("Bearer ")


def _tokens_match(submitted: str, cookie: str) -> bool:
    if not submitted or not cookie:
        return False
    # Constant-time comparison so timing doesn't leak the cookie.
    return secrets.compare_digest(submitted, cookie)


def install(app) -> None:
    """Wire the CSRF protection into a Flask app. Call once from
    create_app() after blueprints are registered."""
    from flask import request, g, make_response, jsonify

    # ---- before_request: validate incoming -------------------------------
    @app.before_request
    def _check_csrf():
        req = request
        if req.method not in _UNSAFE_METHODS:
            return None
        if _path_is_safe(req.path):
            return None
        if _has_bearer_auth(req):
            return None
        cookie = req.cookies.get(COOKIE_NAME, "")
        submitted = (
            req.headers.get(HEADER_NAME)
            or (req.form.get(FORM_FIELD) if req.form else None)
            or req.args.get(FORM_FIELD)
            or ""
        )
        if not _tokens_match(submitted, cookie):
            log.warning(
                "csrf rejected path=%s method=%s has_cookie=%s has_token=%s",
                req.path, req.method, bool(cookie), bool(submitted),
            )
            # Envelope for /api/*, plain text for HTML pages.
            if req.path.startswith("/api/"):
                return jsonify({"error": {
                    "code": "CSRF_FAILED",
                    "message": "Missing or invalid CSRF token",
                }}), 403
            return (
                "CSRF token missing or invalid. "
                "Refresh the page and try again.",
                403,
            )
        return None

    # ---- after_request: set cookie if absent -----------------------------
    @app.after_request
    def _set_csrf_cookie(response):
        # Don't touch responses that already have the header set (tests etc.)
        if COOKIE_NAME in request.cookies:
            return response
        # Mint a fresh token. Same-site=Lax prevents cross-site form POSTs
        # that would otherwise submit our own cookie. Secure when not
        # local. HttpOnly=False because JS needs to read it for fetch().
        token = _generate_token()
        response.set_cookie(
            COOKIE_NAME, token,
            max_age=60 * 60 * 24 * 30,   # 30 days
            httponly=False,
            secure=(os.getenv("FLASK_ENV") == "production"),
            samesite="Lax",
        )
        # Stash on g so the context processor below can surface the
        # freshly-minted value to the template on this very render.
        g._pqa_fresh_csrf = token
        return response

    # ---- template context ------------------------------------------------
    @app.context_processor
    def _inject_csrf_token():
        # Prefer the cookie value (stable across forms); fall back to
        # the freshly minted token for the first response of a session.
        token = (
            request.cookies.get(COOKIE_NAME)
            or getattr(g, "_pqa_fresh_csrf", None)
            or ""
        )
        csrf_input = (
            f'<input type="hidden" name="{FORM_FIELD}" '
            f'value="{token}">'
        )
        return {"csrf_token": token, "csrf_input": csrf_input}


def skip_paths() -> Iterable[str]:
    """Expose the safe-path list for introspection / tests."""
    return _SAFE_PATH_PREFIXES
