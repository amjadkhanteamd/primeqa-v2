"""API routes for the release domain.

Endpoints: /api/releases/*
"""

from flask import Blueprint, jsonify, request

from primeqa.core.auth import require_auth, require_role
from primeqa.db import get_db
from primeqa.release.repository import ReleaseRepository
from primeqa.release.service import ReleaseService

release_bp = Blueprint("release", __name__)


def _get_service():
    db = next(get_db())
    return ReleaseService(ReleaseRepository(db)), db


@release_bp.route("/api/releases", methods=["GET"])
@require_auth
def list_releases():
    svc, db = _get_service()
    try:
        return jsonify(svc.list_releases(
            request.user["tenant_id"], status=request.args.get("status"),
        )), 200
    finally:
        db.close()


@release_bp.route("/api/releases", methods=["POST"])
@require_role("admin", "tester")
def create_release():
    data = request.get_json(silent=True) or {}
    if not data.get("name"):
        return jsonify(error="name is required"), 400
    svc, db = _get_service()
    try:
        return jsonify(svc.create_release(
            request.user["tenant_id"], data["name"], request.user["id"],
            version_tag=data.get("version_tag"),
            description=data.get("description"),
            target_date=data.get("target_date"),
            decision_criteria=data.get("decision_criteria"),
        )), 201
    except ValueError as e:
        return jsonify(error=str(e)), 400
    finally:
        db.close()


@release_bp.route("/api/releases/<int:release_id>", methods=["GET"])
@require_auth
def get_release(release_id):
    svc, db = _get_service()
    try:
        detail = svc.get_release_detail(release_id, request.user["tenant_id"])
        if not detail:
            return jsonify(error="Release not found"), 404
        return jsonify(detail), 200
    finally:
        db.close()


@release_bp.route("/api/releases/<int:release_id>", methods=["PATCH"])
@require_role("admin", "tester")
def update_release(release_id):
    data = request.get_json(silent=True) or {}
    svc, db = _get_service()
    try:
        return jsonify(svc.update_release(release_id, request.user["tenant_id"], data)), 200
    except ValueError as e:
        return jsonify(error=str(e)), 400
    finally:
        db.close()


@release_bp.route("/api/releases/<int:release_id>", methods=["DELETE"])
@require_role("admin")
def delete_release(release_id):
    svc, db = _get_service()
    try:
        svc.delete_release(release_id, request.user["tenant_id"])
        return jsonify(message="Deleted"), 200
    except ValueError as e:
        return jsonify(error=str(e)), 404
    finally:
        db.close()


@release_bp.route("/api/releases/<int:release_id>/requirements", methods=["POST"])
@require_role("admin", "tester")
def add_requirement(release_id):
    data = request.get_json(silent=True) or {}
    if not data.get("requirement_id"):
        return jsonify(error="requirement_id is required"), 400
    svc, db = _get_service()
    try:
        svc.add_requirement(release_id, request.user["tenant_id"],
                            data["requirement_id"], request.user["id"])
        return jsonify(message="Added"), 200
    except ValueError as e:
        return jsonify(error=str(e)), 400
    finally:
        db.close()


@release_bp.route("/api/releases/<int:release_id>/requirements/<int:req_id>", methods=["DELETE"])
@require_role("admin", "tester")
def remove_requirement(release_id, req_id):
    svc, db = _get_service()
    try:
        svc.remove_requirement(release_id, request.user["tenant_id"], req_id)
        return jsonify(message="Removed"), 200
    except ValueError as e:
        return jsonify(error=str(e)), 400
    finally:
        db.close()


@release_bp.route("/api/releases/<int:release_id>/test-plan", methods=["POST"])
@require_role("admin", "tester")
def add_test_plan_item(release_id):
    data = request.get_json(silent=True) or {}
    if not data.get("test_case_id"):
        return jsonify(error="test_case_id is required"), 400
    svc, db = _get_service()
    try:
        svc.add_test_plan_item(
            release_id, request.user["tenant_id"], data["test_case_id"],
            priority=data.get("priority", "medium"),
            position=data.get("position", 0),
            inclusion_reason=data.get("inclusion_reason"),
        )
        return jsonify(message="Added"), 200
    except ValueError as e:
        return jsonify(error=str(e)), 400
    finally:
        db.close()


@release_bp.route("/api/releases/<int:release_id>/test-plan/<int:tc_id>", methods=["DELETE"])
@require_role("admin", "tester")
def remove_test_plan_item(release_id, tc_id):
    svc, db = _get_service()
    try:
        svc.remove_test_plan_item(release_id, request.user["tenant_id"], tc_id)
        return jsonify(message="Removed"), 200
    except ValueError as e:
        return jsonify(error=str(e)), 400
    finally:
        db.close()
