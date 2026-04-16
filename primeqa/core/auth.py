"""Auth decorators for route protection.

require_auth — extracts and validates JWT, sets request.user
require_role — chains with require_auth to enforce role-based access
"""

import os
from functools import wraps

import jwt
from flask import request, jsonify


def _get_jwt_secret():
    return os.getenv("JWT_SECRET", "dev-secret-change-me")


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify(error="Missing or invalid Authorization header"), 401

        token = auth_header[7:]
        try:
            payload = jwt.decode(token, _get_jwt_secret(), algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return jsonify(error="Token expired", code="TOKEN_EXPIRED"), 401
        except jwt.InvalidTokenError:
            return jsonify(error="Invalid token"), 401

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
            if request.user["role"] not in roles:
                return jsonify(error="Insufficient permissions"), 403
            return f(*args, **kwargs)
        return decorated
    return decorator
