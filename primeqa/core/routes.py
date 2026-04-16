"""API routes for the core domain.

Endpoints: /api/auth/*, /api/users/*, /api/environments/*
"""

from flask import Blueprint, jsonify, request

from primeqa.core.auth import require_auth, require_role
from primeqa.db import get_db
from primeqa.core.repository import UserRepository, RefreshTokenRepository
from primeqa.core.service import AuthService

core_bp = Blueprint("core", __name__)


def _get_auth_service():
    db = next(get_db())
    user_repo = UserRepository(db)
    token_repo = RefreshTokenRepository(db)
    return AuthService(user_repo, token_repo), db


# --- Auth ---

@core_bp.route("/api/auth/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = data.get("email")
    password = data.get("password")
    tenant_id = data.get("tenant_id", 1)

    if not email or not password:
        return jsonify(error="email and password are required"), 400

    svc, db = _get_auth_service()
    try:
        result = svc.login(tenant_id, email, password)
        if not result:
            return jsonify(error="Invalid email or password"), 401
        return jsonify(result), 200
    finally:
        db.close()


@core_bp.route("/api/auth/refresh", methods=["POST"])
def refresh():
    data = request.get_json(silent=True) or {}
    refresh_token = data.get("refresh_token")

    if not refresh_token:
        return jsonify(error="refresh_token is required"), 400

    svc, db = _get_auth_service()
    try:
        result = svc.refresh(refresh_token)
        if not result:
            return jsonify(error="Invalid or expired refresh token"), 401
        return jsonify(result), 200
    finally:
        db.close()


@core_bp.route("/api/auth/logout", methods=["POST"])
@require_auth
def logout():
    svc, db = _get_auth_service()
    try:
        svc.logout(request.user["id"])
        return jsonify(message="Logged out"), 200
    finally:
        db.close()


@core_bp.route("/api/auth/me", methods=["GET"])
@require_auth
def me():
    svc, db = _get_auth_service()
    try:
        user = svc.get_user(request.user["id"])
        if not user:
            return jsonify(error="User not found"), 404
        return jsonify(user), 200
    finally:
        db.close()


# --- Users (admin only) ---

@core_bp.route("/api/auth/users", methods=["GET"])
@require_role("admin")
def list_users():
    svc, db = _get_auth_service()
    try:
        users = svc.list_users(request.user["tenant_id"])
        return jsonify(users), 200
    finally:
        db.close()


@core_bp.route("/api/auth/users", methods=["POST"])
@require_role("admin")
def create_user():
    data = request.get_json(silent=True) or {}
    required = ["email", "password", "full_name", "role"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify(error=f"Missing required fields: {', '.join(missing)}"), 400

    if data["role"] not in ("admin", "tester", "ba", "viewer"):
        return jsonify(error="Invalid role"), 400

    svc, db = _get_auth_service()
    try:
        user = svc.create_user(
            tenant_id=request.user["tenant_id"],
            email=data["email"],
            password=data["password"],
            full_name=data["full_name"],
            role=data["role"],
        )
        return jsonify(user), 201
    except ValueError as e:
        return jsonify(error=str(e)), 409
    finally:
        db.close()


@core_bp.route("/api/auth/users/<int:user_id>", methods=["PATCH"])
@require_role("admin")
def update_user(user_id):
    data = request.get_json(silent=True) or {}
    svc, db = _get_auth_service()
    try:
        user = svc.update_user(user_id, **data)
        return jsonify(user), 200
    except ValueError as e:
        return jsonify(error=str(e)), 400
    finally:
        db.close()


# --- Environments (stubs) ---

@core_bp.route("/api/environments", methods=["GET"])
@require_auth
def list_environments():
    return jsonify(error="Not Implemented"), 501


@core_bp.route("/api/environments", methods=["POST"])
@require_role("admin")
def create_environment():
    return jsonify(error="Not Implemented"), 501


@core_bp.route("/api/environments/<int:env_id>", methods=["PATCH"])
@require_role("admin")
def update_environment(env_id):
    return jsonify(error="Not Implemented"), 501


@core_bp.route("/api/environments/<int:env_id>/test-connection", methods=["POST"])
@require_role("admin")
def test_connection(env_id):
    return jsonify(error="Not Implemented"), 501


@core_bp.route("/api/environments/<int:env_id>/credentials", methods=["POST"])
@require_role("admin")
def store_credentials(env_id):
    return jsonify(error="Not Implemented"), 501
