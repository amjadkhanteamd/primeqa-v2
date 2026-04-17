"""API routes for the test management domain.

Endpoints: /api/sections/*, /api/requirements/*, /api/test-cases/*,
           /api/suites/*, /api/reviews/*, /api/impacts/*
"""

from flask import Blueprint, jsonify, request

from primeqa.core.auth import require_auth, require_role
from primeqa.db import get_db
from primeqa.test_management.repository import (
    SectionRepository, RequirementRepository, TestCaseRepository,
    TestSuiteRepository, BAReviewRepository, MetadataImpactRepository,
)
from primeqa.test_management.service import TestManagementService, ConflictError
from primeqa.core.repository import EnvironmentRepository, ConnectionRepository
from primeqa.metadata.repository import MetadataRepository

test_management_bp = Blueprint("test_management", __name__)


def _get_service():
    db = next(get_db())
    return TestManagementService(
        SectionRepository(db), RequirementRepository(db),
        TestCaseRepository(db), TestSuiteRepository(db),
        BAReviewRepository(db), MetadataImpactRepository(db),
    ), db


# --- Sections ---

@test_management_bp.route("/api/sections", methods=["GET"])
@require_auth
def list_sections():
    svc, db = _get_service()
    try:
        tree = svc.get_section_tree(request.user["tenant_id"])
        return jsonify(tree), 200
    finally:
        db.close()


@test_management_bp.route("/api/sections", methods=["POST"])
@require_role("admin")
def create_section():
    data = request.get_json(silent=True) or {}
    if not data.get("name"):
        return jsonify(error="name is required"), 400
    svc, db = _get_service()
    try:
        s = svc.create_section(
            request.user["tenant_id"], data["name"], request.user["id"],
            parent_id=data.get("parent_id"), description=data.get("description"),
            position=data.get("position", 0),
        )
        return jsonify(s), 201
    finally:
        db.close()


@test_management_bp.route("/api/sections/<int:section_id>", methods=["PATCH"])
@require_role("admin")
def update_section(section_id):
    data = request.get_json(silent=True) or {}
    svc, db = _get_service()
    try:
        s = svc.update_section(section_id, request.user["tenant_id"], data)
        return jsonify(s), 200
    except ValueError as e:
        return jsonify(error=str(e)), 404
    finally:
        db.close()


@test_management_bp.route("/api/sections/<int:section_id>", methods=["DELETE"])
@require_role("admin")
def delete_section(section_id):
    svc, db = _get_service()
    try:
        svc.delete_section(section_id, request.user["tenant_id"])
        return jsonify(message="Deleted"), 200
    except ValueError as e:
        return jsonify(error=str(e)), 404
    finally:
        db.close()


# --- Requirements ---

@test_management_bp.route("/api/requirements", methods=["GET"])
@require_auth
def list_requirements():
    svc, db = _get_service()
    try:
        reqs = svc.list_requirements(
            request.user["tenant_id"], section_id=request.args.get("section_id", type=int),
        )
        return jsonify(reqs), 200
    finally:
        db.close()


@test_management_bp.route("/api/requirements", methods=["POST"])
@require_role("admin", "tester")
def create_requirement():
    data = request.get_json(silent=True) or {}
    if not data.get("section_id"):
        return jsonify(error="section_id is required"), 400
    svc, db = _get_service()
    try:
        req = svc.create_requirement(
            request.user["tenant_id"], data["section_id"],
            data.get("source", "manual"), request.user["id"],
            jira_key=data.get("jira_key"), jira_summary=data.get("jira_summary"),
            jira_description=data.get("jira_description"),
            acceptance_criteria=data.get("acceptance_criteria"),
        )
        return jsonify(req), 201
    finally:
        db.close()


@test_management_bp.route("/api/requirements/<int:req_id>", methods=["PATCH"])
@require_role("admin", "tester")
def update_requirement(req_id):
    data = request.get_json(silent=True) or {}
    svc, db = _get_service()
    try:
        req = svc.requirement_repo.update_requirement(req_id, request.user["tenant_id"], data)
        if not req:
            return jsonify(error="Requirement not found"), 404
        return jsonify(TestManagementService._req_dict(req)), 200
    finally:
        db.close()


@test_management_bp.route("/api/requirements/import-jira", methods=["POST"])
@require_role("admin", "tester")
def import_jira():
    data = request.get_json(silent=True) or {}
    required = ["section_id", "jira_base_url", "jira_key"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify(error=f"Missing: {', '.join(missing)}"), 400
    svc, db = _get_service()
    try:
        req = svc.import_jira_requirement(
            request.user["tenant_id"], data["section_id"],
            data["jira_base_url"], data["jira_key"], request.user["id"],
            jira_auth=data.get("jira_auth"),
        )
        return jsonify(req), 201
    except ValueError as e:
        return jsonify(error=str(e)), 409
    except Exception as e:
        return jsonify(error=f"Jira import failed: {str(e)}"), 500
    finally:
        db.close()


@test_management_bp.route("/api/requirements/<int:req_id>/sync", methods=["POST"])
@require_role("admin", "tester")
def sync_jira(req_id):
    data = request.get_json(silent=True) or {}
    if not data.get("jira_base_url"):
        return jsonify(error="jira_base_url is required"), 400
    svc, db = _get_service()
    try:
        req, changed = svc.sync_jira_requirement(
            req_id, request.user["tenant_id"],
            data["jira_base_url"], data.get("jira_auth"),
        )
        return jsonify({"requirement": req, "changed": changed}), 200
    except ValueError as e:
        return jsonify(error=str(e)), 400
    finally:
        db.close()


# --- Test Cases ---

@test_management_bp.route("/api/test-cases", methods=["GET"])
@require_auth
def list_test_cases():
    svc, db = _get_service()
    try:
        tcs = svc.list_test_cases(
            request.user["tenant_id"], request.user["id"],
            requirement_id=request.args.get("requirement_id", type=int),
            section_id=request.args.get("section_id", type=int),
            status=request.args.get("status"),
        )
        return jsonify(tcs), 200
    finally:
        db.close()


@test_management_bp.route("/api/test-cases", methods=["POST"])
@require_role("admin", "tester")
def create_test_case():
    data = request.get_json(silent=True) or {}
    if not data.get("title"):
        return jsonify(error="title is required"), 400
    svc, db = _get_service()
    try:
        tc = svc.create_test_case(
            request.user["tenant_id"], data["title"],
            request.user["id"], request.user["id"],
            requirement_id=data.get("requirement_id"),
            section_id=data.get("section_id"),
            visibility=data.get("visibility", "private"),
        )
        return jsonify(tc), 201
    except ValueError as e:
        return jsonify(error=str(e)), 400
    finally:
        db.close()


@test_management_bp.route("/api/test-cases/<int:tc_id>", methods=["GET"])
@require_auth
def get_test_case(tc_id):
    svc, db = _get_service()
    try:
        tc = svc.get_test_case(tc_id, request.user["tenant_id"], request.user["id"])
        return jsonify(tc), 200
    except ValueError as e:
        return jsonify(error=str(e)), 404
    finally:
        db.close()


@test_management_bp.route("/api/test-cases/<int:tc_id>", methods=["PATCH"])
@require_role("admin", "tester")
def update_test_case(tc_id):
    data = request.get_json(silent=True) or {}
    expected_version = data.pop("expected_version", None)
    svc, db = _get_service()
    try:
        tc = svc.update_test_case(tc_id, request.user["tenant_id"], data, expected_version)
        return jsonify(tc), 200
    except ConflictError:
        return jsonify(error="Conflict: test case was modified by another user"), 409
    except ValueError as e:
        return jsonify(error=str(e)), 400
    finally:
        db.close()


@test_management_bp.route("/api/test-cases/<int:tc_id>/share", methods=["POST"])
@require_auth
def share_test_case(tc_id):
    svc, db = _get_service()
    try:
        tc = svc.share_test_case(tc_id, request.user["tenant_id"], request.user["id"])
        return jsonify(tc), 200
    except ValueError as e:
        return jsonify(error=str(e)), 400
    finally:
        db.close()


@test_management_bp.route("/api/test-cases/<int:tc_id>/activate", methods=["POST"])
@require_role("admin")
def activate_test_case(tc_id):
    svc, db = _get_service()
    try:
        tc = svc.activate_test_case(tc_id, request.user["tenant_id"])
        return jsonify(tc), 200
    except ValueError as e:
        return jsonify(error=str(e)), 400
    finally:
        db.close()


@test_management_bp.route("/api/test-cases/<int:tc_id>/versions", methods=["GET"])
@require_auth
def list_versions(tc_id):
    svc, db = _get_service()
    try:
        versions = svc.list_versions(tc_id, request.user["tenant_id"])
        return jsonify(versions), 200
    except ValueError as e:
        return jsonify(error=str(e)), 404
    finally:
        db.close()


@test_management_bp.route("/api/test-cases/<int:tc_id>/versions", methods=["POST"])
@require_role("admin", "tester")
def create_version(tc_id):
    data = request.get_json(silent=True) or {}
    if not data.get("metadata_version_id"):
        return jsonify(error="metadata_version_id is required"), 400
    svc, db = _get_service()
    try:
        v = svc.create_version(
            tc_id, request.user["tenant_id"],
            data["metadata_version_id"], request.user["id"],
            steps=data.get("steps", []),
            expected_results=data.get("expected_results", []),
            preconditions=data.get("preconditions", []),
            generation_method=data.get("generation_method", "manual"),
            confidence_score=data.get("confidence_score"),
            referenced_entities=data.get("referenced_entities", []),
        )
        return jsonify(v), 201
    except ValueError as e:
        return jsonify(error=str(e)), 400
    finally:
        db.close()


# --- Suites ---

@test_management_bp.route("/api/suites", methods=["GET"])
@require_auth
def list_suites():
    svc, db = _get_service()
    try:
        return jsonify(svc.list_suites(request.user["tenant_id"])), 200
    finally:
        db.close()


@test_management_bp.route("/api/suites", methods=["POST"])
@require_role("admin", "tester")
def create_suite():
    data = request.get_json(silent=True) or {}
    if not data.get("name") or not data.get("suite_type"):
        return jsonify(error="name and suite_type are required"), 400
    svc, db = _get_service()
    try:
        suite = svc.create_suite(
            request.user["tenant_id"], data["name"],
            data["suite_type"], request.user["id"],
            description=data.get("description"),
        )
        return jsonify(suite), 201
    finally:
        db.close()


@test_management_bp.route("/api/suites/<int:suite_id>/test-cases", methods=["GET"])
@require_auth
def get_suite_test_cases(suite_id):
    svc, db = _get_service()
    try:
        tcs = svc.get_suite_test_cases(suite_id, request.user["tenant_id"])
        return jsonify(tcs), 200
    except ValueError as e:
        return jsonify(error=str(e)), 404
    finally:
        db.close()


@test_management_bp.route("/api/suites/<int:suite_id>/test-cases", methods=["POST"])
@require_role("admin", "tester")
def add_to_suite(suite_id):
    data = request.get_json(silent=True) or {}
    if not data.get("test_case_id"):
        return jsonify(error="test_case_id is required"), 400
    svc, db = _get_service()
    try:
        svc.add_to_suite(suite_id, data["test_case_id"],
                         request.user["tenant_id"], data.get("position", 0))
        return jsonify(message="Added"), 200
    except ValueError as e:
        return jsonify(error=str(e)), 400
    finally:
        db.close()


@test_management_bp.route("/api/suites/<int:suite_id>/test-cases/<int:tc_id>", methods=["DELETE"])
@require_role("admin", "tester")
def remove_from_suite(suite_id, tc_id):
    svc, db = _get_service()
    try:
        svc.remove_from_suite(suite_id, tc_id, request.user["tenant_id"])
        return jsonify(message="Removed"), 200
    except ValueError as e:
        return jsonify(error=str(e)), 400
    finally:
        db.close()


@test_management_bp.route("/api/suites/<int:suite_id>/test-cases/<int:tc_id>/reorder", methods=["PATCH"])
@require_role("admin", "tester")
def reorder_in_suite(suite_id, tc_id):
    data = request.get_json(silent=True) or {}
    svc, db = _get_service()
    try:
        svc.reorder_suite_test_case(
            suite_id, tc_id, request.user["tenant_id"], data.get("position", 0),
        )
        return jsonify(message="Reordered"), 200
    except ValueError as e:
        return jsonify(error=str(e)), 400
    finally:
        db.close()


# --- Reviews ---

@test_management_bp.route("/api/reviews", methods=["GET"])
@require_role("admin", "ba")
def list_reviews():
    svc, db = _get_service()
    try:
        reviews = svc.list_reviews(
            request.user["tenant_id"],
            status=request.args.get("status"),
            assigned_to=request.args.get("assigned_to", type=int),
        )
        return jsonify(reviews), 200
    finally:
        db.close()


@test_management_bp.route("/api/reviews/my-queue", methods=["GET"])
@require_role("admin", "ba")
def my_review_queue():
    svc, db = _get_service()
    try:
        reviews = svc.list_reviews(
            request.user["tenant_id"], status="pending",
            assigned_to=request.user["id"],
        )
        return jsonify(reviews), 200
    finally:
        db.close()


@test_management_bp.route("/api/reviews", methods=["POST"])
@require_role("admin", "tester")
def assign_review():
    data = request.get_json(silent=True) or {}
    required = ["test_case_version_id", "assigned_to"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify(error=f"Missing: {', '.join(missing)}"), 400
    svc, db = _get_service()
    try:
        review = svc.assign_review(
            request.user["tenant_id"],
            data["test_case_version_id"], data["assigned_to"],
        )
        return jsonify(review), 201
    finally:
        db.close()


@test_management_bp.route("/api/reviews/<int:review_id>", methods=["PATCH"])
@require_role("admin", "ba")
def submit_review(review_id):
    data = request.get_json(silent=True) or {}
    if not data.get("status"):
        return jsonify(error="status is required"), 400
    if data["status"] not in ("approved", "rejected", "needs_edit"):
        return jsonify(error="Invalid status"), 400
    svc, db = _get_service()
    try:
        review = svc.submit_review(
            review_id, data["status"],
            feedback=data.get("feedback"),
            reviewed_by=request.user["id"],
        )
        return jsonify(review), 200
    except ValueError as e:
        return jsonify(error=str(e)), 400
    finally:
        db.close()


# --- Impacts ---

@test_management_bp.route("/api/impacts", methods=["GET"])
@require_auth
def list_impacts():
    svc, db = _get_service()
    try:
        return jsonify(svc.list_pending_impacts(request.user["tenant_id"])), 200
    finally:
        db.close()


@test_management_bp.route("/api/impacts/<int:impact_id>/resolve", methods=["POST"])
@require_role("admin", "tester")
def resolve_impact(impact_id):
    data = request.get_json(silent=True) or {}
    if not data.get("resolution"):
        return jsonify(error="resolution is required"), 400
    if data["resolution"] not in ("regenerated", "edited", "dismissed"):
        return jsonify(error="Invalid resolution"), 400
    svc, db = _get_service()
    try:
        impact = svc.resolve_impact(impact_id, data["resolution"], request.user["id"])
        return jsonify(impact), 200
    except ValueError as e:
        return jsonify(error=str(e)), 400
    finally:
        db.close()


# --- AI Generation ---

@test_management_bp.route("/api/test-cases/generate", methods=["POST"])
@require_role("admin", "tester")
def generate_test_case():
    data = request.get_json(silent=True) or {}
    for f in ["requirement_id", "environment_id"]:
        if not data.get(f):
            return jsonify(error=f"{f} is required"), 400
    svc, db = _get_service()
    try:
        env_repo = EnvironmentRepository(db)
        conn_repo = ConnectionRepository(db)
        meta_repo = MetadataRepository(db)
        result = svc.generate_test_case(
            tenant_id=request.user["tenant_id"],
            requirement_id=data["requirement_id"],
            environment_id=data["environment_id"],
            created_by=request.user["id"],
            env_repo=env_repo, conn_repo=conn_repo, metadata_repo=meta_repo,
            test_case_id=data.get("test_case_id"),
        )
        return jsonify(result), 201
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except Exception as e:
        return jsonify(error=f"Generation failed: {e}"), 500
    finally:
        db.close()
