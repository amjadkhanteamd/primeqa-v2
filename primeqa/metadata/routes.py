"""API routes for the metadata domain.

Endpoints: /api/metadata/*
"""

from flask import Blueprint, jsonify

metadata_bp = Blueprint("metadata", __name__)


@metadata_bp.route("/api/metadata/<int:environment_id>/refresh", methods=["POST"])
def refresh_metadata(environment_id):
    return jsonify(error="Not Implemented"), 501


@metadata_bp.route("/api/metadata/<int:environment_id>/current", methods=["GET"])
def get_current_version(environment_id):
    return jsonify(error="Not Implemented"), 501


@metadata_bp.route("/api/metadata/<int:environment_id>/diff", methods=["GET"])
def get_diff(environment_id):
    return jsonify(error="Not Implemented"), 501


@metadata_bp.route("/api/metadata/<int:environment_id>/impacts", methods=["GET"])
def list_impacts(environment_id):
    return jsonify(error="Not Implemented"), 501
