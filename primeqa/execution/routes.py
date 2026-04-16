"""API routes for the execution domain.

Endpoints: /api/runs/*, /api/environments/<id>/slots
"""

from flask import Blueprint, jsonify, request

from primeqa.core.auth import require_auth, require_role
from primeqa.db import get_db
from primeqa.execution.repository import (
    PipelineRunRepository, PipelineStageRepository,
    ExecutionSlotRepository, WorkerHeartbeatRepository,
)
from primeqa.execution.service import PipelineService

execution_bp = Blueprint("execution", __name__)


def _get_service():
    db = next(get_db())
    run_repo = PipelineRunRepository(db)
    stage_repo = PipelineStageRepository(db)
    slot_repo = ExecutionSlotRepository(db)
    hb_repo = WorkerHeartbeatRepository(db)
    return PipelineService(run_repo, stage_repo, slot_repo, hb_repo), db


@execution_bp.route("/api/runs", methods=["POST"])
@require_role("admin", "tester")
def create_run():
    data = request.get_json(silent=True) or {}
    required = ["environment_id", "run_type", "source_type", "source_ids"]
    missing = [f for f in required if f not in data]
    if missing:
        return jsonify(error=f"Missing: {', '.join(missing)}"), 400
    svc, db = _get_service()
    try:
        result = svc.create_run(
            tenant_id=request.user["tenant_id"],
            environment_id=data["environment_id"],
            triggered_by=request.user["id"],
            run_type=data["run_type"],
            source_type=data["source_type"],
            source_ids=data["source_ids"],
            priority=data.get("priority", "normal"),
            max_execution_time_sec=data.get("max_execution_time_sec", 3600),
            config=data.get("config", {}),
        )
        return jsonify(result), 201
    except ValueError as e:
        return jsonify(error=str(e)), 400
    finally:
        db.close()


@execution_bp.route("/api/runs", methods=["GET"])
@require_auth
def list_runs():
    svc, db = _get_service()
    try:
        runs = svc.list_runs(
            request.user["tenant_id"],
            status=request.args.get("status"),
            environment_id=request.args.get("environment_id", type=int),
            triggered_by=request.args.get("triggered_by", type=int),
            limit=request.args.get("limit", 50, type=int),
            offset=request.args.get("offset", 0, type=int),
        )
        return jsonify(runs), 200
    finally:
        db.close()


@execution_bp.route("/api/runs/<int:run_id>", methods=["GET"])
@require_auth
def get_run(run_id):
    svc, db = _get_service()
    try:
        result = svc.get_run_status(run_id, request.user["tenant_id"])
        if not result:
            return jsonify(error="Run not found"), 404
        return jsonify(result), 200
    finally:
        db.close()


@execution_bp.route("/api/runs/<int:run_id>/cancel", methods=["POST"])
@require_role("admin", "tester")
def cancel_run(run_id):
    svc, db = _get_service()
    try:
        result = svc.cancel_run(run_id, request.user["tenant_id"])
        return jsonify(result), 200
    except ValueError as e:
        return jsonify(error=str(e)), 400
    finally:
        db.close()


@execution_bp.route("/api/runs/queue", methods=["GET"])
@require_auth
def get_queue():
    svc, db = _get_service()
    try:
        queue = svc.get_queue(request.user["tenant_id"])
        return jsonify(queue), 200
    finally:
        db.close()


@execution_bp.route("/api/environments/<int:env_id>/slots", methods=["GET"])
@require_auth
def get_slots(env_id):
    svc, db = _get_service()
    try:
        status = svc.get_slot_status(env_id)
        if not status:
            return jsonify(error="Environment not found"), 404
        return jsonify(status), 200
    finally:
        db.close()
