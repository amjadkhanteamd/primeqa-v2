"""Auth decorators for route protection.

require_auth — extracts and validates JWT, sets request.user
require_role — chains with require_auth to enforce role-based access
"""

import os
from functools import wraps

import jwt
from flask import request, jsonify
from primeqa.shared.api import json_error


def _get_jwt_secret():
    # F-3: fail-closed in production (raise on unset/empty/dev-default) so tokens
    # are never signed/verified with a forgeable secret. core/secrets is the
    # single resolution chokepoint.
    from primeqa.core.secrets import get_jwt_secret
    return get_jwt_secret()


INACTIVE_REASON = "account_inactive"


def session_is_active(user_id, tenant_id) -> bool:
    """THE CHOKEPOINT (AUD-038, D-499): the account's ``is_active`` flag is
    read on EVERY authenticated request — one indexed SELECT — so a
    deactivation takes effect on the deactivated user's next request,
    whatever their token still says. Before this, the flag was read only at
    login and at refresh: a deactivated session read and wrote for the access
    token's 30-minute life.

    Fail closed: a missing row, an inactive row, or a read that fails all
    answer False (unknown is not active). Memoised per request on ``g`` so
    the web and API decorators, when both run, read once."""
    from flask import g
    key = "_plimsol_session_active"
    cached = getattr(g, key, None)
    if cached is not None and cached[0] == (user_id, tenant_id):
        return cached[1]
    active = False
    try:
        from sqlalchemy import text

        from primeqa import db as dbm
        with dbm.engine.connect() as conn:
            row = conn.execute(text(
                "SELECT is_active FROM users WHERE id = :u AND tenant_id = :t"),
                {"u": int(user_id), "t": int(tenant_id)}).first()
        active = bool(row and row[0])
    except Exception as exc:  # noqa: BLE001 — unknown is not active
        import logging
        logging.getLogger("primeqa.auth").warning(
            "session activity check failed for user %s tenant %s: %s — refusing", user_id, tenant_id, exc)
        active = False
    setattr(g, key, ((user_id, tenant_id), active))
    return active


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # Accept either:
        #   - Authorization: Bearer <jwt>  (traditional API clients)
        #   - access_token cookie          (httponly cookie used by the web UI)
        # so same-origin AJAX from the rendered pages can hit /api/* without
        # shipping the JWT through JS.
        auth_header = request.headers.get("Authorization", "")
        token = None
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        elif request.cookies.get("access_token"):
            token = request.cookies.get("access_token")

        if not token:
            return json_error("UNAUTHORIZED", "Missing or invalid Authorization header", http=401)

        try:
            payload = jwt.decode(token, _get_jwt_secret(), algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return json_error("TOKEN_EXPIRED", "Token expired", http=401)
        except jwt.InvalidTokenError:
            return json_error("UNAUTHORIZED", "Invalid token", http=401)

        # Audit fix C-4: tolerate missing / malformed claims — treat as
        # invalid token rather than crashing with KeyError. `sub` and
        # `tenant_id` are required; others default.
        if "sub" not in payload or "tenant_id" not in payload:
            return json_error("UNAUTHORIZED", "Malformed token", http=401)
        try:
            request.user = {
                "id": int(payload["sub"]),
                "tenant_id": payload["tenant_id"],
                "email": payload.get("email", ""),
                "role": payload.get("role", "viewer"),
                "full_name": payload.get("full_name", ""),
            }
        except (ValueError, TypeError):
            return json_error("UNAUTHORIZED", "Malformed token", http=401)
        # AUD-038: a deactivated account is refused HERE, before the view.
        if not session_is_active(request.user["id"], request.user["tenant_id"]):
            return json_error("ACCOUNT_INACTIVE", "This account is deactivated.", http=401)
        return f(*args, **kwargs)
    return decorated


def require_role(*roles):
    """D-245 Phase 6: thin wrapper over the role ladder for **API** routes. Gates
    at ``floor_tier(roles)`` (the lowest listed role's tier) via the same
    ``authorize()`` path as ``require_tier_api`` — superadmin still passes (ladder
    top), and a list naming ``tester`` now also admits ``ba`` (both ``MEMBER``),
    the one intended widening. The explicit role names stay at call sites as
    living documentation of audience."""
    from primeqa.core.authz import floor_tier
    return require_tier_api(floor_tier(roles))


def require_tier_api(min_tier):
    """D-245 Phase-2 transitional role gate for **API** routes — the new
    ``authorize()`` path, applied as the OUTER decorator (fail-closed). Enforces
    auth then the minimum role tier, returning a 403 envelope on deny (matches
    ``require_role``'s API contract). The permanent replacement gate for the
    permission decorators removed in Phase 5."""
    from primeqa.core.authz import authorize

    def decorator(f):
        @wraps(f)
        @require_auth
        def decorated(*args, **kwargs):
            allow, _reason = authorize(getattr(request, "user", None), min_tier)
            if not allow:
                return json_error("FORBIDDEN", "Insufficient permissions", http=403)
            return f(*args, **kwargs)
        return decorated
    return decorator
