"""API routes for the core domain.

Endpoints: /api/auth/*, /api/users/*, /api/environments/*
"""

from flask import Blueprint, jsonify

core_bp = Blueprint("core", __name__)


# --- Auth ---

@core_bp.route("/api/auth/login", methods=["POST"])
def login():
    return jsonify(error="Not Implemented"), 501


@core_bp.route("/api/auth/refresh", methods=["POST"])
def refresh():
    return jsonify(error="Not Implemented"), 501


@core_bp.route("/api/auth/logout", methods=["POST"])
def logout():
    return jsonify(error="Not Implemented"), 501


@core_bp.route("/api/auth/me", methods=["GET"])
def me():
    return jsonify(error="Not Implemented"), 501


# --- Users ---

@core_bp.route("/api/auth/users", methods=["GET"])
def list_users():
    return jsonify(error="Not Implemented"), 501


@core_bp.route("/api/auth/users", methods=["POST"])
def create_user():
    return jsonify(error="Not Implemented"), 501


@core_bp.route("/api/auth/users/<int:user_id>", methods=["PATCH"])
def update_user(user_id):
    return jsonify(error="Not Implemented"), 501


# --- Environments ---

@core_bp.route("/api/environments", methods=["GET"])
def list_environments():
    return jsonify(error="Not Implemented"), 501


@core_bp.route("/api/environments", methods=["POST"])
def create_environment():
    return jsonify(error="Not Implemented"), 501


@core_bp.route("/api/environments/<int:env_id>", methods=["PATCH"])
def update_environment(env_id):
    return jsonify(error="Not Implemented"), 501


@core_bp.route("/api/environments/<int:env_id>/test-connection", methods=["POST"])
def test_connection(env_id):
    return jsonify(error="Not Implemented"), 501


@core_bp.route("/api/environments/<int:env_id>/credentials", methods=["POST"])
def store_credentials(env_id):
    return jsonify(error="Not Implemented"), 501
