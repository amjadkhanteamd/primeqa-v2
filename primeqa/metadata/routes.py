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
    """Refresh metadata for the given environment.

    R3: accepts optional JSON body {"categories": ["objects","fields",...]}
    to sync only a subset. Without it, all 6 categories are refreshed.
    """
    data = request.get_json(silent=True) or {}
    categories = data.get("categories")
    svc, db = _get_metadata_service()
    try:
        result = svc.refresh_metadata(
            environment_id, request.user["tenant_id"], categories=categories,
        )
        return jsonify(result), 200
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except Exception as e:
        return jsonify(error=f"Metadata refresh failed: {str(e)}"), 500
    finally:
        db.close()


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


@metadata_bp.route("/api/metadata/<int:meta_version_id>/sync-events", methods=["GET"])
@require_auth
def stream_sync_events(meta_version_id):
    """SSE stream for metadata sync progress.

    Emits category_started, category_finished, sync_finished events as the
    refresh progresses. Falls back to DB snapshot every 5s.
    """
    from flask import Response
    from primeqa.runs.streams import stream_run_events
    from primeqa.metadata.sync_engine import sync_bus_key, SyncEngine

    svc, db = _get_metadata_service()
    try:
        # Scope check: the meta_version must belong to a tenant-visible env
        mv = svc.metadata_repo.get_version(meta_version_id)
        if not mv:
            return jsonify(error="meta_version not found"), 404
    finally:
        db.close()

    def snapshot():
        snap_db = next(get_db())
        try:
            eng = SyncEngine(snap_db, MetadataRepository(snap_db), {})
            statuses = eng.get_status(meta_version_id)
            overall_status_pool = {s["status"] for s in statuses}
            if overall_status_pool == {"complete"}:
                overall = "complete"
            elif "running" in overall_status_pool or "pending" in overall_status_pool:
                overall = "in_progress"
            elif "failed" in overall_status_pool and "complete" not in overall_status_pool:
                overall = "failed"
            else:
                overall = "partial"
            return {"meta_version_id": meta_version_id,
                    "status": overall, "categories": statuses}
        finally:
            snap_db.close()

    # Reuse the run-stream generator but keyed on the negative meta_version_id.
    # Tighter snapshot interval (1s) so the metadata progress bar stays
    # responsive across processes \u2014 the web SSE handler won't see the
    # worker's in-process bus events, so DB polling is the de-facto
    # delivery channel. LISTEN/NOTIFY could lower this further in a
    # follow-up.
    resp = Response(
        stream_run_events(sync_bus_key(meta_version_id), snapshot,
                          snapshot_sec=1),
        mimetype="text/event-stream",
    )
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


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
