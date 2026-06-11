"""API routes for the intelligence domain.

Endpoints: /api/explanations/*, /api/patterns/*, /api/dependencies/*,
/api/facts/*, /api/runs/*/results/*/causal-links

Tenant-scoping rule (audit F1, 2026-04-19): every endpoint that takes an
id must verify the owning entity belongs to the caller's tenant BEFORE
returning any data. Explanations + causal-links chain through
run_test_results → pipeline_runs.tenant_id; patterns carry tenant_id
directly; dependencies scope through environments.tenant_id.

Uses the shared API envelope via `json_error` for 4xx/5xx so clients
get {error:{code,message}} consistently.
"""

from flask import Blueprint, jsonify, request

from primeqa.core.auth import require_auth, require_role
from primeqa.db import get_db
from primeqa.intelligence.repository import (
    EntityDependencyRepository, ExplanationRepository,
    FailurePatternRepository, BehaviourFactRepository, StepCausalLinkRepository,
)
from primeqa.intelligence.service import IntelligenceService
from primeqa.shared.api import json_error

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


def _own_environment_or_404(db, environment_id, tenant_id):
    from primeqa.core.models import Environment
    return db.query(Environment).filter(
        Environment.id == environment_id,
        Environment.tenant_id == tenant_id,
    ).first()


# --- Dependencies ---

@intelligence_bp.route("/api/dependencies/<int:environment_id>", methods=["GET"])
@require_auth
def get_dependencies(environment_id):
    svc, db = _get_service()
    try:
        env = _own_environment_or_404(db, environment_id, request.user["tenant_id"])
        if not env:
            return json_error("NOT_FOUND", "Environment not found", http=404)
        if not env.current_meta_version_id:
            return json_error("NO_METADATA",
                              "Environment has no current metadata version", http=404)
        deps = svc.get_dependencies(env.current_meta_version_id)
        return jsonify(deps), 200
    finally:
        db.close()


@intelligence_bp.route("/api/dependencies/<int:environment_id>/graph/<object_name>", methods=["GET"])
@require_auth
def get_dependency_graph(environment_id, object_name):
    svc, db = _get_service()
    try:
        env = _own_environment_or_404(db, environment_id, request.user["tenant_id"])
        if not env:
            return json_error("NOT_FOUND", "Environment not found", http=404)
        if not env.current_meta_version_id:
            return json_error("NO_METADATA",
                              "Environment has no current metadata version", http=404)
        deps = svc.get_dependencies(env.current_meta_version_id, object_name)
        return jsonify(deps), 200
    finally:
        db.close()


# --- Explanations ---

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
            return json_error("NOT_FOUND", "Pattern not found", http=404)
        # FailurePattern carries tenant_id directly; compare before returning.
        if p.get("tenant_id") != request.user["tenant_id"]:
            return json_error("NOT_FOUND", "Pattern not found", http=404)
        return jsonify(p), 200
    finally:
        db.close()


@intelligence_bp.route("/api/patterns/<int:pattern_id>/resolve", methods=["POST"])
@require_role("admin")
def resolve_pattern(pattern_id):
    svc, db = _get_service()
    try:
        # Same tenant check — admins from another tenant can't resolve.
        p = svc.get_pattern(pattern_id)
        if not p or p.get("tenant_id") != request.user["tenant_id"]:
            return json_error("NOT_FOUND", "Pattern not found", http=404)
        svc.resolve_pattern(pattern_id)
        return jsonify(message="Resolved"), 200
    finally:
        db.close()


# --- Causal Links ---

@intelligence_bp.route("/api/facts/<int:environment_id>", methods=["GET"])
@require_auth
def list_facts(environment_id):
    svc, db = _get_service()
    try:
        # Facts carry tenant_id on the row AND we pass the caller's
        # tenant_id into the service, so leakage is already prevented
        # here. Keep the env-ownership check for defence-in-depth.
        if not _own_environment_or_404(db, environment_id, request.user["tenant_id"]):
            return json_error("NOT_FOUND", "Environment not found", http=404)
        facts = svc.list_facts(request.user["tenant_id"], environment_id)
        return jsonify(facts), 200
    finally:
        db.close()


@intelligence_bp.route("/api/facts/<int:environment_id>/seed", methods=["POST"])
@require_role("admin")
def seed_facts(environment_id):
    svc, db = _get_service()
    try:
        if not _own_environment_or_404(db, environment_id, request.user["tenant_id"]):
            return json_error("NOT_FOUND", "Environment not found", http=404)
        count = svc.seed_facts(request.user["tenant_id"], environment_id)
        return jsonify({"seeded": count}), 200
    finally:
        db.close()
