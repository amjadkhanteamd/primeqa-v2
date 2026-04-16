"""API routes for the core domain.

Endpoints: /api/auth/*, /api/users/*, /api/environments/*
"""

from flask import Blueprint, jsonify, request

from primeqa.core.auth import require_auth, require_role
from primeqa.db import get_db
from primeqa.core.repository import UserRepository, RefreshTokenRepository, EnvironmentRepository
from primeqa.core.service import AuthService, EnvironmentService

core_bp = Blueprint("core", __name__)


def _get_auth_service():
    db = next(get_db())
    user_repo = UserRepository(db)
    token_repo = RefreshTokenRepository(db)
    return AuthService(user_repo, token_repo), db


def _get_env_service():
    db = next(get_db())
    env_repo = EnvironmentRepository(db)
    return EnvironmentService(env_repo), db


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


# --- Environments ---

@core_bp.route("/api/environments", methods=["GET"])
@require_auth
def list_environments():
    svc, db = _get_env_service()
    try:
        envs = svc.list_environments(request.user["tenant_id"])
        return jsonify(envs), 200
    finally:
        db.close()


@core_bp.route("/api/environments", methods=["POST"])
@require_role("admin")
def create_environment():
    data = request.get_json(silent=True) or {}
    required = ["name", "env_type", "sf_instance_url", "sf_api_version"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify(error=f"Missing required fields: {', '.join(missing)}"), 400

    svc, db = _get_env_service()
    try:
        env = svc.create_environment(
            tenant_id=request.user["tenant_id"],
            name=data["name"],
            env_type=data["env_type"],
            sf_instance_url=data["sf_instance_url"],
            sf_api_version=data["sf_api_version"],
            execution_policy=data.get("execution_policy", "full"),
            capture_mode=data.get("capture_mode", "smart"),
            max_execution_slots=data.get("max_execution_slots", 2),
            **({} if "cleanup_mandatory" not in data else {"cleanup_mandatory": data["cleanup_mandatory"]}),
        )
        return jsonify(env), 201
    except ValueError as e:
        return jsonify(error=str(e)), 400
    finally:
        db.close()


@core_bp.route("/api/environments/<int:env_id>", methods=["GET"])
@require_auth
def get_environment(env_id):
    svc, db = _get_env_service()
    try:
        env = svc.get_environment(env_id, request.user["tenant_id"])
        if not env:
            return jsonify(error="Environment not found"), 404
        return jsonify(env), 200
    finally:
        db.close()


@core_bp.route("/api/environments/<int:env_id>", methods=["PATCH"])
@require_role("admin")
def update_environment(env_id):
    data = request.get_json(silent=True) or {}
    svc, db = _get_env_service()
    try:
        env = svc.update_environment(env_id, request.user["tenant_id"], data)
        return jsonify(env), 200
    except ValueError as e:
        return jsonify(error=str(e)), 400
    finally:
        db.close()


@core_bp.route("/api/environments/<int:env_id>/test-connection", methods=["POST"])
@require_role("admin")
def test_connection(env_id):
    svc, db = _get_env_service()
    try:
        result = svc.test_connection(env_id, request.user["tenant_id"])
        return jsonify(result), 200
    except ValueError as e:
        return jsonify(error=str(e)), 400
    finally:
        db.close()


@core_bp.route("/api/environments/<int:env_id>/credentials", methods=["POST"])
@require_role("admin")
def store_credentials(env_id):
    data = request.get_json(silent=True) or {}
    required = ["client_id", "client_secret"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify(error=f"Missing required fields: {', '.join(missing)}"), 400

    svc, db = _get_env_service()
    try:
        result = svc.store_credentials(
            environment_id=env_id,
            tenant_id=request.user["tenant_id"],
            client_id=data["client_id"],
            client_secret=data["client_secret"],
            access_token=data.get("access_token"),
            refresh_token=data.get("refresh_token"),
        )
        return jsonify(result), 200
    except ValueError as e:
        return jsonify(error=str(e)), 400
    finally:
        db.close()
