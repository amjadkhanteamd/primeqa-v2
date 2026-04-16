"""API routes for the execution domain.

Endpoints: /api/runs/*, /api/results/*
"""

from flask import Blueprint, jsonify

execution_bp = Blueprint("execution", __name__)


# --- Pipeline Runs ---

@execution_bp.route("/api/runs", methods=["GET"])
def list_runs():
    return jsonify(error="Not Implemented"), 501


@execution_bp.route("/api/runs", methods=["POST"])
def create_run():
    return jsonify(error="Not Implemented"), 501


@execution_bp.route("/api/runs/<int:run_id>", methods=["GET"])
def get_run(run_id):
    return jsonify(error="Not Implemented"), 501


@execution_bp.route("/api/runs/<int:run_id>/cancel", methods=["POST"])
def cancel_run(run_id):
    return jsonify(error="Not Implemented"), 501


@execution_bp.route("/api/runs/<int:run_id>/stages", methods=["GET"])
def get_stages(run_id):
    return jsonify(error="Not Implemented"), 501


@execution_bp.route("/api/runs/queue", methods=["GET"])
def get_queue():
    return jsonify(error="Not Implemented"), 501


@execution_bp.route("/api/runs/active", methods=["GET"])
def get_active_runs():
    return jsonify(error="Not Implemented"), 501


# --- Results ---

@execution_bp.route("/api/results/<int:run_id>", methods=["GET"])
def list_results(run_id):
    return jsonify(error="Not Implemented"), 501


@execution_bp.route("/api/results/<int:run_id>/test/<int:result_id>", methods=["GET"])
def get_test_result(run_id, result_id):
    return jsonify(error="Not Implemented"), 501


@execution_bp.route("/api/results/<int:run_id>/test/<int:result_id>/steps", methods=["GET"])
def get_step_results(run_id, result_id):
    return jsonify(error="Not Implemented"), 501


@execution_bp.route("/api/results/<int:run_id>/cleanup", methods=["GET"])
def get_cleanup_status(run_id):
    return jsonify(error="Not Implemented"), 501
