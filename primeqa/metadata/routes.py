"""API routes for the metadata domain.

Endpoints: /api/metadata/*
"""

from flask import Blueprint, jsonify, request

from primeqa.core.auth import require_auth, require_role
from primeqa.db import get_db
from primeqa.core.repository import EnvironmentRepository
from primeqa.metadata.repository import MetadataRepository
from primeqa.metadata.service import MetadataService

metadata_bp = Blueprint("metadata", __name__)


def _get_metadata_service():
    db = next(get_db())
    metadata_repo = MetadataRepository(db)
    env_repo = EnvironmentRepository(db)
    return MetadataService(metadata_repo, env_repo), db


@metadata_bp.route("/api/metadata/<int:environment_id>/refresh", methods=["POST"])
@require_role("admin", "tester")
def refresh_metadata(environment_id):
    svc, db = _get_metadata_service()
    try:
        result = svc.refresh_metadata(environment_id, request.user["tenant_id"])
        return jsonify(result), 200
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except Exception as e:
        return jsonify(error=f"Metadata refresh failed: {str(e)}"), 500
    finally:
        db.close()


@metadata_bp.route("/api/metadata/<int:environment_id>/current", methods=["GET"])
@require_auth
def get_current_version(environment_id):
    svc, db = _get_metadata_service()
    try:
        result = svc.get_current_version_summary(environment_id)
        if not result:
            return jsonify(error="No metadata version found"), 404
        return jsonify(result), 200
    finally:
        db.close()


@metadata_bp.route("/api/metadata/<int:environment_id>/diff", methods=["GET"])
@require_auth
def get_diff(environment_id):
    svc, db = _get_metadata_service()
    try:
        result = svc.get_diff(environment_id)
        if not result:
            return jsonify(error="No diff available (need at least 2 versions)"), 404
        return jsonify(result), 200
    finally:
        db.close()


@metadata_bp.route("/api/metadata/<int:environment_id>/impacts", methods=["GET"])
@require_auth
def list_impacts(environment_id):
    svc, db = _get_metadata_service()
    try:
        result = svc.list_pending_impacts(environment_id)
        return jsonify(result), 200
    finally:
        db.close()
