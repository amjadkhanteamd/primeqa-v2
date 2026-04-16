"""API routes for the intelligence domain.

Endpoints: /api/explanations/*, /api/patterns/*
"""

from flask import Blueprint, jsonify

intelligence_bp = Blueprint("intelligence", __name__)


@intelligence_bp.route("/api/explanations/<int:run_test_result_id>", methods=["GET"])
def get_explanation(run_test_result_id):
    return jsonify(error="Not Implemented"), 501


@intelligence_bp.route("/api/explanations/<int:run_test_result_id>", methods=["POST"])
def request_explanation(run_test_result_id):
    return jsonify(error="Not Implemented"), 501


@intelligence_bp.route("/api/patterns", methods=["GET"])
def list_patterns():
    return jsonify(error="Not Implemented"), 501


@intelligence_bp.route("/api/patterns/<int:pattern_id>/resolve", methods=["POST"])
def resolve_pattern(pattern_id):
    return jsonify(error="Not Implemented"), 501
