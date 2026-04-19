"""API routes for the release domain.

Endpoints: /api/releases/*
"""

from flask import Blueprint, jsonify, request

from primeqa.core.auth import require_auth, require_role
from primeqa.db import get_db
from primeqa.release.repository import ReleaseRepository
from primeqa.release.service import ReleaseService
from primeqa.shared.api import json_error

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
        return json_error("VALIDATION_ERROR", "name is required", http=400)
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
        return json_error("VALIDATION_ERROR", str(e), http=400)
    finally:
        db.close()


@release_bp.route("/api/releases/<int:release_id>", methods=["GET"])
@require_auth
def get_release(release_id):
    svc, db = _get_service()
    try:
        detail = svc.get_release_detail(release_id, request.user["tenant_id"])
        if not detail:
            return json_error("NOT_FOUND", "Release not found", http=404)
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
        return json_error("VALIDATION_ERROR", str(e), http=400)
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
        return json_error("VALIDATION_ERROR", str(e), http=400)
    finally:
        db.close()


@release_bp.route("/api/releases/<int:release_id>/requirements", methods=["POST"])
@require_role("admin", "tester")
def add_requirement(release_id):
    data = request.get_json(silent=True) or {}
    if not data.get("requirement_id"):
        return json_error("VALIDATION_ERROR", "requirement_id is required", http=400)
    svc, db = _get_service()
    try:
        svc.add_requirement(release_id, request.user["tenant_id"],
                            data["requirement_id"], request.user["id"])
        return jsonify(message="Added"), 200
    except ValueError as e:
        return json_error("VALIDATION_ERROR", str(e), http=400)
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
        return json_error("VALIDATION_ERROR", str(e), http=400)
    finally:
        db.close()


@release_bp.route("/api/releases/<int:release_id>/requirements/bulk", methods=["POST"])
@require_role("admin", "tester")
def add_requirements_bulk(release_id):
    """Attach many requirements to a release. Body: {requirement_ids: [...]}.
    Returns {added, already_in, skipped}."""
    from primeqa.shared.api import BULK_MAX_ITEMS
    data = request.get_json(silent=True) or {}
    ids = data.get("requirement_ids") or []
    if not isinstance(ids, list) or not ids:
        return json_error("VALIDATION_ERROR", "requirement_ids must be a non-empty array", http=400)
    if len(ids) > BULK_MAX_ITEMS:  # audit F5
        return json_error(
            "BULK_LIMIT",
            f"Bulk operations are limited to {BULK_MAX_ITEMS} items per call",
            http=400,
        )
    svc, db = _get_service()
    try:
        result = svc.add_requirements_bulk(
            release_id, request.user["tenant_id"], ids, request.user["id"],
        )
        return jsonify(result), 200
    except ValueError as e:
        return json_error("VALIDATION_ERROR", str(e), http=400)
    finally:
        db.close()


@release_bp.route("/api/releases/<int:release_id>/test-plan/bulk", methods=["POST"])
@require_role("admin", "tester")
def add_test_plan_items_bulk(release_id):
    """Attach many test cases to a release's test plan.
    Body: {test_case_ids: [...], priority?, inclusion_reason?}."""
    from primeqa.shared.api import BULK_MAX_ITEMS
    data = request.get_json(silent=True) or {}
    ids = data.get("test_case_ids") or []
    if not isinstance(ids, list) or not ids:
        return json_error("VALIDATION_ERROR", "test_case_ids must be a non-empty array", http=400)
    if len(ids) > BULK_MAX_ITEMS:  # audit F5
        return json_error(
            "BULK_LIMIT",
            f"Bulk operations are limited to {BULK_MAX_ITEMS} items per call",
            http=400,
        )
    svc, db = _get_service()
    try:
        result = svc.add_test_plan_items_bulk(
            release_id, request.user["tenant_id"], ids, request.user["id"],
            priority=data.get("priority", "medium"),
            inclusion_reason=data.get("inclusion_reason"),
        )
        return jsonify(result), 200
    except ValueError as e:
        return json_error("VALIDATION_ERROR", str(e), http=400)
    finally:
        db.close()


@release_bp.route("/api/releases/<int:release_id>/test-plan", methods=["POST"])
@require_role("admin", "tester")
def add_test_plan_item(release_id):
    data = request.get_json(silent=True) or {}
    if not data.get("test_case_id"):
        return json_error("VALIDATION_ERROR", "test_case_id is required", http=400)
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
        return json_error("VALIDATION_ERROR", str(e), http=400)
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
        return json_error("VALIDATION_ERROR", str(e), http=400)
    finally:
        db.close()


@release_bp.route("/api/releases/<int:release_id>/evaluate-decision", methods=["POST"])
@require_role("admin", "tester")
def evaluate_decision(release_id):
    from primeqa.release.decision_engine import DecisionEngine
    svc, db = _get_service()
    try:
        release = svc.release_repo.get_release(release_id, request.user["tenant_id"])
        if not release:
            return json_error("NOT_FOUND", "Release not found", http=404)
        engine = DecisionEngine(db)
        result = engine.evaluate(release)
        svc.release_repo.create_decision(
            release_id=release_id,
            recommendation=result["recommendation"],
            confidence=result["confidence"],
            reasoning=result,
            criteria_met=result["criteria_met"],
            recommended_by="ai",
        )
        return jsonify(result), 200
    finally:
        db.close()


@release_bp.route("/api/releases/<int:release_id>/decisions/<int:decision_id>/finalize", methods=["POST"])
@require_role("admin")
def finalize_decision(release_id, decision_id):
    data = request.get_json(silent=True) or {}
    final = data.get("final_decision")
    if final not in ("go", "conditional_go", "no_go"):
        return json_error("VALIDATION_ERROR", "Invalid final_decision", http=400)
    svc, db = _get_service()
    try:
        d = svc.release_repo.finalize_decision(decision_id, final, request.user["id"], data.get("override_reason"))
        if not d:
            return json_error("NOT_FOUND", "Decision not found", http=404)
        return jsonify({"final_decision": d.final_decision}), 200
    finally:
        db.close()


@release_bp.route("/api/releases/<int:release_id>/status", methods=["GET"])
def public_release_status(release_id):
    """Public endpoint for CI/CD to poll release decision status.

    R5 / Q3: if the latest decision has `agent_verdict_counts=true` (default),
    CI sees the post-agent result (which may flip red\u2192green after the agent
    auto-fixed and rerun passed). If false, CI sees the pre-agent (raw human)
    verdict. Super Admin can toggle per release in the release detail page.
    """
    db = next(get_db())
    try:
        from primeqa.release.models import Release, ReleaseDecision, ReleaseRun
        from primeqa.execution.models import PipelineRun
        from sqlalchemy import desc
        release = db.query(Release).filter(Release.id == release_id).first()
        if not release:
            return json_error("NOT_FOUND", "Release not found", http=404)
        latest = db.query(ReleaseDecision).filter(
            ReleaseDecision.release_id == release_id,
        ).order_by(desc(ReleaseDecision.created_at)).first()

        # Compute post-agent rolled-up stats if there have been agent reruns.
        # When `agent_verdict_counts=false` we ignore agent-triggered reruns
        # and only reflect the original (parent_run_id IS NULL) runs.
        agent_counts = True if latest is None else bool(latest.agent_verdict_counts)
        q = db.query(PipelineRun).join(
            ReleaseRun, ReleaseRun.pipeline_run_id == PipelineRun.id,
        ).filter(ReleaseRun.release_id == release_id)
        if not agent_counts:
            q = q.filter(PipelineRun.parent_run_id.is_(None))
        runs = q.all()
        passed = sum(r.passed or 0 for r in runs)
        failed = sum(r.failed or 0 for r in runs)
        total  = sum(r.total_tests or 0 for r in runs)

        return jsonify({
            "release_id": release_id,
            "name": release.name,
            "status": release.status,
            "recommendation": latest.recommendation if latest else None,
            "final_decision": latest.final_decision if latest else None,
            "confidence": latest.confidence if latest else None,
            "decided_at": latest.decided_at.isoformat() if latest and latest.decided_at else None,
            "agent_verdict_counts": agent_counts,
            "rollup": {"passed": passed, "failed": failed, "total": total,
                       "runs_counted": len(runs)},
        }), 200
    finally:
        db.close()


@release_bp.route("/api/webhooks/ci-trigger", methods=["POST"])
def ci_webhook_trigger():
    """CI/CD webhook to trigger release test runs. Expects HMAC-SHA256 signature."""
    import hmac
    import hashlib
    import os

    secret = os.getenv("WEBHOOK_SECRET", "")
    if secret:
        provided_sig = request.headers.get("X-PrimeQA-Signature", "")
        body = request.get_data()
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, provided_sig):
            return json_error("UNAUTHORIZED", "Invalid signature", http=401)

    data = request.get_json(silent=True) or {}
    release_id = data.get("release_id")
    environment_id = data.get("environment_id")
    commit_sha = data.get("commit_sha", "unknown")

    if not release_id or not environment_id:
        return json_error("VALIDATION_ERROR", "release_id and environment_id required", http=400)

    db = next(get_db())
    try:
        from primeqa.release.models import Release, ReleaseTestPlanItem, ReleaseRun
        from primeqa.execution.repository import (
            PipelineRunRepository, PipelineStageRepository,
            ExecutionSlotRepository, WorkerHeartbeatRepository,
        )
        from primeqa.execution.service import PipelineService
        release = db.query(Release).filter(Release.id == release_id).first()
        if not release:
            return json_error("NOT_FOUND", "Release not found", http=404)

        plan_items = db.query(ReleaseTestPlanItem).filter(
            ReleaseTestPlanItem.release_id == release_id,
        ).all()
        tc_ids = [item.test_case_id for item in plan_items]
        if not tc_ids:
            return json_error("VALIDATION_ERROR", "No test cases in release plan", http=400)

        svc = PipelineService(
            PipelineRunRepository(db), PipelineStageRepository(db),
            ExecutionSlotRepository(db), WorkerHeartbeatRepository(db),
        )
        result = svc.create_run(
            tenant_id=release.tenant_id, environment_id=environment_id,
            triggered_by=release.created_by, run_type="execute_only",
            source_type="release", source_ids=tc_ids, priority="high",
            config={"commit_sha": commit_sha, "release_id": release_id},
        )
        rr = ReleaseRun(release_id=release_id, pipeline_run_id=result["id"],
                       triggered_by=release.created_by)
        db.add(rr)
        db.commit()

        return jsonify({
            "run_id": result["id"],
            "status_url": f"/api/releases/{release_id}/status",
        }), 201
    finally:
        db.close()


@release_bp.route("/api/releases/<int:release_id>/score-risks", methods=["POST"])
@require_role("admin", "tester")
def score_release_risks(release_id):
    from primeqa.intelligence.risk_engine import RiskEngine
    svc, db = _get_service()
    try:
        release = svc.release_repo.get_release(release_id, request.user["tenant_id"])
        if not release:
            return json_error("NOT_FOUND", "Release not found", http=404)
        engine = RiskEngine(db)
        impact_count = engine.score_all_release_impacts(release_id)
        plan_count = engine.rank_release_test_plan(release_id)
        return jsonify({
            "impacts_scored": impact_count,
            "plan_items_ranked": plan_count,
        }), 200
    finally:
        db.close()
