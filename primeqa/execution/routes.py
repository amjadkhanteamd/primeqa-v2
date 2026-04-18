"""API routes for the execution domain.

Endpoints: /api/runs/*, /api/environments/<id>/slots
"""

from flask import Blueprint, jsonify, request

from primeqa.core.auth import require_auth, require_role
from primeqa.db import get_db
from primeqa.execution.repository import (
    PipelineRunRepository, PipelineStageRepository,
    ExecutionSlotRepository, WorkerHeartbeatRepository,
    RunTestResultRepository, RunStepResultRepository,
    RunCreatedEntityRepository,
)
from primeqa.execution.service import PipelineService
from primeqa.execution.cleanup import CleanupEngine, CleanupAttemptRepository
from primeqa.execution.data_engine import DataEngineService, DataTemplate, DataFactory

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


# ---- Jira picker endpoints for Run Wizard (R1) -----------------------------
# Thin pass-through to Jira REST using the stored Jira connection. Fetched
# on demand (user clicks "Load projects" etc.; no TTL cache per Q decision).

def _jira_client(db, connection_id, tenant_id):
    from primeqa.core.repository import ConnectionRepository
    from primeqa.runs.wizard import JiraClient
    conn = ConnectionRepository(db).get_connection_decrypted(connection_id, tenant_id)
    if not conn or conn.get("connection_type") != "jira":
        return None
    cfg = conn["config"]
    base = cfg.get("base_url", "").rstrip("/")
    auth = None
    if cfg.get("auth_type") == "basic" and cfg.get("username") and cfg.get("api_token"):
        import base64
        auth = base64.b64encode(f"{cfg['username']}:{cfg['api_token']}".encode()).decode()
    return JiraClient(base, auth)


# ---- Jira ticket search for Run Wizard -------------------------------------
# Env-based: resolve the Jira connection from the selected environment so the
# user doesn't have to pick a connection separately.

def _jira_client_for_env(db, env_id, tenant_id):
    from primeqa.core.repository import EnvironmentRepository
    env = EnvironmentRepository(db).get_environment(env_id, tenant_id)
    if not env or not env.jira_connection_id:
        return None, env
    return _jira_client(db, env.jira_connection_id, tenant_id), env


@execution_bp.route("/api/jira/search", methods=["GET"])
@require_auth
def jira_ticket_search():
    """Ticket-level Jira search for the wizard chip picker.

    Accept: text/html (default) \u2192 HTMX fragment at
    `templates/runs/_jira_search_results.html`.
    Accept: application/json or ?format=json \u2192 JSON payload.

    Params:
      env_id  (required) \u2014 env whose jira_connection_id is used
      q       (required) \u2014 query string (issue key or free text)
      limit   (optional, default 20, max 50)
    """
    from flask import render_template
    # Accept either `env_id` (canonical) or `environment_id` (matches the form
    # field name, in case a future client uses hx-include on the select).
    env_id = (request.args.get("env_id", type=int)
              or request.args.get("environment_id", type=int))
    q = (request.args.get("q") or "").strip()
    limit = request.args.get("limit", default=20, type=int)

    want_json = (request.args.get("format") == "json" or
                 "application/json" in (request.headers.get("Accept") or ""))

    def _render(payload, hint=None, error=None):
        if want_json:
            body = {"results": payload or [], "hint": hint, "error": error,
                    "count": len(payload or [])}
            return jsonify(body), 200
        return render_template("runs/_jira_search_results.html",
                               results=payload or [], hint=hint, error=error), 200

    if not env_id:
        return _render([], hint="Pick an environment above to enable Jira search.")

    if len(q) < 2:
        return _render([], hint="Type at least 2 characters\u2026")

    db = next(get_db())
    try:
        client, env = _jira_client_for_env(db, env_id, request.user["tenant_id"])
        if not client:
            return _render([], hint=(
                "This environment has no Jira connection. "
                "Attach one in Settings \u2192 Environments, or pick a different env."
            ))
        try:
            results = client.search_issues(q, connection_id=env.jira_connection_id, limit=limit)
        except Exception as e:
            return _render([], error=f"Jira search failed: {e}")
        return _render(results)
    finally:
        db.close()


# ---- Run preview (live count as wizard selection changes) ------------------

@execution_bp.route("/api/runs/preview", methods=["POST"])
@require_auth
def run_preview():
    """Read-only live preview. Reuses RunWizardResolver so the resolution
    logic stays identical between the inline chip count and the full
    /runs/new/preview screen.

    Body: {environment_id, run_type,
           jira_keys[], suite_ids[], section_ids[],
           requirement_ids[], test_case_ids[]}
    Returns: {test_case_count, requirement_count, missing_jira_keys[],
              warnings[], over_soft_cap, over_hard_cap}
    """
    from primeqa.runs.wizard import (
        RunWizardResolver, WizardSelection, HARD_CAP, SOFT_CAP,
    )
    from primeqa.test_management.repository import (
        TestSuiteRepository, SectionRepository, TestCaseRepository,
        RequirementRepository,
    )
    from primeqa.core.repository import ConnectionRepository, EnvironmentRepository

    data = request.get_json(silent=True) or {}
    environment_id = data.get("environment_id")
    jira_keys = [k.strip() for k in (data.get("jira_keys") or []) if k.strip()][:100]

    # Resolve the env's Jira connection for any Jira keys
    db = next(get_db())
    try:
        jira_entries = []
        if jira_keys and environment_id:
            env = EnvironmentRepository(db).get_environment(
                environment_id, request.user["tenant_id"],
            )
            if env and env.jira_connection_id:
                jira_entries.append({
                    "type": "issues",
                    "connection_id": env.jira_connection_id,
                    "issue_keys": jira_keys,
                })

        selection = WizardSelection(
            suite_ids=[int(x) for x in (data.get("suite_ids") or []) if str(x).lstrip("-").isdigit()],
            section_ids=[int(x) for x in (data.get("section_ids") or []) if str(x).lstrip("-").isdigit()],
            test_case_ids=[int(x) for x in (data.get("test_case_ids") or []) if str(x).lstrip("-").isdigit()],
            requirement_ids=[int(x) for x in (data.get("requirement_ids") or []) if str(x).lstrip("-").isdigit()],
            jira=jira_entries,
        )

        resolver = RunWizardResolver(
            db,
            suite_repo=TestSuiteRepository(db),
            section_repo=SectionRepository(db),
            tc_repo=TestCaseRepository(db),
            req_repo=RequirementRepository(db),
            connection_repo=ConnectionRepository(db),
        )
        try:
            resolved = resolver.resolve(request.user["tenant_id"], selection)
        except Exception as e:
            return jsonify(error=str(e)), 400

        jira_summary = (resolved.source_refs or {}).get("jira") or []
        req_count = len((resolved.source_refs or {}).get("requirements") or [])
        # Rough requirement count: requirements picked explicitly + jira-resolved ones
        for j in jira_summary:
            req_count += len((j.get("test_case_ids") or []))  # distinct TCs per Jira entry

        # Warning enrichment
        warnings = list(resolved.resolution_warnings)
        if len(jira_keys) > 100:
            warnings.append("Jira selection capped at 100 tickets.")

        return jsonify({
            "test_case_count": resolved.test_count,
            "requirement_count": req_count,
            "missing_jira_keys": resolved.missing_jira_keys,
            "warnings": warnings,
            "over_soft_cap": resolved.over_soft_cap,
            "over_hard_cap": resolved.over_hard_cap,
            "soft_cap": SOFT_CAP,
            "hard_cap": HARD_CAP,
            "summary_text": _build_summary_text(
                len(jira_keys), len(selection.suite_ids),
                len(selection.section_ids), len(selection.requirement_ids),
                len(selection.test_case_ids), resolved.test_count,
            ),
        }), 200
    finally:
        db.close()


def _build_summary_text(jira, suites, sections, reqs, tcs, total):
    parts = []
    if jira:    parts.append(f"{jira} Jira ticket{'s' if jira != 1 else ''}")
    if suites:  parts.append(f"{suites} suite{'s' if suites != 1 else ''}")
    if sections: parts.append(f"{sections} section{'s' if sections != 1 else ''}")
    if reqs:    parts.append(f"{reqs} requirement{'s' if reqs != 1 else ''}")
    if tcs:     parts.append(f"{tcs} test case{'s' if tcs != 1 else ''}")
    if not parts:
        return "Nothing selected yet."
    return f"{', '.join(parts)} \u2192 {total} test case{'s' if total != 1 else ''}"


@execution_bp.route("/api/jira/<int:connection_id>/projects", methods=["GET"])
@require_auth
def jira_projects(connection_id):
    db = next(get_db())
    try:
        client = _jira_client(db, connection_id, request.user["tenant_id"])
        if not client:
            return jsonify(error="Jira connection not found"), 404
        return jsonify(client.list_projects()), 200
    except Exception as e:
        return jsonify(error=f"Jira fetch failed: {e}"), 502
    finally:
        db.close()


@execution_bp.route("/api/jira/<int:connection_id>/projects/<string:project_key>/boards", methods=["GET"])
@require_auth
def jira_boards(connection_id, project_key):
    db = next(get_db())
    try:
        client = _jira_client(db, connection_id, request.user["tenant_id"])
        if not client:
            return jsonify(error="Jira connection not found"), 404
        return jsonify(client.list_boards_for_project(project_key)), 200
    except Exception as e:
        return jsonify(error=f"Jira fetch failed: {e}"), 502
    finally:
        db.close()


@execution_bp.route("/api/jira/<int:connection_id>/boards/<int:board_id>/sprints", methods=["GET"])
@require_auth
def jira_sprints(connection_id, board_id):
    db = next(get_db())
    try:
        client = _jira_client(db, connection_id, request.user["tenant_id"])
        if not client:
            return jsonify(error="Jira connection not found"), 404
        states = request.args.get("state", "active,closed,future")
        return jsonify(client.list_sprints(board_id, states)), 200
    except Exception as e:
        return jsonify(error=f"Jira fetch failed: {e}"), 502
    finally:
        db.close()


@execution_bp.route("/api/runs/<int:run_id>/events", methods=["GET"])
@require_auth
def stream_run_events(run_id):
    """Server-Sent Events endpoint for live run timeline updates.

    Browser subscribes here; worker publishes step_started/step_finished/
    run_status events to the in-process EventBus. Falls back to DB snapshots
    every 5s when no bus events arrive (handles multi-process setups).
    """
    from flask import Response
    from primeqa.runs.streams import stream_run_events as sse_gen
    tenant_id = request.user["tenant_id"]
    db = next(get_db())
    try:
        # Authorization: confirm the user can see this run (tenant-scoped)
        run = PipelineRunRepository(db).get_run(run_id, tenant_id)
        if not run:
            return jsonify(error="Run not found"), 404
    finally:
        db.close()

    def snapshot():
        # Fresh session per snapshot; keeps things simple and avoids stale reads
        snap_db = next(get_db())
        try:
            run = PipelineRunRepository(snap_db).get_run(run_id, tenant_id)
            if not run:
                return {"status": "unknown"}
            stages = PipelineStageRepository(snap_db).get_stages(run_id)
            test_results = RunTestResultRepository(snap_db).list_results(run_id)
            return {
                "status": run.status,
                "passed": run.passed, "failed": run.failed,
                "total_tests": run.total_tests,
                "stages": [{"stage_name": s.stage_name, "status": s.status} for s in stages],
                "tests": [
                    {"id": r.id, "test_case_id": r.test_case_id,
                     "status": r.status,
                     "failure_summary": r.failure_summary}
                    for r in test_results
                ],
            }
        finally:
            snap_db.close()

    resp = Response(sse_gen(run_id, snapshot), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


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


# --- Results ---

@execution_bp.route("/api/runs/<int:run_id>/results", methods=["GET"])
@require_auth
def list_results(run_id):
    db = next(get_db())
    try:
        repo = RunTestResultRepository(db)
        step_repo = RunStepResultRepository(db)
        results = repo.list_results(run_id)
        output = []
        for r in results:
            steps = step_repo.list_step_results(r.id)
            output.append({
                "id": r.id, "run_id": r.run_id, "test_case_id": r.test_case_id,
                "status": r.status, "failure_type": r.failure_type,
                "failure_summary": r.failure_summary,
                "total_steps": r.total_steps, "passed_steps": r.passed_steps,
                "failed_steps": r.failed_steps, "duration_ms": r.duration_ms,
                "steps": [{
                    "id": s.id, "step_order": s.step_order,
                    "step_action": s.step_action, "target_object": s.target_object,
                    "target_record_id": s.target_record_id, "status": s.status,
                    "execution_state": s.execution_state,
                    "before_state": s.before_state, "after_state": s.after_state,
                    "field_diff": s.field_diff, "api_request": s.api_request,
                    "api_response": s.api_response, "error_message": s.error_message,
                    "duration_ms": s.duration_ms,
                } for s in steps],
            })
        return jsonify(output), 200
    finally:
        db.close()


@execution_bp.route("/api/runs/<int:run_id>/results/<int:result_id>/steps", methods=["GET"])
@require_auth
def get_step_results(run_id, result_id):
    db = next(get_db())
    try:
        repo = RunStepResultRepository(db)
        steps = repo.list_step_results(result_id)
        return jsonify([{
            "id": s.id, "step_order": s.step_order,
            "step_action": s.step_action, "target_object": s.target_object,
            "target_record_id": s.target_record_id, "status": s.status,
            "execution_state": s.execution_state,
            "before_state": s.before_state, "after_state": s.after_state,
            "field_diff": s.field_diff, "api_request": s.api_request,
            "api_response": s.api_response, "error_message": s.error_message,
            "duration_ms": s.duration_ms,
        } for s in steps]), 200
    finally:
        db.close()


# --- Cleanup ---

@execution_bp.route("/api/runs/<int:run_id>/cleanup-status", methods=["GET"])
@require_auth
def get_cleanup_status(run_id):
    db = next(get_db())
    try:
        entity_repo = RunCreatedEntityRepository(db)
        cleanup_repo = CleanupAttemptRepository(db)
        engine = CleanupEngine(entity_repo, cleanup_repo)
        status = engine.get_cleanup_status(run_id)
        return jsonify(status), 200
    finally:
        db.close()


@execution_bp.route("/api/runs/<int:run_id>/retry-cleanup", methods=["POST"])
@require_role("admin", "tester")
def retry_cleanup(run_id):
    db = next(get_db())
    try:
        from primeqa.core.models import Environment
        from primeqa.execution.models import PipelineRun
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if not run:
            return jsonify(error="Run not found"), 404
        env = db.query(Environment).filter(Environment.id == run.environment_id).first()
        entity_repo = RunCreatedEntityRepository(db)
        cleanup_repo = CleanupAttemptRepository(db)
        engine = CleanupEngine(entity_repo, cleanup_repo)
        result = engine.retry_cleanup(run_id, env)
        return jsonify(result), 200
    finally:
        db.close()


@execution_bp.route("/api/environments/<int:env_id>/orphaned-records", methods=["GET"])
@require_auth
def get_orphaned_records(env_id):
    db = next(get_db())
    try:
        entity_repo = RunCreatedEntityRepository(db)
        cleanup_repo = CleanupAttemptRepository(db)
        engine = CleanupEngine(entity_repo, cleanup_repo)
        orphaned = engine.get_orphaned_records(env_id)
        return jsonify(orphaned), 200
    finally:
        db.close()


@execution_bp.route("/api/environments/<int:env_id>/emergency-cleanup", methods=["POST"])
@require_role("admin")
def emergency_cleanup(env_id):
    db = next(get_db())
    try:
        from primeqa.core.models import Environment
        from primeqa.core.repository import EnvironmentRepository
        env_repo = EnvironmentRepository(db)
        env = env_repo.get_environment(env_id)
        if not env:
            return jsonify(error="Environment not found"), 404
        creds = env_repo.get_credentials_decrypted(env_id)
        if not creds or not creds.get("access_token"):
            return jsonify(error="No credentials for this environment"), 400
        from primeqa.execution.executor import SalesforceExecutionClient
        sf = SalesforceExecutionClient(env.sf_instance_url, env.sf_api_version, creds["access_token"])
        entity_repo = RunCreatedEntityRepository(db)
        cleanup_repo = CleanupAttemptRepository(db)
        engine = CleanupEngine(entity_repo, cleanup_repo, sf)
        data = request.get_json(silent=True) or {}
        result = engine.emergency_cleanup(env, data.get("sobject_types"))
        return jsonify(result), 200
    finally:
        db.close()


# --- Test Data Engine ---

@execution_bp.route("/api/data/templates", methods=["GET"])
@require_auth
def list_data_templates():
    db = next(get_db())
    try:
        svc = DataEngineService(db)
        tmpls = svc.list_templates(request.user["tenant_id"], object_type=request.args.get("object_type"))
        return jsonify([{
            "id": t.id, "name": t.name, "description": t.description,
            "object_type": t.object_type, "field_values": t.field_values,
        } for t in tmpls]), 200
    finally:
        db.close()


@execution_bp.route("/api/data/templates", methods=["POST"])
@require_role("admin", "tester")
def create_data_template():
    data = request.get_json(silent=True) or {}
    for f in ["name", "object_type"]:
        if not data.get(f):
            return jsonify(error=f"{f} is required"), 400
    db = next(get_db())
    try:
        svc = DataEngineService(db)
        t = svc.create_template(
            request.user["tenant_id"], data["name"], data["object_type"],
            data.get("field_values", {}), request.user["id"],
            description=data.get("description"),
        )
        return jsonify({"id": t.id, "name": t.name}), 201
    finally:
        db.close()


@execution_bp.route("/api/data/factories", methods=["GET"])
@require_auth
def list_data_factories():
    db = next(get_db())
    try:
        svc = DataEngineService(db)
        factories = svc.list_factories(request.user["tenant_id"])
        return jsonify([{
            "id": f.id, "name": f.name, "description": f.description,
            "factory_type": f.factory_type, "config": f.config,
        } for f in factories]), 200
    finally:
        db.close()


@execution_bp.route("/api/data/factories", methods=["POST"])
@require_role("admin", "tester")
def create_data_factory():
    data = request.get_json(silent=True) or {}
    for f in ["name", "factory_type"]:
        if not data.get(f):
            return jsonify(error=f"{f} is required"), 400
    db = next(get_db())
    try:
        svc = DataEngineService(db)
        factory = svc.create_factory(
            request.user["tenant_id"], data["name"], data["factory_type"],
            data.get("config", {}), request.user["id"],
            description=data.get("description"),
        )
        return jsonify({"id": factory.id, "name": factory.name}), 201
    finally:
        db.close()


@execution_bp.route("/api/data/factories/<int:fid>/preview", methods=["POST"])
@require_auth
def preview_factory(fid):
    db = next(get_db())
    try:
        f = db.query(DataFactory).filter(
            DataFactory.id == fid, DataFactory.tenant_id == request.user["tenant_id"],
        ).first()
        if not f:
            return jsonify(error="Factory not found"), 404
        svc = DataEngineService(db)
        samples = [svc.generate_value(f.factory_type, f.config) for _ in range(5)]
        return jsonify({"samples": samples}), 200
    finally:
        db.close()
