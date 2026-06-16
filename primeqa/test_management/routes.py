"""API routes for the test management domain.

All list endpoints return the uniform envelope `{data, meta}` via
`primeqa.shared.api.json_page`. Errors return `{error: {code, message}}`
via `json_error` / `json_error_from`. Destructive bulk actions require
`confirm == "DELETE"` in the payload and are capped at
`primeqa.shared.api.BULK_MAX_ITEMS` (100).

Soft-delete / restore / purge convention:
  DELETE /api/<res>/<id>          — soft delete (anyone with write role)
  POST   /api/<res>/<id>/restore  — restore from trash
  POST   /api/<res>/<id>/purge    — admin-only permanent deletion
"""

from flask import Blueprint, jsonify, request

from primeqa.core.auth import require_auth, require_role
from primeqa.core.repository import (
    ActivityLogRepository, ConnectionRepository, EnvironmentRepository,
)
from primeqa.db import get_db
from primeqa.metadata.repository import MetadataRepository
from primeqa.shared.api import (
    BulkLimitError, ConflictError, ForbiddenError, NotFoundError,
    ServiceError, ValidationError,
    json_error, json_error_from, json_list, json_page,
    parse_list_params, require_bulk_confirm,
)
from primeqa.shared.query_builder import QueryBuilderError
from primeqa.test_management.repository import (
    RequirementRepository, SectionRepository,
)
from primeqa.test_management.service import TestManagementService

test_management_bp = Blueprint("test_management", __name__)


def _get_service():
    db = next(get_db())
    svc = TestManagementService(
        section_repo=SectionRepository(db),
        requirement_repo=RequirementRepository(db),
        activity_repo=ActivityLogRepository(db),
    )
    return svc, db


def _handle(fn):
    """Map ServiceError / QueryBuilderError / ValueError → uniform envelope."""
    try:
        return fn()
    except (ValidationError, ConflictError, NotFoundError, ForbiddenError,
            BulkLimitError, ServiceError) as e:
        return json_error_from(e)
    except QueryBuilderError as e:
        return json_error(e.code, e.message, http=400)
    except ValueError as e:
        return json_error("VALIDATION_ERROR", str(e), http=400)


# ---- Sections ---------------------------------------------------------------

@test_management_bp.route("/api/sections", methods=["GET"])
@require_auth
def list_sections():
    svc, db = _get_service()
    try:
        # Legacy clients get the tree; paginated consumers get ?page=
        if "page" in request.args or "per_page" in request.args or "q" in request.args:
            params = parse_list_params(
                request, allowed_filters=["parent_id"],
                default_sort="updated_at", default_order="desc",
            )
            def run():
                page, serializer = svc.list_sections_page(
                    request.user["tenant_id"],
                    page=params["page"], per_page=params["per_page"],
                    q=params["q"], sort=params["sort"], order=params["order"],
                    filters=params["filters"], include_deleted=params["show_deleted"],
                )
                return json_page(page, serialize=serializer)
            return _handle(run)
        tree = svc.get_section_tree(request.user["tenant_id"])
        return jsonify(tree), 200
    finally:
        db.close()


@test_management_bp.route("/api/sections", methods=["POST"])
@require_role("admin")
def create_section():
    data = request.get_json(silent=True) or {}
    if not data.get("name"):
        return json_error("VALIDATION_ERROR", "name is required")
    svc, db = _get_service()
    try:
        def run():
            s = svc.create_section(
                request.user["tenant_id"], data["name"], request.user["id"],
                parent_id=data.get("parent_id"),
                description=data.get("description"),
                position=data.get("position", 0),
            )
            return jsonify(s), 201
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/sections/<int:section_id>", methods=["PATCH"])
@require_role("admin")
def update_section(section_id):
    data = request.get_json(silent=True) or {}
    expected_version = data.pop("expected_version", None)
    svc, db = _get_service()
    try:
        def run():
            s = svc.update_section(section_id, request.user["tenant_id"], data,
                                   expected_version=expected_version,
                                   user_id=request.user["id"])
            return jsonify(s), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/sections/<int:section_id>", methods=["DELETE"])
@require_role("admin")
def delete_section(section_id):
    svc, db = _get_service()
    try:
        def run():
            s = svc.delete_section(section_id, request.user["tenant_id"], request.user["id"])
            return jsonify(s), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/sections/<int:section_id>/restore", methods=["POST"])
@require_role("admin")
def restore_section(section_id):
    svc, db = _get_service()
    try:
        def run():
            s = svc.restore_section(section_id, request.user["tenant_id"], request.user["id"])
            return jsonify(s), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/sections/<int:section_id>/purge", methods=["POST"])
@require_role("admin")
def purge_section(section_id):
    svc, db = _get_service()
    try:
        def run():
            svc.purge_section(section_id, request.user["tenant_id"], request.user["id"])
            return jsonify({"message": "Purged"}), 200
        return _handle(run)
    finally:
        db.close()


# ---- Requirements -----------------------------------------------------------

@test_management_bp.route("/api/requirements", methods=["GET"])
@require_auth
def list_requirements():
    svc, db = _get_service()
    try:
        if "page" in request.args or "per_page" in request.args or "q" in request.args:
            params = parse_list_params(
                request, allowed_filters=["section_id", "source", "is_stale"],
            )
            def run():
                page, serializer = svc.list_requirements_page(
                    request.user["tenant_id"],
                    page=params["page"], per_page=params["per_page"],
                    q=params["q"], sort=params["sort"], order=params["order"],
                    filters=params["filters"], include_deleted=params["show_deleted"],
                )
                return json_page(page, serialize=serializer)
            return _handle(run)
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
        return json_error("VALIDATION_ERROR", "section_id is required")
    svc, db = _get_service()
    try:
        def run():
            req = svc.create_requirement(
                request.user["tenant_id"], data["section_id"],
                data.get("source", "manual"), request.user["id"],
                jira_key=data.get("jira_key"), jira_summary=data.get("jira_summary"),
                jira_description=data.get("jira_description"),
                acceptance_criteria=data.get("acceptance_criteria"),
            )
            return jsonify(req), 201
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/requirements/<int:req_id>", methods=["GET"])
@require_auth
def get_requirement(req_id):
    """Fetch a single requirement by id.

    Added post-QA-sweep (finding 11.1.7) \u2014 the list endpoint existed
    but no individual-detail GET, so programmatic integrations couldn't
    read one record without pulling the full paginated list. Mirrors
    the pattern used for /api/test-cases/:id and /api/runs/:id.
    """
    svc, db = _get_service()
    try:
        def run():
            req = svc.get_requirement(req_id, request.user["tenant_id"])
            return jsonify(req), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/requirements/<int:req_id>", methods=["PATCH"])
@require_role("admin", "tester")
def update_requirement(req_id):
    data = request.get_json(silent=True) or {}
    expected_version = data.pop("expected_version", None)
    svc, db = _get_service()
    try:
        def run():
            req = svc.update_requirement(
                req_id, request.user["tenant_id"], data,
                expected_version=expected_version, user_id=request.user["id"],
            )
            return jsonify(req), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/requirements/<int:req_id>", methods=["DELETE"])
@require_role("admin", "tester")
def delete_requirement(req_id):
    svc, db = _get_service()
    try:
        def run():
            r = svc.delete_requirement(req_id, request.user["tenant_id"], request.user["id"])
            return jsonify(r), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/requirements/<int:req_id>/restore", methods=["POST"])
@require_role("admin", "tester")
def restore_requirement(req_id):
    svc, db = _get_service()
    try:
        def run():
            r = svc.restore_requirement(req_id, request.user["tenant_id"], request.user["id"])
            return jsonify(r), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/requirements/<int:req_id>/purge", methods=["POST"])
@require_role("admin")
def purge_requirement(req_id):
    svc, db = _get_service()
    try:
        def run():
            svc.purge_requirement(req_id, request.user["tenant_id"], request.user["id"])
            return jsonify({"message": "Purged"}), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/requirements/import-jira", methods=["POST"])
@require_role("admin", "tester")
def import_jira():
    data = request.get_json(silent=True) or {}
    required = ["section_id", "jira_base_url", "jira_key"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return json_error("VALIDATION_ERROR", f"Missing: {', '.join(missing)}")
    svc, db = _get_service()
    try:
        def run():
            req = svc.import_jira_requirement(
                request.user["tenant_id"], data["section_id"],
                data["jira_base_url"], data["jira_key"], request.user["id"],
                jira_auth=data.get("jira_auth"),
            )
            return jsonify(req), 201
        return _handle(run)
    except Exception as e:
        return json_error("JIRA_IMPORT_FAILED", f"Jira import failed: {e}", http=500)
    finally:
        db.close()


@test_management_bp.route("/api/requirements/<int:req_id>/sync", methods=["POST"])
@require_role("admin", "tester")
def sync_jira(req_id):
    data = request.get_json(silent=True) or {}
    if not data.get("jira_base_url"):
        return json_error("VALIDATION_ERROR", "jira_base_url is required")
    svc, db = _get_service()
    try:
        def run():
            req, changed = svc.sync_jira_requirement(
                req_id, request.user["tenant_id"],
                data["jira_base_url"], data.get("jira_auth"),
            )
            return jsonify({"requirement": req, "changed": changed}), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/step-schema", methods=["GET"])
@require_auth
def get_step_schema():
    from primeqa.test_management.step_schema import STEP_ACTIONS
    return jsonify(STEP_ACTIONS), 200


@test_management_bp.route("/api/metadata/<int:env_id>/objects", methods=["GET"])
@require_auth
def list_environment_objects(env_id):
    q = (request.args.get("q") or "").lower()
    db = next(get_db())
    try:
        from primeqa.core.models import Environment
        env = db.query(Environment).filter(
            Environment.id == env_id, Environment.tenant_id == request.user["tenant_id"],
        ).first()
        if not env:
            return jsonify([]), 200
        # D-195 Step 5a.1: metadata reads are S1-only (tenant-scoped org model);
        # gate on the S1 reader being hydrated, not on the inert meta_version id.
        from primeqa.metadata_bridge.s1_reader import build_metadata_s1_reader
        reader = build_metadata_s1_reader(request.user["tenant_id"])
        if not reader:
            return jsonify([]), 200
        objects = reader.get_objects(None)
        if q:
            objects = [o for o in objects if q in o.api_name.lower() or q in (o.label or "").lower()]
        return jsonify([{
            "api_name": o.api_name, "label": o.label, "is_custom": o.is_custom,
        } for o in objects[:50]]), 200
    finally:
        db.close()


@test_management_bp.route("/api/metadata/<int:env_id>/objects/<string:object_name>/fields", methods=["GET"])
@require_auth
def list_object_fields(env_id, object_name):
    q = (request.args.get("q") or "").lower()
    db = next(get_db())
    try:
        from primeqa.core.models import Environment
        env = db.query(Environment).filter(
            Environment.id == env_id, Environment.tenant_id == request.user["tenant_id"],
        ).first()
        if not env:
            return jsonify([]), 200
        # D-195 Step 5a.1: S1-only (see list_environment_objects).
        from primeqa.metadata_bridge.s1_reader import build_metadata_s1_reader
        reader = build_metadata_s1_reader(request.user["tenant_id"])
        if not reader:
            return jsonify([]), 200
        obj = reader.get_object_by_api_name(None, object_name)
        if not obj:
            return jsonify([]), 200
        fields = reader.get_fields(None, obj.id)
        if q:
            fields = [f for f in fields if q in f.api_name.lower() or q in (f.label or "").lower()]
        return jsonify([{
            "api_name": f.api_name, "label": f.label, "field_type": f.field_type,
            "is_required": f.is_required, "is_createable": f.is_createable,
            "is_custom": f.is_custom, "reference_to": f.reference_to,
            "picklist_values": f.picklist_values,
        } for f in fields[:200]]), 200
    finally:
        db.close()


# ---- Bulk ops (test cases) --------------------------------------------------

_DESTRUCTIVE_BULK_ACTIONS = {"soft_delete"}

