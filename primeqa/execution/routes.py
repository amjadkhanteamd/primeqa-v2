"""API routes for the execution domain.

Endpoints: /api/runs/*, /api/environments/<id>/slots
"""

from flask import Blueprint, jsonify, request

from primeqa.core.auth import require_auth, require_role
from primeqa.core.permissions import require_run_permission
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
from primeqa.shared.api import json_error

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
@require_run_permission("single_run")
def create_run():
    # require_run_permission has already enforced:
    #   - layer 1: user holds `run_single_ticket` (or is superadmin)
    #   - layer 2: env.allow_single_run is true + prod confirmation if needed
    # It also extracted environment_id so we know it's a valid int here.
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
        return json_error("VALIDATION_ERROR", str(e), http=400)
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
      env_id  \u2014 env whose jira_connection_id is used (run wizard path)
      conn_id \u2014 direct Jira connection (requirements import path)
      q       (required) \u2014 query string (issue key or free text)
      limit   (optional, default 20, max 50)

    One of env_id / conn_id is required; conn_id wins if both are passed.
    """
    from flask import render_template
    # Accept either `env_id` (canonical) or `environment_id` (matches the form
    # field name, in case a future client uses hx-include on the select).
    env_id = (request.args.get("env_id", type=int)
              or request.args.get("environment_id", type=int))
    # Direct connection: used by the requirements-import chip picker, which
    # doesn't know/need an environment \u2014 the user has explicitly chosen
    # which Jira to pull from.
    conn_id = (request.args.get("conn_id", type=int)
               or request.args.get("jira_connection_id", type=int))
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

    if not env_id and not conn_id:
        return _render([], hint="Pick an environment or Jira connection to enable search.")

    if len(q) < 2:
        return _render([], hint="Type at least 2 characters\u2026")

    db = next(get_db())
    try:
        if conn_id:
            client = _jira_client(db, conn_id, request.user["tenant_id"])
            if not client:
                return _render([], hint="Jira connection not found or not configured.")
            effective_conn_id = conn_id
        else:
            client, env = _jira_client_for_env(db, env_id, request.user["tenant_id"])
            if not client:
                return _render([], hint=(
                    "This environment has no Jira connection. "
                    "Attach one in Settings \u2192 Environments, or pick a different env."
                ))
            effective_conn_id = env.jira_connection_id
        try:
            results = client.search_issues(q, connection_id=effective_conn_id, limit=limit)
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
            return json_error("VALIDATION_ERROR", str(e), http=400)

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
            return json_error("NOT_FOUND", "Jira connection not found", http=404)
        return jsonify(client.list_projects()), 200
    except Exception as e:
        return json_error("PROVIDER_ERROR", f"Jira fetch failed: {e}", http=502)
    finally:
        db.close()


@execution_bp.route("/api/jira/<int:connection_id>/projects/<string:project_key>/boards", methods=["GET"])
@require_auth
def jira_boards(connection_id, project_key):
    db = next(get_db())
    try:
        client = _jira_client(db, connection_id, request.user["tenant_id"])
        if not client:
            return json_error("NOT_FOUND", "Jira connection not found", http=404)
        return jsonify(client.list_boards_for_project(project_key)), 200
    except Exception as e:
        return json_error("PROVIDER_ERROR", f"Jira fetch failed: {e}", http=502)
    finally:
        db.close()


@execution_bp.route("/api/jira/<int:connection_id>/boards/<int:board_id>/sprints", methods=["GET"])
@require_auth
def jira_sprints(connection_id, board_id):
    db = next(get_db())
    try:
        client = _jira_client(db, connection_id, request.user["tenant_id"])
        if not client:
            return json_error("NOT_FOUND", "Jira connection not found", http=404)
        states = request.args.get("state", "active,closed,future")
        return jsonify(client.list_sprints(board_id, states)), 200
    except Exception as e:
        return json_error("PROVIDER_ERROR", f"Jira fetch failed: {e}", http=502)
    finally:
        db.close()


# ---- /run page pickers: env-scoped sprint / tickets / release --------------
# These endpoints power the four-mode /run page introduced in Prompt 16.
# They're env-scoped (resolve Jira / release context from the
# environment the user has selected in the dropdown) so the client
# doesn't have to re-plumb project / board / connection choices.

@execution_bp.route("/api/jira/sprints", methods=["GET"])
@require_auth
def list_jira_sprints_for_env():
    """List sprints accessible via the environment's Jira connection.

    Query:
      environment_id (required)
      state (optional, default 'active,closed'):
        any comma-separated subset of {active, closed, future}

    Returns:
      {"sprints": [{id, name, state, startDate, endDate, board_id,
                    board_name, project_key, project_name}]}
      or {"error": ..., "sprints": []} on provider failure.
    """
    from primeqa.core.permissions import require_permission
    env_id = request.args.get("environment_id", type=int)
    state = request.args.get("state") or "active,closed"

    @require_permission("run_sprint")
    def _do():
        if not env_id:
            return json_error("VALIDATION_ERROR",
                              "environment_id required", http=400)
        db = next(get_db())
        try:
            client, env = _jira_client_for_env(
                db, env_id, request.user["tenant_id"])
            if env is None:
                return json_error("NOT_FOUND", "Environment not found",
                                  http=404)
            if client is None:
                return jsonify({"sprints": [],
                                "hint": "This environment has no Jira connection."}), 200
            try:
                sprints = client.list_sprints_for_tenant(states=state)
            except Exception as e:
                return jsonify({"sprints": [], "error": f"Jira fetch failed: {e}"}), 200
            return jsonify({"sprints": sprints}), 200
        finally:
            db.close()

    return _do()


def _decorate_with_readiness(db, tenant_id: int, tickets: list[dict],
                              key_field: str = "key") -> list[dict]:
    """Shared decorator: attach `readiness` to every ticket dict.

    Picker endpoints return tickets in different shapes (key vs.
    jira_key), so take the key field as a param. Batch-fetches
    readiness once for the whole list — no N+1.
    """
    from primeqa.runs.bulk import get_batch_readiness, READY_NEEDS_GEN
    keys = [t.get(key_field) for t in tickets if t.get(key_field)]
    readiness_map = get_batch_readiness(keys, tenant_id, db)
    for t in tickets:
        k = t.get(key_field)
        t["readiness"] = readiness_map.get(k, READY_NEEDS_GEN) if k else READY_NEEDS_GEN
    return tickets


@execution_bp.route("/api/jira/sprints/<int:sprint_id>/tickets", methods=["GET"])
@require_auth
def list_jira_sprint_tickets_for_env(sprint_id):
    """Tickets in a specific sprint (env's Jira). The sprint id must
    belong to the same Jira connection as the environment — we don't
    verify that cross-Jira (the agile endpoint errors if the id is
    invalid for this auth).

    Each ticket carries `readiness` ∈ APPROVED / DRAFT / GENERATING /
    NEEDS_GENERATION so the picker can badge it and the "Run" gate
    can decide whether to open the readiness modal.
    """
    from primeqa.core.permissions import require_permission
    env_id = request.args.get("environment_id", type=int)

    @require_permission("run_sprint")
    def _do():
        if not env_id:
            return json_error("VALIDATION_ERROR",
                              "environment_id required", http=400)
        db = next(get_db())
        try:
            client, env = _jira_client_for_env(
                db, env_id, request.user["tenant_id"])
            if env is None:
                return json_error("NOT_FOUND", "Environment not found",
                                  http=404)
            if client is None:
                return jsonify({"tickets": []}), 200
            try:
                issues = client.sprint_issues(sprint_id)
            except Exception as e:
                return jsonify({"tickets": [],
                                "error": f"Jira fetch failed: {e}"}), 200
            tickets = [{
                "key": i.get("key"),
                "summary": (i.get("summary") or i.get("fields", {}).get(
                    "summary") or "")[:240],
                "status": i.get("status") or (
                    i.get("fields", {}).get("status") or {}).get("name") or "",
            } for i in issues if i.get("key")]
            tickets = _decorate_with_readiness(
                db, request.user["tenant_id"], tickets, key_field="key")
            return jsonify({"tickets": tickets}), 200
        finally:
            db.close()

    return _do()


@execution_bp.route("/api/jira/tickets/recent", methods=["GET"])
@require_auth
def list_recent_tickets_for_user():
    """Last 10 tickets the current user has interacted with in PrimeQA
    (viewed a requirement detail page, ran a single ticket, selected in
    a picker). Scoped to (user, environment) so switching envs gives a
    clean slate. Requires `run_single_ticket` — the Tickets tab depends
    on it.
    """
    from primeqa.core.permissions import require_permission
    from primeqa.runs.recent_tickets import list_recent
    env_id = request.args.get("environment_id", type=int)
    limit = request.args.get("limit", default=10, type=int)

    @require_permission("run_single_ticket")
    def _do():
        if not env_id:
            return jsonify({"tickets": []}), 200
        db = next(get_db())
        try:
            rows = list_recent(db, request.user["id"], env_id, limit=limit)
            # Readiness decorator uses `jira_key` for this list shape
            rows = _decorate_with_readiness(
                db, request.user["tenant_id"], rows, key_field="jira_key")
            return jsonify({"tickets": rows}), 200
        finally:
            db.close()

    return _do()


@execution_bp.route("/api/jira/tickets/search", methods=["GET"])
@require_auth
def search_jira_tickets_with_filters():
    """Filtered Jira search for the /run Tickets picker.

    Query:
      environment_id (required)
      q              (required; min 1 char)
      filter         (optional, CSV): any subset of
                     {mine, current_sprint, open, recent}

    Results are mapped onto the same shape as /api/jira/search but
    filtered by the additional JQL clauses.
    """
    from primeqa.core.permissions import require_permission
    env_id = request.args.get("environment_id", type=int)
    q = (request.args.get("q") or "").strip()
    filters_raw = (request.args.get("filter") or "")
    filters = {f.strip() for f in filters_raw.split(",") if f.strip()}
    limit = request.args.get("limit", default=25, type=int)

    @require_permission("run_single_ticket")
    def _do():
        if not env_id:
            return json_error("VALIDATION_ERROR",
                              "environment_id required", http=400)
        if len(q) < 1:
            return jsonify({"tickets": []}), 200
        db = next(get_db())
        try:
            client, env = _jira_client_for_env(
                db, env_id, request.user["tenant_id"])
            if client is None:
                return jsonify({"tickets": [],
                                "hint": "This environment has no Jira connection."}), 200

            # Build JQL via the shared helper — same branching as
            # /api/jira/search so both surfaces behave identically.
            # We only append the Tickets-tab-specific filter clauses
            # (mine / current_sprint / open / recent) on top of the
            # core match, then apply any client-side filter the
            # helper returned (letters+dash+digits narrowing).
            from primeqa.runs.wizard import build_search_core_jql
            core, client_filter = build_search_core_jql(q)

            clauses = [core]
            if "mine" in filters:
                clauses.append("assignee = currentUser()")
            if "current_sprint" in filters:
                clauses.append("sprint in openSprints()")
            if "open" in filters:
                clauses.append("resolution = Unresolved")
            if "recent" in filters:
                clauses.append("updated >= -30d")

            jql = " AND ".join(clauses) + " ORDER BY updated DESC"

            try:
                from urllib.parse import urlencode
                params = {"jql": jql, "maxResults": max(1, min(limit, 50)),
                          "fields": "summary,status,issuetype,assignee"}
                url = f"{client.base_url}/rest/api/3/search/jql?{urlencode(params)}"
                import requests as _r
                resp = _r.get(url, headers=client.headers, timeout=10)
                resp.raise_for_status()
                body = resp.json()
            except Exception as e:
                return jsonify({"tickets": [],
                                "error": f"Jira search failed: {e}"}), 200

            tickets = []
            for i in body.get("issues", []):
                f = i.get("fields") or {}
                assignee = f.get("assignee") or {}
                tickets.append({
                    "key": i.get("key"),
                    "summary": (f.get("summary") or "")[:240],
                    "status": ((f.get("status") or {}).get("name") or ""),
                    "issue_type": ((f.get("issuetype") or {}).get("name") or ""),
                    "assignee": assignee.get("displayName")
                                or assignee.get("emailAddress") or "",
                    "assignee_email": assignee.get("emailAddress") or "",
                })
            # Client-side narrowing runs BEFORE readiness decoration so
            # we don't waste a readiness batch-fetch on rows that are
            # about to be filtered out.
            if client_filter is not None:
                tickets = client_filter(tickets)
            tickets = _decorate_with_readiness(
                db, request.user["tenant_id"], tickets, key_field="key")
            return jsonify({"tickets": tickets}), 200
        finally:
            db.close()

    return _do()


@execution_bp.route("/api/releases", methods=["GET"])
@require_auth
def list_releases_for_run():
    """Releases the current tenant owns. Surfaced on the /run Release
    tab. Environment filter is accepted but today we don't scope
    releases by environment (releases are tenant-global); we carry it
    for forward compatibility.
    """
    from primeqa.core.permissions import require_permission
    env_id = request.args.get("environment_id", type=int)  # noqa: F841

    @require_permission("run_sprint", "run_suite", require_all=False)
    def _do():
        from primeqa.release.models import (
            Release, ReleaseRequirement, ReleaseTestPlanItem,
        )
        from sqlalchemy import func as _f
        db = next(get_db())
        try:
            rows = (db.query(Release)
                    .filter(Release.tenant_id == request.user["tenant_id"])
                    .filter(Release.status != "cancelled")
                    .order_by(Release.target_date.asc().nullslast(),
                              Release.created_at.desc())
                    .limit(50).all())
            ids = [r.id for r in rows]
            ticket_counts = {}
            tc_counts = {}
            if ids:
                rq = (db.query(ReleaseRequirement.release_id,
                               _f.count().label("n"))
                      .filter(ReleaseRequirement.release_id.in_(ids))
                      .group_by(ReleaseRequirement.release_id).all())
                ticket_counts = {rid: n for rid, n in rq}
                tq = (db.query(ReleaseTestPlanItem.release_id,
                               _f.count().label("n"))
                      .filter(ReleaseTestPlanItem.release_id.in_(ids))
                      .group_by(ReleaseTestPlanItem.release_id).all())
                tc_counts = {rid: n for rid, n in tq}
            out = [{
                "id": r.id, "name": r.name,
                "version_tag": r.version_tag,
                "status": r.status,
                "target_date": r.target_date.isoformat() if r.target_date else None,
                "ticket_count": int(ticket_counts.get(r.id, 0)),
                "test_case_count": int(tc_counts.get(r.id, 0)),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            } for r in rows]
            return jsonify({"releases": out}), 200
        finally:
            db.close()

    return _do()


@execution_bp.route("/api/releases/<int:release_id>/contents", methods=["GET"])
@require_auth
def release_contents_for_run(release_id):
    """Tickets (Jira keys) + Test Cases attached to a release. Used by
    the /run Release picker to populate the per-item checkbox list.
    """
    from primeqa.core.permissions import require_permission

    @require_permission("run_sprint", "run_suite", require_all=False)
    def _do():
        from primeqa.release.models import (
            Release, ReleaseRequirement, ReleaseTestPlanItem,
        )
        from primeqa.test_management.models import Requirement, TestCase
        db = next(get_db())
        try:
            rel = (db.query(Release)
                   .filter(Release.id == release_id,
                           Release.tenant_id == request.user["tenant_id"])
                   .first())
            if rel is None:
                return json_error("NOT_FOUND", "Release not found", http=404)

            req_rows = (db.query(Requirement)
                        .join(ReleaseRequirement,
                              ReleaseRequirement.requirement_id == Requirement.id)
                        .filter(ReleaseRequirement.release_id == release_id,
                                Requirement.deleted_at.is_(None))
                        .all())
            tickets = [{
                "requirement_id": r.id,
                "jira_key": r.jira_key,
                "summary": (r.jira_summary or "")[:240],
            } for r in req_rows if r.jira_key]
            # Release mode badges are informational only — the readiness
            # modal does NOT fire here (release test plans are curated).
            tickets = _decorate_with_readiness(
                db, request.user["tenant_id"], tickets, key_field="jira_key")

            tc_rows = (db.query(TestCase)
                       .join(ReleaseTestPlanItem,
                             ReleaseTestPlanItem.test_case_id == TestCase.id)
                       .filter(ReleaseTestPlanItem.release_id == release_id,
                               TestCase.deleted_at.is_(None))
                       .all())
            test_cases = [{
                "id": t.id, "title": t.title[:240],
                "status": t.status,
            } for t in tc_rows]

            return jsonify({
                "release": {
                    "id": rel.id, "name": rel.name,
                    "version_tag": rel.version_tag,
                    "status": rel.status,
                },
                "tickets": tickets,
                "test_cases": test_cases,
            }), 200
        finally:
            db.close()

    return _do()


@execution_bp.route("/api/suites/<int:suite_id>/overview", methods=["GET"])
@require_auth
def suite_overview_for_run(suite_id):
    """Suite summary for the /run Suite picker: test-case count, gate
    threshold, last run, and the current set of TCs with checkboxes.
    """
    from primeqa.core.permissions import require_permission

    @require_permission("run_suite")
    def _do():
        from primeqa.test_management.models import (
            SuiteTestCase, TestCase, TestSuite,
        )
        from primeqa.execution.models import PipelineRun
        db = next(get_db())
        try:
            s = (db.query(TestSuite)
                 .filter_by(id=suite_id, tenant_id=request.user["tenant_id"])
                 .first())
            if s is None or s.deleted_at is not None:
                return json_error("NOT_FOUND", "Suite not found", http=404)
            tcs_q = (db.query(TestCase, SuiteTestCase.position)
                     .join(SuiteTestCase,
                           SuiteTestCase.test_case_id == TestCase.id)
                     .filter(SuiteTestCase.suite_id == suite_id,
                             TestCase.deleted_at.is_(None))
                     .order_by(SuiteTestCase.position.asc()))
            tcs = [{
                "id": t.id, "title": t.title[:240],
                "status": t.status, "position": pos or 0,
            } for t, pos in tcs_q.all()]
            # Last run that referenced this suite (source_refs.suite_id)
            last_run = (db.query(PipelineRun)
                        .filter(PipelineRun.tenant_id == request.user["tenant_id"],
                                PipelineRun.source_type == "suite")
                        .order_by(PipelineRun.queued_at.desc())
                        .limit(25).all())
            last = None
            for r in last_run:
                refs = r.source_refs or {}
                if refs.get("suite_id") == suite_id:
                    last = {"id": r.id, "status": r.status,
                            "queued_at": r.queued_at.isoformat() if r.queued_at else None}
                    break
            return jsonify({
                "suite": {
                    "id": s.id, "name": s.name,
                    "suite_type": s.suite_type,
                    "quality_gate_threshold": s.quality_gate_threshold,
                    "test_case_count": len(tcs),
                },
                "test_cases": tcs,
                "last_run": last,
            }), 200
        finally:
            db.close()

    return _do()


@execution_bp.route("/api/runs/<int:run_id>/events", methods=["GET"])
@require_auth
def stream_run_events(run_id):
    """Server-Sent Events endpoint for live run timeline updates.

    Three delivery channels are interleaved:
      1. In-process EventBus (sub-second when web+worker share a process).
      2. DB tail of `run_events` (cross-service on Railway \u2014 worker
         writes to DB, web polls it every ~1s).
      3. DB snapshot of status/counts every 5s.

    On connect, the last ~200 events are backfilled so a page refresh
    keeps the log panel populated.
    """
    from flask import Response
    from primeqa.runs.streams import stream_run_events as sse_gen
    from primeqa.execution.models import RunEvent
    tenant_id = request.user["tenant_id"]
    db = next(get_db())
    try:
        # Authorization: confirm the user can see this run (tenant-scoped)
        run = PipelineRunRepository(db).get_run(run_id, tenant_id)
        if not run:
            return json_error("NOT_FOUND", "Run not found", http=404)
    finally:
        db.close()

    def snapshot():
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

    def _event_to_dict(ev):
        return {
            "id": ev.id, "kind": ev.kind, "level": ev.level,
            "message": ev.message, "context": ev.context or {},
            "ts": ev.ts.isoformat() if ev.ts else None,
        }

    def initial_events():
        """Last ~200 events on the run so refresh repopulates the log."""
        ev_db = next(get_db())
        try:
            # Keep the tail (most recent 200) in chronological order
            q = (ev_db.query(RunEvent)
                 .filter(RunEvent.run_id == run_id,
                         RunEvent.tenant_id == tenant_id)
                 .order_by(RunEvent.id.desc())
                 .limit(200))
            rows = list(reversed(q.all()))
            return [_event_to_dict(e) for e in rows]
        finally:
            ev_db.close()

    def tail_events(since_id):
        """Events newer than `since_id`, chronological."""
        ev_db = next(get_db())
        try:
            q = (ev_db.query(RunEvent)
                 .filter(RunEvent.run_id == run_id,
                         RunEvent.tenant_id == tenant_id,
                         RunEvent.id > (since_id or 0))
                 .order_by(RunEvent.id.asc())
                 .limit(200))
            return [_event_to_dict(e) for e in q.all()]
        finally:
            ev_db.close()

    resp = Response(
        sse_gen(run_id, snapshot, tail_events,
                initial_events_fn=initial_events),
        mimetype="text/event-stream",
    )
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


@execution_bp.route("/api/runs/<int:run_id>/events/download", methods=["GET"])
@require_auth
def download_run_events(run_id):
    """Export ALL events for a run as JSON (or text).

    Purpose: attach to a bug report or ticket; review after the run
    completed. No size cap on download \u2014 we cap in the UI panel,
    not on export.
    """
    from flask import Response
    from primeqa.execution.models import RunEvent
    import json

    tenant_id = request.user["tenant_id"]
    fmt = (request.args.get("format") or "json").lower()

    db = next(get_db())
    try:
        run = PipelineRunRepository(db).get_run(run_id, tenant_id)
        if not run:
            return json_error("NOT_FOUND", "Run not found", http=404)

        rows = (db.query(RunEvent)
                .filter(RunEvent.run_id == run_id,
                        RunEvent.tenant_id == tenant_id)
                .order_by(RunEvent.id.asc())
                .all())

        events = [{
            "id": e.id, "ts": e.ts.isoformat() if e.ts else None,
            "kind": e.kind, "level": e.level,
            "message": e.message, "context": e.context or {},
        } for e in rows]
    finally:
        db.close()

    fname = f"run-{run_id}-events"

    if fmt == "txt":
        lines = [f"[{e['ts']}] {e['level'].upper():5} {e['kind']:16} {e['message']}"
                 for e in events]
        body = "\n".join(lines) + "\n"
        resp = Response(body, mimetype="text/plain; charset=utf-8")
        resp.headers["Content-Disposition"] = f'attachment; filename="{fname}.txt"'
        return resp

    # default JSON
    body = json.dumps({
        "run_id": run_id, "tenant_id": tenant_id,
        "exported_at": None,  # client-side stamps its own download time
        "count": len(events),
        "events": events,
    }, indent=2, default=str)
    resp = Response(body, mimetype="application/json")
    resp.headers["Content-Disposition"] = f'attachment; filename="{fname}.json"'
    return resp


@execution_bp.route("/api/runs/<int:run_id>", methods=["GET"])
@require_auth
def get_run(run_id):
    svc, db = _get_service()
    try:
        result = svc.get_run_status(run_id, request.user["tenant_id"])
        if not result:
            return json_error("NOT_FOUND", "Run not found", http=404)
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
        return json_error("VALIDATION_ERROR", str(e), http=400)
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
            return json_error("NOT_FOUND", "Environment not found", http=404)
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
            return json_error("NOT_FOUND", "Run not found", http=404)
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
            return json_error("NOT_FOUND", "Environment not found", http=404)
        creds = env_repo.get_credentials_decrypted(env_id)
        if not creds or not creds.get("access_token"):
            return json_error("VALIDATION_ERROR", "No credentials for this environment", http=400)
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
            return json_error("VALIDATION_ERROR", f"{f} is required", http=400)
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
            return json_error("VALIDATION_ERROR", f"{f} is required", http=400)
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
            return json_error("NOT_FOUND", "Factory not found", http=404)
        svc = DataEngineService(db)
        samples = [svc.generate_value(f.factory_type, f.config) for _ in range(5)]
        return jsonify({"samples": samples}), 200
    finally:
        db.close()
