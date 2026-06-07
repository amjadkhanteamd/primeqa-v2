"""API routes for the metadata domain.

Endpoints: /api/metadata/*
"""

from flask import Blueprint, jsonify, request

from primeqa.core.auth import require_auth
from primeqa.db import get_db
from primeqa.core.repository import EnvironmentRepository
from primeqa.metadata.repository import MetadataRepository
from primeqa.metadata.service import MetadataService
from primeqa.shared.api import json_error

metadata_bp = Blueprint("metadata", __name__)


def _get_metadata_service():
    db = next(get_db())
    metadata_repo = MetadataRepository(db)
    env_repo = EnvironmentRepository(db)
    return MetadataService(metadata_repo, env_repo), db


# POST /api/metadata/<id>/refresh — RETIRED (D-193): the v1 meta_* sync writer is
# gone (reads are on S1); sync via the Substrate (S1) panel. The GET readers below
# stay until the reader-retirement / Step-5 drop.


@metadata_bp.route("/api/metadata/<int:environment_id>/sync-status", methods=["GET"])
@require_auth
def get_sync_status(environment_id):
    """Return the per-category sync status for the current meta_version."""
    svc, db = _get_metadata_service()
    try:
        from primeqa.metadata.sync_engine import SyncEngine
        env = svc.env_repo.get_environment(environment_id, request.user["tenant_id"])
        if not env or not env.current_meta_version_id:
            return jsonify(meta_version_id=None, statuses=[]), 200
        eng = SyncEngine(db, svc.metadata_repo, {})
        return jsonify(
            meta_version_id=env.current_meta_version_id,
            statuses=eng.get_status(env.current_meta_version_id),
        ), 200
    finally:
        db.close()


# GET /api/metadata/<mv_id>/sync-events (SSE) \u2014 RETIRED (D-193): drove the v1 sync
# progress UI (now removed). The v1 meta_* sync writer is gone.


@metadata_bp.route("/api/metadata/<int:environment_id>/current", methods=["GET"])
@require_auth
def get_current_version(environment_id):
    svc, db = _get_metadata_service()
    try:
        result = svc.get_current_version_summary(environment_id)
        if not result:
            return json_error("NOT_FOUND", "No metadata version found", http=404)
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
            return json_error("VALIDATION_ERROR", "No diff available (need at least 2 versions, http=400)"), 404
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
