"""API routes for the core domain.

Endpoints: /api/auth/*, /api/users/*, /api/environments/*, /api/connections/*, /api/groups/*
"""

from flask import Blueprint, jsonify, request

from primeqa.core.auth import require_auth, require_role
from primeqa.db import get_db
from primeqa.core.repository import (
    UserRepository, RefreshTokenRepository, EnvironmentRepository,
    ConnectionRepository, GroupRepository,
)
from primeqa.core.service import AuthService, EnvironmentService, ConnectionService, GroupService
from primeqa.shared.api import json_error

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
    """Audit fix C-1 (2026-04-19): tenant_id is NO LONGER accepted from
    the client. Previously `tenant_id = data.get("tenant_id", 1)` let
    anyone bypass multi-tenant isolation by guessing tenant ids. The
    service now derives tenant from the email record on the user table."""
    data = request.get_json(silent=True) or {}
    email = data.get("email")
    password = data.get("password")

    if not email or not password:
        return json_error("VALIDATION_ERROR", "email and password are required", http=400)
    if not isinstance(email, str) or not isinstance(password, str):
        return json_error("VALIDATION_ERROR",
                          "email and password must be strings", http=400)

    svc, db = _get_auth_service()
    try:
        result = svc.login(email, password)
        if not result:
            return json_error("UNAUTHORIZED", "Invalid email or password", http=401)
        return jsonify(result), 200
    finally:
        db.close()


@core_bp.route("/api/auth/refresh", methods=["POST"])
def refresh():
    data = request.get_json(silent=True) or {}
    refresh_token = data.get("refresh_token")

    if not refresh_token:
        return json_error("VALIDATION_ERROR", "refresh_token is required", http=400)

    svc, db = _get_auth_service()
    try:
        result = svc.refresh(refresh_token)
        if not result:
            return json_error("UNAUTHORIZED", "Invalid or expired refresh token", http=401)
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
            return json_error("NOT_FOUND", "User not found", http=404)
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
        return json_error("VALIDATION_ERROR",
                          f"Missing required fields: {', '.join(missing)}",
                          http=400)

    if data["role"] not in ("admin", "tester", "ba", "viewer"):
        return json_error("VALIDATION_ERROR", "Invalid role", http=400)

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
        # Duplicate-email + tenant-cap are conflict states (resource
        # already exists / state prevents the op), not validation errors.
        msg = str(e)
        low = msg.lower()
        if "already exists" in low or "maximum of" in low:
            code = "CONFLICT" if "already exists" in low else "TENANT_CAP"
            return json_error(code, msg, http=409)
        return json_error("VALIDATION_ERROR", msg, http=400)
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
        return json_error("VALIDATION_ERROR", str(e), http=400)
    finally:
        db.close()


# --- Environments ---

@core_bp.route("/api/environments", methods=["GET"])
@require_auth
def list_environments():
    svc, db = _get_env_service()
    try:
        envs = svc.list_environments(
            request.user["tenant_id"], request.user["id"], request.user["role"],
        )
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
            created_by=request.user["id"],
            **({} if "cleanup_mandatory" not in data else {"cleanup_mandatory": data["cleanup_mandatory"]}),
        )
        return jsonify(env), 201
    except ValueError as e:
        return json_error("VALIDATION_ERROR", str(e), http=400)
    finally:
        db.close()


@core_bp.route("/api/environments/<int:env_id>", methods=["GET"])
@require_auth
def get_environment(env_id):
    svc, db = _get_env_service()
    try:
        env = svc.get_environment(env_id, request.user["tenant_id"])
        if not env:
            return json_error("NOT_FOUND", "Environment not found", http=404)
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
        return json_error("VALIDATION_ERROR", str(e), http=400)
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
        return json_error("VALIDATION_ERROR", str(e), http=400)
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
        return json_error("VALIDATION_ERROR", str(e), http=400)
    finally:
        db.close()


# --- Connections ---

def _get_conn_service():
    db = next(get_db())
    return ConnectionService(ConnectionRepository(db)), db


@core_bp.route("/api/connections", methods=["GET"])
@require_auth
def list_connections():
    svc, db = _get_conn_service()
    try:
        return jsonify(svc.list_connections(request.user["tenant_id"], request.args.get("type"))), 200
    finally:
        db.close()


@core_bp.route("/api/connections", methods=["POST"])
@require_role("admin")
def create_connection():
    data = request.get_json(silent=True) or {}
    for f in ["connection_type", "name", "config"]:
        if not data.get(f):
            return json_error("VALIDATION_ERROR", f"{f} is required", http=400)
    svc, db = _get_conn_service()
    try:
        conn = svc.create_connection(request.user["tenant_id"], data["connection_type"],
                                     data["name"], data["config"], request.user["id"])
        return jsonify(conn), 201
    except ValueError as e:
        return json_error("VALIDATION_ERROR", str(e), http=400)
    finally:
        db.close()


@core_bp.route("/api/connections/<int:conn_id>", methods=["GET"])
@require_auth
def get_connection(conn_id):
    svc, db = _get_conn_service()
    try:
        conn = svc.get_connection(conn_id, request.user["tenant_id"])
        if not conn:
            return json_error("NOT_FOUND", "Connection not found", http=404)
        return jsonify(conn), 200
    finally:
        db.close()


@core_bp.route("/api/connections/<int:conn_id>", methods=["DELETE"])
@require_role("admin")
def delete_connection(conn_id):
    svc, db = _get_conn_service()
    try:
        svc.delete_connection(conn_id, request.user["tenant_id"])
        return jsonify(message="Deleted"), 200
    except ValueError as e:
        return json_error("VALIDATION_ERROR", str(e), http=400)
    finally:
        db.close()


@core_bp.route("/api/connections/<int:conn_id>/test", methods=["POST"])
@require_role("admin")
def test_connection_api(conn_id):
    svc, db = _get_conn_service()
    try:
        result = svc.test_connection(conn_id, request.user["tenant_id"])
        return jsonify(result), 200
    except ValueError as e:
        return json_error("VALIDATION_ERROR", str(e), http=400)
    finally:
        db.close()


# --- Groups ---

def _get_group_service():
    db = next(get_db())
    return GroupService(GroupRepository(db)), db


@core_bp.route("/api/groups", methods=["GET"])
@require_auth
def list_groups():
    svc, db = _get_group_service()
    try:
        return jsonify(svc.list_groups(request.user["tenant_id"], request.user["id"], request.user["role"])), 200
    finally:
        db.close()


@core_bp.route("/api/groups", methods=["POST"])
@require_role("admin")
def create_group():
    data = request.get_json(silent=True) or {}
    if not data.get("name"):
        return json_error("VALIDATION_ERROR", "name is required", http=400)
    svc, db = _get_group_service()
    try:
        return jsonify(svc.create_group(request.user["tenant_id"], data["name"],
                                        request.user["id"], data.get("description"))), 201
    finally:
        db.close()


@core_bp.route("/api/groups/<int:group_id>", methods=["GET"])
@require_auth
def get_group(group_id):
    svc, db = _get_group_service()
    try:
        detail = svc.get_group_detail(group_id, request.user["tenant_id"])
        if not detail:
            return json_error("NOT_FOUND", "Group not found", http=404)
        return jsonify(detail), 200
    finally:
        db.close()


@core_bp.route("/api/groups/<int:group_id>", methods=["DELETE"])
@require_role("admin")
def delete_group(group_id):
    svc, db = _get_group_service()
    try:
        svc.delete_group(group_id, request.user["tenant_id"])
        return jsonify(message="Deleted"), 200
    except ValueError as e:
        return json_error("VALIDATION_ERROR", str(e), http=400)
    finally:
        db.close()


@core_bp.route("/api/groups/<int:group_id>/members", methods=["POST"])
@require_role("admin")
def add_group_member(group_id):
    data = request.get_json(silent=True) or {}
    if not data.get("user_id"):
        return json_error("VALIDATION_ERROR", "user_id is required", http=400)
    svc, db = _get_group_service()
    try:
        svc.add_member(group_id, request.user["tenant_id"], data["user_id"], request.user["id"])
        return jsonify(message="Added"), 200
    except ValueError as e:
        return json_error("VALIDATION_ERROR", str(e), http=400)
    finally:
        db.close()


@core_bp.route("/api/groups/<int:group_id>/members/<int:user_id>", methods=["DELETE"])
@require_role("admin")
def remove_group_member(group_id, user_id):
    svc, db = _get_group_service()
    try:
        svc.remove_member(group_id, request.user["tenant_id"], user_id)
        return jsonify(message="Removed"), 200
    except ValueError as e:
        return json_error("VALIDATION_ERROR", str(e), http=400)
    finally:
        db.close()


@core_bp.route("/api/groups/<int:group_id>/environments", methods=["POST"])
@require_role("admin")
def add_group_environment(group_id):
    data = request.get_json(silent=True) or {}
    if not data.get("environment_id"):
        return json_error("VALIDATION_ERROR", "environment_id is required", http=400)
    svc, db = _get_group_service()
    try:
        svc.add_environment(group_id, request.user["tenant_id"], data["environment_id"], request.user["id"])
        return jsonify(message="Added"), 200
    except ValueError as e:
        return json_error("VALIDATION_ERROR", str(e), http=400)
    finally:
        db.close()


@core_bp.route("/api/groups/<int:group_id>/environments/<int:env_id>", methods=["DELETE"])
@require_role("admin")
def remove_group_environment(group_id, env_id):
    svc, db = _get_group_service()
    try:
        svc.remove_environment(group_id, request.user["tenant_id"], env_id)
        return jsonify(message="Removed"), 200
    except ValueError as e:
        return json_error("VALIDATION_ERROR", str(e), http=400)
    finally:
        db.close()
