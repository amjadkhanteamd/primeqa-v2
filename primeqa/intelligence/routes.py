"""API routes for the intelligence domain.

Endpoints: /api/explanations/*, /api/patterns/*, /api/dependencies/*, /api/facts/*
"""

from flask import Blueprint, jsonify, request

from primeqa.core.auth import require_auth, require_role
from primeqa.db import get_db
from primeqa.intelligence.repository import (
    EntityDependencyRepository, ExplanationRepository,
    FailurePatternRepository, BehaviourFactRepository, StepCausalLinkRepository,
)
from primeqa.intelligence.service import IntelligenceService

intelligence_bp = Blueprint("intelligence", __name__)


def _get_service():
    db = next(get_db())
    return IntelligenceService(
        EntityDependencyRepository(db),
        ExplanationRepository(db),
        FailurePatternRepository(db),
        BehaviourFactRepository(db),
        StepCausalLinkRepository(db),
    ), db


# --- Dependencies ---

@intelligence_bp.route("/api/dependencies/<int:environment_id>", methods=["GET"])
@require_auth
def get_dependencies(environment_id):
    svc, db = _get_service()
    try:
        from primeqa.core.models import Environment
        env = db.query(Environment).filter(Environment.id == environment_id).first()
        if not env or not env.current_meta_version_id:
            return jsonify(error="No metadata version"), 404
        deps = svc.get_dependencies(env.current_meta_version_id)
        return jsonify(deps), 200
    finally:
        db.close()


@intelligence_bp.route("/api/dependencies/<int:environment_id>/graph/<object_name>", methods=["GET"])
@require_auth
def get_dependency_graph(environment_id, object_name):
    svc, db = _get_service()
    try:
        from primeqa.core.models import Environment
        env = db.query(Environment).filter(Environment.id == environment_id).first()
        if not env or not env.current_meta_version_id:
            return jsonify(error="No metadata version"), 404
        deps = svc.get_dependencies(env.current_meta_version_id, object_name)
        return jsonify(deps), 200
    finally:
        db.close()


# --- Explanations ---

@intelligence_bp.route("/api/explanations/<int:run_test_result_id>", methods=["GET"])
@require_auth
def get_explanations(run_test_result_id):
    svc, db = _get_service()
    try:
        explanations = svc.explanation_repo.list_explanations(run_test_result_id)
        return jsonify([{
            "id": e.id, "explanation_type": e.explanation_type,
            "parsed_explanation": e.parsed_explanation,
            "model_used": e.model_used,
            "requested_at": e.requested_at.isoformat() if e.requested_at else None,
        } for e in explanations]), 200
    finally:
        db.close()


# --- Patterns ---

@intelligence_bp.route("/api/patterns", methods=["GET"])
@require_auth
def list_patterns():
    svc, db = _get_service()
    try:
        patterns = svc.list_active_patterns(
            request.user["tenant_id"],
            request.args.get("environment_id", type=int),
        )
        return jsonify(patterns), 200
    finally:
        db.close()


@intelligence_bp.route("/api/patterns/<int:pattern_id>", methods=["GET"])
@require_auth
def get_pattern(pattern_id):
    svc, db = _get_service()
    try:
        p = svc.get_pattern(pattern_id)
        if not p:
            return jsonify(error="Pattern not found"), 404
        return jsonify(p), 200
    finally:
        db.close()


@intelligence_bp.route("/api/patterns/<int:pattern_id>/resolve", methods=["POST"])
@require_role("admin")
def resolve_pattern(pattern_id):
    svc, db = _get_service()
    try:
        svc.resolve_pattern(pattern_id)
        return jsonify(message="Resolved"), 200
    finally:
        db.close()


# --- Causal Links ---

@intelligence_bp.route("/api/runs/<int:run_id>/results/<int:test_result_id>/causal-links", methods=["GET"])
@require_auth
def get_causal_links(run_id, test_result_id):
    svc, db = _get_service()
    try:
        links = svc.get_causal_links(test_result_id)
        return jsonify(links), 200
    finally:
        db.close()


# --- Facts ---

@intelligence_bp.route("/api/facts/<int:environment_id>", methods=["GET"])
@require_auth
def list_facts(environment_id):
    svc, db = _get_service()
    try:
        facts = svc.list_facts(request.user["tenant_id"], environment_id)
        return jsonify(facts), 200
    finally:
        db.close()


@intelligence_bp.route("/api/facts/<int:environment_id>/seed", methods=["POST"])
@require_role("admin")
def seed_facts(environment_id):
    svc, db = _get_service()
    try:
        count = svc.seed_facts(request.user["tenant_id"], environment_id)
        return jsonify({"seeded": count}), 200
    finally:
        db.close()
