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
from primeqa.core.permissions import require_permission
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
    BAReviewRepository, MetadataImpactRepository, RequirementRepository,
    SectionRepository, TestCaseRepository, TestSuiteRepository,
)
from primeqa.test_management.service import TestManagementService

test_management_bp = Blueprint("test_management", __name__)


def _get_service():
    db = next(get_db())
    svc = TestManagementService(
        section_repo=SectionRepository(db),
        requirement_repo=RequirementRepository(db),
        test_case_repo=TestCaseRepository(db),
        suite_repo=TestSuiteRepository(db),
        review_repo=BAReviewRepository(db),
        impact_repo=MetadataImpactRepository(db),
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


@test_management_bp.route("/api/requirements/bulk-generate", methods=["POST"])
@require_role("admin", "tester")
def bulk_generate_requirements():
    """Prompt 11: flip sync -> async. Extended for /run readiness
    modal: accepts either `requirement_ids` (legacy) or `jira_keys`
    (new — auto-imports missing requirements first).

    Body: {
      environment_id: int (required),
      requirement_ids: [int] | jira_keys: [str]   (at least one, non-empty)
    }
    Returns 202 with {jobs: [{requirement_id, jira_key?, job_id,
    already_running, ...}], total: N}.

    Jira-keys path: if a key's requirement doesn't exist yet, import
    it on the fly using the env's Jira connection + the tenant's
    default section (oldest non-deleted section). Then queue a
    generation job like the legacy path.

    Cap: combined 20 per call. Rejects with BATCH_TOO_LARGE on
    overflow — do NOT chunk client-side (silent partial failure is
    worse than an explicit ceiling).
    """
    from primeqa.intelligence.generation_jobs import create_or_get_job
    from primeqa.core.repository import ConnectionRepository
    from primeqa.core.models import Environment
    from primeqa.test_management.repository import (
        RequirementRepository, SectionRepository, TestCaseRepository,
        TestSuiteRepository, BAReviewRepository, MetadataImpactRepository,
    )
    from primeqa.test_management.service import TestManagementService
    from primeqa.db import SessionLocal

    data = request.get_json(silent=True) or {}
    env_id = data.get("environment_id")
    req_ids_raw = data.get("requirement_ids") or []
    jira_keys_raw = data.get("jira_keys") or []

    if not env_id:
        return json_error("VALIDATION_ERROR", "environment_id is required")

    # Coerce + dedupe the two input lists
    req_ids = []
    if isinstance(req_ids_raw, list):
        for x in req_ids_raw:
            try:
                req_ids.append(int(x))
            except (TypeError, ValueError):
                continue
    req_ids = list(dict.fromkeys(req_ids))  # dedupe, preserve order

    jira_keys = []
    if isinstance(jira_keys_raw, list):
        for k in jira_keys_raw:
            if isinstance(k, str) and k.strip():
                jira_keys.append(k.strip())
    jira_keys = list(dict.fromkeys(jira_keys))

    if not req_ids and not jira_keys:
        return json_error(
            "VALIDATION_ERROR",
            "At least one of requirement_ids or jira_keys is required "
            "(both may be passed together).")

    combined = len(req_ids) + len(jira_keys)
    if combined > 20:
        return json_error(
            "BATCH_TOO_LARGE",
            f"Select 20 or fewer tickets per batch, or use bulk import "
            f"on the Requirements page. Got {combined}.",
            http=400,
        )

    tenant_id = request.user["tenant_id"]
    user_id = request.user["id"]

    jobs_payload = []
    db = SessionLocal()
    try:
        # Jira-keys path: import missing requirements first, fold them
        # into req_ids. A key whose requirement already exists resolves
        # to the existing requirement_id (no duplicate row).
        if jira_keys:
            env = db.query(Environment).filter_by(
                id=int(env_id), tenant_id=tenant_id).first()
            if env is None:
                return json_error("NOT_FOUND", "Environment not found", http=404)
            if not env.jira_connection_id:
                return json_error(
                    "VALIDATION_ERROR",
                    "Environment has no Jira connection; cannot import "
                    "by jira_keys.", http=400)
            conn_row = ConnectionRepository(db).get_connection_decrypted(
                env.jira_connection_id, tenant_id)
            if not conn_row:
                return json_error("NOT_FOUND",
                                  "Jira connection not found", http=404)
            cfg = conn_row["config"]
            jira_base = cfg.get("base_url", "").rstrip("/")
            jira_auth = None
            if (cfg.get("auth_type") == "basic"
                    and cfg.get("username") and cfg.get("api_token")):
                import base64
                jira_auth = base64.b64encode(
                    f"{cfg['username']}:{cfg['api_token']}".encode()
                ).decode()

            svc = TestManagementService(
                SectionRepository(db), RequirementRepository(db),
                TestCaseRepository(db), TestSuiteRepository(db),
                BAReviewRepository(db), MetadataImpactRepository(db),
            )

            # Default section: oldest non-deleted section in the tenant.
            # If none exist, create an "Inbox" section so imports have
            # a home. Keeps the flow from failing just because the
            # tenant hasn't set up sections yet.
            from primeqa.test_management.models import Section
            default_section = (db.query(Section)
                               .filter(Section.tenant_id == tenant_id,
                                       Section.deleted_at.is_(None))
                               .order_by(Section.id.asc())
                               .first())
            if default_section is None:
                default_section = Section(
                    tenant_id=tenant_id, name="Inbox", created_by=user_id)
                db.add(default_section); db.commit(); db.refresh(default_section)

            # Resolve each Jira key → requirement_id (import if missing)
            for key in jira_keys:
                try:
                    existing = svc.requirement_repo.find_by_jira_key(
                        tenant_id, key)
                    if existing:
                        req_id = existing.id
                        jira_key_result = key
                    else:
                        imported = svc.import_jira_requirement(
                            tenant_id=tenant_id,
                            section_id=default_section.id,
                            jira_base_url=jira_base,
                            jira_key=key,
                            created_by=user_id,
                            jira_auth=jira_auth,
                        )
                        req_id = imported["id"]
                        jira_key_result = key
                    # Now queue a generation job for this requirement
                    job, already = create_or_get_job(
                        db, tenant_id=tenant_id,
                        environment_id=int(env_id),
                        requirement_id=req_id,
                        created_by=user_id,
                    )
                    jobs_payload.append({
                        "requirement_id": req_id,
                        "jira_key": jira_key_result,
                        "job_id": job.id,
                        "status": job.status,
                        "already_running": already,
                    })
                except Exception as e:
                    jobs_payload.append({
                        "requirement_id": None,
                        "jira_key": key,
                        "job_id": None, "status": "error",
                        "error": str(e)[:200],
                    })

        # Legacy path: requirement_ids directly
        for req_id in req_ids:
            try:
                job, already = create_or_get_job(
                    db, tenant_id=tenant_id,
                    environment_id=int(env_id),
                    requirement_id=int(req_id),
                    created_by=user_id,
                )
                jobs_payload.append({
                    "requirement_id": int(req_id),
                    "job_id": job.id,
                    "status": job.status,
                    "already_running": already,
                })
            except Exception as e:
                jobs_payload.append({
                    "requirement_id": int(req_id),
                    "job_id": None, "status": "error",
                    "error": str(e)[:200],
                })
    finally:
        db.close()

    return jsonify({"jobs": jobs_payload, "total": len(jobs_payload)}), 202


# ---- Test cases -------------------------------------------------------------

@test_management_bp.route("/api/test-cases", methods=["GET"])
@require_auth
def list_test_cases():
    svc, db = _get_service()
    try:
        if "page" in request.args or "per_page" in request.args or "q" in request.args \
                or "sort" in request.args:
            params = parse_list_params(
                request,
                allowed_filters=["status", "requirement_id", "section_id",
                                 "owner_id", "visibility"],
            )
            def run():
                page, serializer = svc.list_test_cases_page(
                    request.user["tenant_id"], request.user["id"],
                    page=params["page"], per_page=params["per_page"],
                    q=params["q"], sort=params["sort"], order=params["order"],
                    filters=params["filters"], include_deleted=params["show_deleted"],
                )
                return json_page(page, serialize=serializer)
            return _handle(run)
        # Legacy unpaginated response for existing clients/tests
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
        return json_error("VALIDATION_ERROR", "title is required")
    svc, db = _get_service()
    try:
        def run():
            tc = svc.create_test_case(
                request.user["tenant_id"], data["title"],
                request.user["id"], request.user["id"],
                requirement_id=data.get("requirement_id"),
                section_id=data.get("section_id"),
                visibility=data.get("visibility", "private"),
            )
            return jsonify(tc), 201
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/test-cases/<int:tc_id>", methods=["GET"])
@require_auth
def get_test_case(tc_id):
    svc, db = _get_service()
    try:
        def run():
            tc = svc.get_test_case(tc_id, request.user["tenant_id"], request.user["id"])
            return jsonify(tc), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/test-cases/<int:tc_id>", methods=["PATCH"])
@require_role("admin", "tester")
def update_test_case(tc_id):
    data = request.get_json(silent=True) or {}
    expected_version = data.pop("expected_version", None)
    svc, db = _get_service()
    try:
        def run():
            tc = svc.update_test_case(
                tc_id, request.user["tenant_id"], data,
                expected_version=expected_version, user_id=request.user["id"],
            )
            return jsonify(tc), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/test-cases/<int:tc_id>/feedback", methods=["POST"])
@require_auth
def submit_test_case_feedback(tc_id):
    """Phase 7: explicit user feedback on an AI-generated test case.

    Body: {
      verdict: "up" | "down"            (required)
      reason:  "wrong_object_or_field" | "invalid_steps" |
               "missing_coverage" | "redundant" | "other"   (optional; for down)
      reason_text: string                                   (optional; required for "other")
    }

    Always returns 200 on successful submission. When the user has
    exceeded the per-TC daily limit (5 signals), the response carries
    `throttled: true` and no signal is written — this is intentional to
    not give spammers a visible rejection signal.

    Open to any authenticated user: feedback signal value is in volume;
    gating it cuts the volume with no quality upside.
    """
    data = request.get_json(silent=True) or {}
    svc, db = _get_service()
    try:
        def run():
            result = svc.submit_user_feedback(
                tc_id,
                request.user["tenant_id"],
                request.user["id"],
                verdict=data.get("verdict"),
                reason=data.get("reason"),
                reason_text=data.get("reason_text"),
            )
            return jsonify(result), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/test-cases/<int:tc_id>", methods=["DELETE"])
@require_role("admin", "tester")
def delete_test_case(tc_id):
    svc, db = _get_service()
    try:
        def run():
            tc = svc.delete_test_case(tc_id, request.user["tenant_id"], request.user["id"])
            return jsonify(tc), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/test-cases/<int:tc_id>/restore", methods=["POST"])
@require_role("admin", "tester")
def restore_test_case(tc_id):
    svc, db = _get_service()
    try:
        def run():
            tc = svc.restore_test_case(tc_id, request.user["tenant_id"], request.user["id"])
            return jsonify(tc), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/test-cases/<int:tc_id>/purge", methods=["POST"])
@require_role("admin")
def purge_test_case(tc_id):
    svc, db = _get_service()
    try:
        def run():
            svc.purge_test_case(tc_id, request.user["tenant_id"], request.user["id"])
            return jsonify({"message": "Purged"}), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/test-cases/<int:tc_id>/share", methods=["POST"])
@require_auth
def share_test_case(tc_id):
    svc, db = _get_service()
    try:
        def run():
            tc = svc.share_test_case(tc_id, request.user["tenant_id"], request.user["id"])
            return jsonify(tc), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/test-cases/<int:tc_id>/activate", methods=["POST"])
@require_role("admin")
def activate_test_case(tc_id):
    svc, db = _get_service()
    try:
        def run():
            tc = svc.activate_test_case(tc_id, request.user["tenant_id"],
                                        user_id=request.user["id"])
            return jsonify(tc), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/test-cases/<int:tc_id>/versions", methods=["GET"])
@require_auth
def list_versions(tc_id):
    svc, db = _get_service()
    try:
        def run():
            versions = svc.list_versions(tc_id, request.user["tenant_id"])
            return jsonify(versions), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/test-cases/<int:tc_id>/versions", methods=["POST"])
@require_role("admin", "tester")
def create_version(tc_id):
    data = request.get_json(silent=True) or {}
    if not data.get("metadata_version_id"):
        return json_error("VALIDATION_ERROR", "metadata_version_id is required")
    svc, db = _get_service()
    try:
        def run():
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
        return _handle(run)
    finally:
        db.close()


# ---- Suites -----------------------------------------------------------------

@test_management_bp.route("/api/suites", methods=["GET"])
@require_auth
def list_suites():
    svc, db = _get_service()
    try:
        if "page" in request.args or "per_page" in request.args or "q" in request.args:
            params = parse_list_params(request, allowed_filters=["suite_type"])
            def run():
                page, serializer = svc.list_suites_page(
                    request.user["tenant_id"],
                    page=params["page"], per_page=params["per_page"],
                    q=params["q"], sort=params["sort"], order=params["order"],
                    filters=params["filters"], include_deleted=params["show_deleted"],
                )
                return json_page(page, serialize=serializer)
            return _handle(run)
        return jsonify(svc.list_suites(request.user["tenant_id"])), 200
    finally:
        db.close()


@test_management_bp.route("/api/suites", methods=["POST"])
@require_role("admin", "tester")
@require_permission("manage_test_suites")
def create_suite():
    data = request.get_json(silent=True) or {}
    if not data.get("name") or not data.get("suite_type"):
        return json_error("VALIDATION_ERROR", "name and suite_type are required")
    svc, db = _get_service()
    try:
        def run():
            suite = svc.create_suite(
                request.user["tenant_id"], data["name"],
                data["suite_type"], request.user["id"],
                description=data.get("description"),
            )
            return jsonify(suite), 201
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/suites/<int:suite_id>", methods=["GET"])
@require_auth
def get_suite(suite_id):
    """Fetch a single suite by id (added post-QA-sweep, finding 11.1.8)."""
    svc, db = _get_service()
    try:
        def run():
            suite = svc.get_suite(suite_id, request.user["tenant_id"])
            return jsonify(suite), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/suites/<int:suite_id>", methods=["PATCH"])
@require_role("admin", "tester")
@require_permission("manage_test_suites")
def update_suite(suite_id):
    data = request.get_json(silent=True) or {}
    expected_version = data.pop("expected_version", None)
    svc, db = _get_service()
    try:
        def run():
            suite = svc.update_suite(
                suite_id, request.user["tenant_id"], data,
                expected_version=expected_version, user_id=request.user["id"],
            )
            return jsonify(suite), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/suites/<int:suite_id>", methods=["DELETE"])
@require_role("admin", "tester")
@require_permission("manage_test_suites")
def delete_suite(suite_id):
    svc, db = _get_service()
    try:
        def run():
            s = svc.delete_suite(suite_id, request.user["tenant_id"], request.user["id"])
            return jsonify(s), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/suites/<int:suite_id>/restore", methods=["POST"])
@require_role("admin", "tester")
@require_permission("manage_test_suites")
def restore_suite(suite_id):
    svc, db = _get_service()
    try:
        def run():
            s = svc.restore_suite(suite_id, request.user["tenant_id"], request.user["id"])
            return jsonify(s), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/suites/<int:suite_id>/purge", methods=["POST"])
@require_role("admin")
@require_permission("manage_test_suites")
def purge_suite(suite_id):
    svc, db = _get_service()
    try:
        def run():
            svc.purge_suite(suite_id, request.user["tenant_id"], request.user["id"])
            return jsonify({"message": "Purged"}), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/suites/<int:suite_id>/test-cases", methods=["GET"])
@require_auth
def get_suite_test_cases(suite_id):
    svc, db = _get_service()
    try:
        def run():
            tcs = svc.get_suite_test_cases(suite_id, request.user["tenant_id"])
            return jsonify(tcs), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/suites/<int:suite_id>/test-cases", methods=["POST"])
@require_role("admin", "tester")
@require_permission("manage_test_suites")
def add_to_suite(suite_id):
    data = request.get_json(silent=True) or {}
    if not data.get("test_case_id"):
        return json_error("VALIDATION_ERROR", "test_case_id is required")
    svc, db = _get_service()
    try:
        def run():
            svc.add_to_suite(suite_id, data["test_case_id"],
                             request.user["tenant_id"], data.get("position", 0))
            return jsonify({"message": "Added"}), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/suites/<int:suite_id>/test-cases/bulk", methods=["POST"])
@require_role("admin", "tester")
@require_permission("manage_test_suites")
def add_to_suite_bulk(suite_id):
    """Add many test cases to a suite in one call.
    Body: {test_case_ids: [int, ...]}
    Returns: {added: [...], already_in: [...], skipped: [...]}
    """
    from primeqa.shared.api import BULK_MAX_ITEMS
    data = request.get_json(silent=True) or {}
    tc_ids = data.get("test_case_ids") or []
    if not isinstance(tc_ids, list) or not tc_ids:
        return json_error("VALIDATION_ERROR",
                          "test_case_ids must be a non-empty array")
    # Audit F5 (2026-04-19): non-destructive bulks still need a cap —
    # an unbounded list turns into a DoS on the suite_test_cases table.
    if len(tc_ids) > BULK_MAX_ITEMS:
        return json_error(
            "BULK_LIMIT",
            f"Bulk operations are limited to {BULK_MAX_ITEMS} items per call",
            http=400,
        )
    svc, db = _get_service()
    try:
        def run():
            result = svc.add_to_suite_bulk(
                suite_id, tc_ids,
                request.user["tenant_id"], request.user["id"],
            )
            return jsonify(result), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/suites/<int:suite_id>/test-cases/<int:tc_id>", methods=["DELETE"])
@require_role("admin", "tester")
@require_permission("manage_test_suites")
def remove_from_suite(suite_id, tc_id):
    svc, db = _get_service()
    try:
        def run():
            svc.remove_from_suite(suite_id, tc_id, request.user["tenant_id"])
            return jsonify({"message": "Removed"}), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/suites/<int:suite_id>/test-cases/<int:tc_id>/reorder", methods=["PATCH"])
@require_role("admin", "tester")
@require_permission("manage_test_suites")
def reorder_in_suite(suite_id, tc_id):
    data = request.get_json(silent=True) or {}
    svc, db = _get_service()
    try:
        def run():
            svc.reorder_suite_test_case(
                suite_id, tc_id, request.user["tenant_id"], data.get("position", 0),
            )
            return jsonify({"message": "Reordered"}), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/test-cases/<int:tc_id>/revalidate", methods=["POST"])
@require_role("admin", "tester")
def revalidate_test_case(tc_id):
    """Re-run static validation on the test case's current version.
    Optional body: {environment_id} to validate against a specific env's
    current meta version instead of the one the TC was generated against.
    """
    data = request.get_json(silent=True) or {}
    env_id = data.get("environment_id")
    svc, db = _get_service()
    try:
        def run():
            from primeqa.core.repository import EnvironmentRepository
            from primeqa.metadata.repository import MetadataRepository
            report = svc.revalidate_test_case_version(
                tc_id, request.user["tenant_id"],
                metadata_repo=MetadataRepository(db),
                env_repo=EnvironmentRepository(db) if env_id else None,
                environment_id=int(env_id) if env_id else None,
            )
            return jsonify(report), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/test-cases/<int:tc_id>/apply-validation-fix", methods=["POST"])
@require_role("admin", "tester")
def apply_validation_fix(tc_id):
    """Create a new TC version with a single suggested fix applied.
    Body: {issue: {...}, replacement: "LastActivityDate"}.
    """
    data = request.get_json(silent=True) or {}
    issue = data.get("issue")
    replacement = data.get("replacement")
    if not issue or not replacement:
        return json_error("VALIDATION_ERROR",
                          "issue and replacement are required")
    svc, db = _get_service()
    try:
        def run():
            from primeqa.metadata.repository import MetadataRepository
            result = svc.apply_validation_fix(
                tc_id, request.user["tenant_id"],
                issue, replacement,
                created_by=request.user["id"],
                metadata_repo=MetadataRepository(db),
            )
            return jsonify(result), 200
        return _handle(run)
    finally:
        db.close()


# ---- Reviews ----------------------------------------------------------------

@test_management_bp.route("/api/reviews", methods=["GET"])
@require_role("admin", "ba")
def list_reviews():
    svc, db = _get_service()
    try:
        if "page" in request.args or "per_page" in request.args:
            params = parse_list_params(
                request, allowed_filters=["status", "assigned_to", "reviewed_by"],
                default_sort="created_at",
            )
            def run():
                page, serializer = svc.list_reviews_page(
                    request.user["tenant_id"],
                    page=params["page"], per_page=params["per_page"],
                    q=params["q"], sort=params["sort"], order=params["order"],
                    filters=params["filters"], include_deleted=params["show_deleted"],
                )
                return json_page(page, serialize=serializer)
            return _handle(run)
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
        return json_error("VALIDATION_ERROR", f"Missing: {', '.join(missing)}")
    svc, db = _get_service()
    try:
        def run():
            review = svc.assign_review(
                request.user["tenant_id"],
                data["test_case_version_id"], data["assigned_to"],
            )
            return jsonify(review), 201
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/reviews/<int:review_id>", methods=["PATCH"])
@require_role("admin", "ba")
@require_permission("review_test_cases")
def submit_review(review_id):
    data = request.get_json(silent=True) or {}
    if not data.get("status"):
        return json_error("VALIDATION_ERROR", "status is required")
    if data["status"] not in ("approved", "rejected", "needs_edit"):
        return json_error("VALIDATION_ERROR", "Invalid status")
    svc, db = _get_service()
    try:
        def run():
            review = svc.submit_review(
                review_id, data["status"],
                feedback=data.get("feedback"),
                reviewed_by=request.user["id"],
                step_comments=data.get("step_comments"),
                reason=data.get("reason"),
            )
            return jsonify(review), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/reviews/<int:review_id>", methods=["DELETE"])
@require_role("admin", "ba")
def delete_review(review_id):
    svc, db = _get_service()
    try:
        def run():
            r = svc.delete_review(review_id, request.user["tenant_id"], request.user["id"])
            return jsonify(r), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/reviews/<int:review_id>/restore", methods=["POST"])
@require_role("admin", "ba")
def restore_review(review_id):
    svc, db = _get_service()
    try:
        def run():
            r = svc.restore_review(review_id, request.user["tenant_id"], request.user["id"])
            return jsonify(r), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/reviews/<int:review_id>/purge", methods=["POST"])
@require_role("admin")
def purge_review(review_id):
    svc, db = _get_service()
    try:
        def run():
            svc.purge_review(review_id, request.user["tenant_id"], request.user["id"])
            return jsonify({"message": "Purged"}), 200
        return _handle(run)
    finally:
        db.close()


# ---- Impacts ----------------------------------------------------------------

@test_management_bp.route("/api/impacts", methods=["GET"])
@require_auth
def list_impacts():
    svc, db = _get_service()
    try:
        if "page" in request.args or "per_page" in request.args or "q" in request.args:
            params = parse_list_params(
                request, allowed_filters=["resolution", "impact_type", "test_case_id"],
                default_sort="created_at",
            )
            def run():
                page, serializer = svc.list_impacts_page(
                    request.user["tenant_id"],
                    page=params["page"], per_page=params["per_page"],
                    q=params["q"], sort=params["sort"], order=params["order"],
                    filters=params["filters"], include_deleted=params["show_deleted"],
                )
                return json_page(page, serialize=serializer)
            return _handle(run)
        return jsonify(svc.list_pending_impacts(request.user["tenant_id"])), 200
    finally:
        db.close()


@test_management_bp.route("/api/impacts/<int:impact_id>/resolve", methods=["POST"])
@require_role("admin", "tester")
def resolve_impact(impact_id):
    data = request.get_json(silent=True) or {}
    if not data.get("resolution"):
        return json_error("VALIDATION_ERROR", "resolution is required")
    if data["resolution"] not in ("regenerated", "edited", "dismissed"):
        return json_error("VALIDATION_ERROR", "Invalid resolution")
    svc, db = _get_service()
    try:
        def run():
            impact = svc.resolve_impact(impact_id, data["resolution"], request.user["id"])
            return jsonify(impact), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/impacts/<int:impact_id>", methods=["DELETE"])
@require_role("admin", "tester")
def delete_impact(impact_id):
    svc, db = _get_service()
    try:
        def run():
            i = svc.delete_impact(impact_id, request.user["tenant_id"], request.user["id"])
            return jsonify(i), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/impacts/<int:impact_id>/restore", methods=["POST"])
@require_role("admin", "tester")
def restore_impact(impact_id):
    svc, db = _get_service()
    try:
        def run():
            i = svc.restore_impact(impact_id, request.user["tenant_id"], request.user["id"])
            return jsonify(i), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/impacts/<int:impact_id>/purge", methods=["POST"])
@require_role("admin")
def purge_impact(impact_id):
    svc, db = _get_service()
    try:
        def run():
            svc.purge_impact(impact_id, request.user["tenant_id"], request.user["id"])
            return jsonify({"message": "Purged"}), 200
        return _handle(run)
    finally:
        db.close()


# ---- Test case run history --------------------------------------------------

@test_management_bp.route("/api/test-cases/<int:tc_id>/runs", methods=["GET"])
@require_auth
def test_case_run_history(tc_id):
    db = next(get_db())
    try:
        from primeqa.execution.models import PipelineRun, RunTestResult
        results = db.query(RunTestResult).join(
            PipelineRun, RunTestResult.run_id == PipelineRun.id,
        ).filter(
            RunTestResult.test_case_id == tc_id,
            PipelineRun.tenant_id == request.user["tenant_id"],
        ).order_by(RunTestResult.executed_at.desc()).limit(20).all()
        return jsonify([{
            "id": r.id, "run_id": r.run_id, "status": r.status,
            "failure_type": r.failure_type, "failure_summary": r.failure_summary,
            "duration_ms": r.duration_ms, "passed_steps": r.passed_steps,
            "failed_steps": r.failed_steps, "total_steps": r.total_steps,
            "executed_at": r.executed_at.isoformat() if r.executed_at else None,
        } for r in results]), 200
    finally:
        db.close()


# ---- Step schema + metadata lookup -----------------------------------------

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
        if not env or not env.current_meta_version_id:
            return jsonify([]), 200
        repo = MetadataRepository(db)
        objects = repo.get_objects(env.current_meta_version_id)
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
        if not env or not env.current_meta_version_id:
            return jsonify([]), 200
        repo = MetadataRepository(db)
        obj = repo.get_object_by_api_name(env.current_meta_version_id, object_name)
        if not obj:
            return jsonify([]), 200
        fields = repo.get_fields(env.current_meta_version_id, obj.id)
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


@test_management_bp.route("/api/test-cases/bulk", methods=["POST"])
@require_role("admin", "tester")
def bulk_test_cases():
    data = request.get_json(silent=True) or {}
    raw_ids = data.get("ids") or []
    action = data.get("action")
    payload = data.get("payload") or {}
    if not raw_ids or not action:
        return json_error("VALIDATION_ERROR", "ids and action are required")
    # Audit fix C-3 (2026-04-19): coerce each id to a positive int. A
    # string like "a" or None previously crashed with int() ValueError.
    ids = []
    for x in raw_ids:
        try:
            n = int(x)
        except (TypeError, ValueError):
            return json_error("VALIDATION_ERROR",
                              f"every id must be a positive integer; got {x!r}",
                              http=400)
        if n <= 0:
            return json_error("VALIDATION_ERROR",
                              f"every id must be positive; got {n}", http=400)
        ids.append(n)

    svc, db = _get_service()
    try:
        def run():
            if action in _DESTRUCTIVE_BULK_ACTIONS:
                require_bulk_confirm(data, ids)
            else:
                # still enforce the hard cap on non-destructive actions
                from primeqa.shared.api import BULK_MAX_ITEMS
                if len(ids) > BULK_MAX_ITEMS:
                    raise BulkLimitError(
                        f"Bulk action exceeds the {BULK_MAX_ITEMS}-item limit",
                        details={"limit": BULK_MAX_ITEMS, "received": len(ids)},
                    )
            result = svc.bulk_test_cases(
                request.user["tenant_id"], request.user["id"],
                ids, action, payload,
            )
            return jsonify(result), 200
        return _handle(run)
    finally:
        db.close()


@test_management_bp.route("/api/test-cases/bulk/purge", methods=["POST"])
@require_role("admin")
def bulk_purge_test_cases():
    data = request.get_json(silent=True) or {}
    raw_ids = data.get("ids") or []
    if not raw_ids:
        return json_error("VALIDATION_ERROR", "ids is required")
    ids = []
    for x in raw_ids:
        try:
            n = int(x)
        except (TypeError, ValueError):
            return json_error("VALIDATION_ERROR",
                              f"every id must be a positive integer; got {x!r}",
                              http=400)
        if n <= 0:
            return json_error("VALIDATION_ERROR",
                              f"every id must be positive; got {n}", http=400)
        ids.append(n)
    svc, db = _get_service()
    try:
        def run():
            require_bulk_confirm(data, ids)
            result = svc.bulk_purge_test_cases(
                request.user["tenant_id"], request.user["id"], ids,
            )
            return jsonify(result), 200
        return _handle(run)
    finally:
        db.close()


# ---- AI generation ----------------------------------------------------------

@test_management_bp.route("/api/test-cases/generate", methods=["POST"])
@require_role("admin", "tester")
def generate_test_case():
    data = request.get_json(silent=True) or {}
    for f in ["requirement_id", "environment_id"]:
        if not data.get(f):
            return json_error("VALIDATION_ERROR", f"{f} is required")
    svc, db = _get_service()
    try:
        def run():
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
        return _handle(run)
    except Exception as e:
        return json_error("GENERATION_FAILED", f"Generation failed: {e}", http=500)
    finally:
        db.close()
