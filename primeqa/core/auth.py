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
    return os.getenv("JWT_SECRET", "dev-secret-change-me")


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
            return jsonify(error="Token expired", code="TOKEN_EXPIRED"), 401
        except jwt.InvalidTokenError:
            return json_error("UNAUTHORIZED", "Invalid token", http=401)

        request.user = {
            "id": int(payload["sub"]),
            "tenant_id": payload["tenant_id"],
            "email": payload["email"],
            "role": payload["role"],
            "full_name": payload["full_name"],
        }
        return f(*args, **kwargs)
    return decorated


def require_role(*roles):
    def decorator(f):
        @wraps(f)
        @require_auth
        def decorated(*args, **kwargs):
            # Super Admin is a tenant-wide god-mode role: always passes every
            # role check (Q: "god mode"). Seeded per tenant, excluded from the
            # 20-user cap elsewhere.
            role = request.user["role"]
            if role == "superadmin":
                return f(*args, **kwargs)
            if role not in roles:
                return json_error("FORBIDDEN", "Insufficient permissions", http=403)
            return f(*args, **kwargs)
        return decorated
    return decorator
