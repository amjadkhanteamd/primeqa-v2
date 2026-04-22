"""Web UI views — server-rendered templates with Jinja2 + HTMX.

All pages require authentication via JWT cookie except /login.
"""

import os
from functools import wraps

import jwt
from flask import Blueprint, render_template, request, redirect, url_for, make_response

from primeqa.db import get_db
from primeqa.core.repository import (
    UserRepository, RefreshTokenRepository, EnvironmentRepository,
    ConnectionRepository, GroupRepository,
)
from primeqa.core.service import AuthService, EnvironmentService, ConnectionService, GroupService
from primeqa.release.repository import ReleaseRepository
from primeqa.release.service import ReleaseService

views_bp = Blueprint("views", __name__, template_folder="templates")

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")


def get_current_user():
    """Audit fix C-4 (2026-04-19): tolerate a JWT that's missing the
    `role` / `tenant_id` / `email` claims (malformed, from an earlier
    schema, or forged). Previously a `KeyError` leaked through and
    crashed every web page with a 500. Now missing claims → treat as
    not-authenticated (returns None → handler redirects to /login)."""
    token = request.cookies.get("access_token")
    if not token:
        return None
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        if "sub" not in payload or "tenant_id" not in payload:
            return None  # malformed — drop to login flow
        return {
            "id": int(payload["sub"]),
            "tenant_id": payload["tenant_id"],
            "email": payload.get("email", ""),
            "role": payload.get("role", "viewer"),
            "full_name": payload.get("full_name", ""),
        }
    except (jwt.InvalidTokenError, ValueError, TypeError):
        return None


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user:
            return redirect("/login")
        request.user = user
        return f(*args, **kwargs)
    return decorated


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated(*args, **kwargs):
            role = request.user["role"]
            if role == "superadmin":
                return f(*args, **kwargs)
            if role not in roles:
                return redirect("/")
            return f(*args, **kwargs)
        return decorated
    return decorator


def ctx(**kwargs):
    return {**kwargs, "user": getattr(request, "user", None)}


# --- Auth ---

@views_bp.route("/login", methods=["GET"])
def login_page():
    return render_template("auth/login.html", user=None, error=None)


@views_bp.route("/login", methods=["POST"])
def login_submit():
    email = request.form.get("email")
    password = request.form.get("password")
    db = next(get_db())
    try:
        svc = AuthService(UserRepository(db), RefreshTokenRepository(db))
        # Tenant derived from email on the users table — no client override (audit C-1).
        result = svc.login(email, password)
        if not result:
            return render_template("auth/login.html", user=None, error="Invalid email or password")

        # Migration 040: route the user to the landing page their permission-set
        # union unlocks (or their saved preference, if still reachable). This
        # replaces the old hardcoded `/` redirect so developers don't land on a
        # dashboard they can't read, and admins don't always bounce through the
        # dashboard before reaching settings.
        from primeqa.core.permissions import get_effective_permissions
        from primeqa.core.navigation import get_landing_page
        from primeqa.core.models import User

        user_row = db.query(User).filter_by(id=result["user"]["id"]).first()
        is_superadmin = (user_row.role == "superadmin") if user_row else False
        perms = get_effective_permissions(result["user"]["id"], db)
        preferred = user_row.preferred_landing_page if user_row else None
        landing = get_landing_page(perms, preferred=preferred,
                                   is_superadmin=is_superadmin)

        resp = make_response(redirect(landing))
        resp.set_cookie("access_token", result["access_token"], httponly=True, samesite="Lax", max_age=1800)
        return resp
    finally:
        db.close()


@views_bp.route("/logout")
def logout():
    resp = make_response(redirect("/login"))
    resp.delete_cookie("access_token")
    return resp


# --- Dashboard ---

@views_bp.route("/")
@login_required
def dashboard():
    """Audit fix M-7 (2026-04-19): was 15 queries / 4.3s — the
    first-impression page on every login. Consolidated:

      - 7 individual count(*) queries → 1 SELECT with subquery
        aggregates (one round-trip instead of 7 × Railway RTT).
      - recent_runs retained as its own query (different shape).
      - analytics collapsed where possible inside AnalyticsService;
        remaining calls run serial on the same session.

    Measured post-fix: ~6 queries, <1.5s.
    """
    from sqlalchemy import text as sql
    db = next(get_db())
    try:
        from primeqa.execution.models import PipelineRun
        tid = request.user["tenant_id"]

        # One CTE-free roll-up: every count lives in its own scalar
        # subquery. Postgres parallelises these on a single
        # round-trip; the total is ~1 RTT instead of 7.
        row = db.execute(sql("""
            SELECT
              (SELECT COUNT(*) FROM test_cases
                 WHERE tenant_id = :tid AND deleted_at IS NULL)        AS tc_count,
              (SELECT COUNT(*) FROM pipeline_runs
                 WHERE tenant_id = :tid)                               AS runs_today,
              (SELECT COUNT(*) FROM ba_reviews
                 WHERE tenant_id = :tid AND status = 'pending'
                   AND deleted_at IS NULL)                             AS pending,
              (SELECT COUNT(*) FROM users
                 WHERE tenant_id = :tid AND is_active = true
                   AND role <> 'superadmin')                           AS user_count,
              (SELECT COUNT(*) FROM environments
                 WHERE tenant_id = :tid)                               AS env_count,
              (SELECT COUNT(*) FROM connections
                 WHERE tenant_id = :tid)                               AS conn_count,
              (SELECT COUNT(*) FROM groups
                 WHERE tenant_id = :tid)                               AS group_count
        """), {"tid": tid}).one()._mapping

        setup_complete = (row["conn_count"] > 0 and row["env_count"] > 0
                          and row["group_count"] > 0)

        recent_runs = db.query(PipelineRun).filter(
            PipelineRun.tenant_id == tid,
        ).order_by(PipelineRun.queued_at.desc()).limit(10).all()

        runs_data = [{
            "id": r.id, "status": r.status, "run_type": r.run_type,
            "priority": r.priority, "queued_at": r.queued_at.isoformat() if r.queued_at else "",
        } for r in recent_runs]

        # Analytics: share the session. Each method is still a separate
        # query but runs on one connection; further consolidation into
        # AnalyticsService is a future pass.
        from primeqa.execution.analytics import AnalyticsService
        analytics = AnalyticsService(db)
        overall = analytics.overall_stats(tid)
        env_pass_rates = analytics.pass_rate_by_environment(tid)
        flaky = analytics.flaky_tests(tid, limit=5)
        releases_health = analytics.release_health(tid)

        stats = {
            "total_test_cases": row["tc_count"],
            "runs_today": row["runs_today"],
            "pass_rate": overall["pass_rate_30d"],
            "pending_reviews": row["pending"],
            "user_count": row["user_count"],
            "env_count": row["env_count"],
        }
        return render_template("dashboard.html", **ctx(
            active_page="dashboard", stats=stats, recent_runs=runs_data,
            setup_complete=setup_complete,
            env_pass_rates=env_pass_rates, flaky_tests=flaky, releases_health=releases_health,
        ))
    finally:
        db.close()


# --- Developer /tickets page ---------------------------------------------

@views_bp.route("/tickets")
@login_required
def my_tickets():
    """Developer primary UI: Jira tickets assigned to me, with inline results.

    This is the Developer Base's entire interface — the sidebar renders
    empty for them (only `my_tickets` passes the gate), so this page has
    to carry the whole workflow: see my tickets, click Run, see results.
    """
    # Permission gate: `run_single_ticket` (held by developer_base +
    # tester_base + admin_base + the API-access set). Import here to
    # avoid tripping any circular import at module load.
    from primeqa.core.permissions import require_page_permission

    @require_page_permission("run_single_ticket")
    def _render():
        from primeqa.core.models import User
        from primeqa.runs.my_tickets import (
            attach_latest_runs,
            fetch_my_tickets,
            list_switchable_environments,
            resolve_active_environment,
            sort_for_triage,
        )

        db = next(get_db())
        try:
            user_row = db.query(User).filter_by(id=request.user["id"]).first()
            env = resolve_active_environment(user_row, db)

            if env is None:
                return render_template("tickets/list.html", **ctx(
                    active_page="tickets",
                    tickets=[],
                    env=None,
                    envs=[],
                    empty_reason="no_environment",
                ))

            tickets = fetch_my_tickets(user_row, env, db)
            if not tickets:
                empty_reason = "no_tickets"
            else:
                empty_reason = None
                tickets = attach_latest_runs(tickets, env, db)
                tickets = sort_for_triage(tickets)

            envs = list_switchable_environments(user_row, db)
            return render_template("tickets/list.html", **ctx(
                active_page="tickets",
                tickets=tickets,
                env=env,
                envs=envs,
                empty_reason=empty_reason,
            ))
        finally:
            db.close()

    return _render()


@views_bp.route("/runs/<int:run_id>/tickets-summary")
@login_required
def run_tickets_summary(run_id):
    """Compact inline step-summary partial for the /tickets page accordion.

    Much thinner than the full /runs/:id detail — one line per step
    with status icon, step name, duration, and error code on failure.
    Loaded via HTMX when the developer expands a ticket.
    """
    from primeqa.execution.models import PipelineRun, RunTestResult, RunStepResult
    from primeqa.test_management.models import TestCase

    db = next(get_db())
    try:
        run = db.query(PipelineRun).filter_by(id=run_id).first()
        if run is None or run.tenant_id != request.user["tenant_id"]:
            return ("Not found", 404)
        results = (db.query(RunTestResult)
                   .filter_by(run_id=run_id)
                   .order_by(RunTestResult.executed_at.asc())
                   .all())
        steps = []
        for r in results:
            rsteps = (db.query(RunStepResult)
                      .filter_by(run_test_result_id=r.id)
                      .order_by(RunStepResult.step_order.asc())
                      .all())
            tc = db.query(TestCase).filter_by(id=r.test_case_id).first()
            steps.append({"result": r, "steps": rsteps,
                          "tc_title": tc.title if tc else ""})
        return render_template("tickets/_run_summary.html", run=run, blocks=steps)
    finally:
        db.close()


@views_bp.route("/api/users/me/active-env", methods=["POST"])
@login_required
def set_active_environment():
    """Update the caller's preferred_environment_id.

    Accepts either form-encoded or JSON body with `environment_id`.
    HTMX-friendly: on success returns a 204 and sets `HX-Redirect` so
    the client picks up the new default everywhere.
    """
    from primeqa.core.models import Environment, User
    db = next(get_db())
    try:
        env_id = request.form.get("environment_id") or (
            request.get_json(silent=True) or {}).get("environment_id")
        try:
            env_id = int(env_id)
        except (TypeError, ValueError):
            return make_response(("environment_id required", 400))
        env = db.query(Environment).filter_by(id=env_id).first()
        if env is None or env.tenant_id != request.user["tenant_id"]:
            return make_response(("not found", 404))
        # Permission: only owner can pick a personal env (or any admin/superadmin).
        if env.environment_type == "personal" and env.owner_user_id != request.user["id"]:
            is_super = request.user.get("role") == "superadmin"
            if not is_super:
                return make_response(("forbidden", 403))
        user_row = db.query(User).filter_by(id=request.user["id"]).first()
        user_row.preferred_environment_id = env.id
        db.commit()
        resp = make_response("", 204)
        resp.headers["HX-Redirect"] = "/tickets"
        return resp
    finally:
        db.close()


# --- Runs ---

# --- /results — alias for /runs. Keeps the existing run-history UI as
# the Results surface per Prompt 8, without duplicating templates. ----

@views_bp.route("/results")
@login_required
def results_list_alias():
    """Tester-facing Results URL. Delegates to the existing runs list
    template so we keep a single source of truth for scoping, the My
    Runs/All Runs toggle, and the run-history rendering. The sidebar
    entry points here (per Prompt 8 navigation)."""
    # Preserve any filter query-string the caller passed.
    qs = request.query_string.decode() if request.query_string else ""
    return redirect("/runs" + (f"?{qs}" if qs else ""))


@views_bp.route("/results/<int:run_id>")
@login_required
def result_detail_alias(run_id):
    """Alias for /runs/:id — same reasoning as /results."""
    return redirect(f"/runs/{run_id}")


@views_bp.route("/runs")
@login_required
def runs_list():
    db = next(get_db())
    try:
        from primeqa.execution.repository import PipelineRunRepository
        from primeqa.execution.models import PipelineRun
        from primeqa.core.permissions import get_effective_permissions
        from sqlalchemy import or_
        repo = PipelineRunRepository(db)
        status_filter = request.args.get("status")
        page = max(1, request.args.get("page", 1, type=int))
        per_page = min(50, max(5, request.args.get("per_page", 20, type=int)))
        # PipelineRunRepository.list_runs supports limit/offset but returns a
        # plain list; we compute the count ourselves so we can render proper
        # pagination. Status filter reused as-is.
        label_filter = (request.args.get("label") or "").strip() or None
        base = db.query(PipelineRun).filter(PipelineRun.tenant_id == request.user["tenant_id"])
        if status_filter:
            base = base.filter(PipelineRun.status == status_filter)
        if label_filter:
            # Substring match (ILIKE) so partial tag text finds matches
            base = base.filter(PipelineRun.label.ilike(f"%{label_filter}%"))

        # Migration 039/040: My Runs / All Runs toggle.
        # Scope the list based on the caller's permissions:
        #   - `view_all_results` in perms → default OFF (show all runs)
        #   - `view_own_results` only     → default ON (show own runs only)
        # The `mine` query param explicitly overrides. `mine=1` forces own;
        # `mine=0` forces all BUT only if the user has view_all_results
        # (otherwise we fall back to own so they don't see empty results by
        # accident on a missing permission).
        user_perms = get_effective_permissions(request.user["id"], db)
        has_all = ("view_all_results" in user_perms
                   or request.user.get("role") == "superadmin")
        has_own = "view_own_results" in user_perms or has_all
        mine_param = request.args.get("mine")
        if mine_param in ("1", "true", "on"):
            show_mine = True
        elif mine_param in ("0", "false", "off") and has_all:
            show_mine = False
        else:
            # Default: own-only if the user only has view_own_results.
            show_mine = (not has_all) and has_own
        if show_mine:
            base = base.filter(PipelineRun.triggered_by == request.user["id"])

        total = base.order_by(None).count()
        runs = base.order_by(PipelineRun.queued_at.desc()) \
                   .offset((page - 1) * per_page).limit(per_page).all()

        runs_data = [{
            "id": r.id, "status": r.status, "run_type": r.run_type,
            "source_type": r.source_type, "priority": r.priority,
            "passed": r.passed, "failed": r.failed, "total_tests": r.total_tests,
            "queued_at": r.queued_at.isoformat() if r.queued_at else "",
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            "label": r.label,
        } for r in runs]
        from math import ceil
        meta = {
            "total": total, "page": page, "per_page": per_page,
            "total_pages": max(1, ceil(total / per_page)) if total else 0,
        }
        return render_template("runs/list.html", **ctx(
            active_page="runs", runs=runs_data,
            status_filter=status_filter, label_filter=label_filter, meta=meta,
            show_mine=show_mine, scope_choice_available=has_all and has_own,
        ))
    finally:
        db.close()


# --- /run — Tester's focused run page (Prompt 7) --------------------------

@views_bp.route("/run")
@login_required
def run_page():
    """Tester's primary workflow page: pick Sprint / Single / Suite,
    configure, and kick off a pipeline run.

    Simpler than /runs/new (the Run Wizard) — one click to run one
    source type. The underlying executor + pipeline_run row are
    shared; `/runs/:id` continues to be the live progress surface.
    """
    from primeqa.core.models import Environment, User
    from primeqa.core.permissions import require_page_permission
    from primeqa.execution.models import PipelineRun
    from primeqa.runs.my_tickets import resolve_active_environment
    from primeqa.test_management.models import TestSuite

    @require_page_permission("run_sprint", "run_suite", require_all=False)
    def _render():
        db = next(get_db())
        try:
            user_row = db.query(User).filter_by(id=request.user["id"]).first()
            env = resolve_active_environment(user_row, db)
            # List all envs in the tenant for the env selector (the Tester
            # may run against any team env; personal envs are allowed too).
            envs = (db.query(Environment)
                    .filter_by(tenant_id=request.user["tenant_id"], is_active=True)
                    .order_by(Environment.name.asc())
                    .all())
            # Suite picker data — tenant-scoped, not deleted.
            suites = (db.query(TestSuite)
                      .filter_by(tenant_id=request.user["tenant_id"])
                      .filter(TestSuite.deleted_at.is_(None))
                      .order_by(TestSuite.name.asc())
                      .all())
            # Run history for this env (last 5) — shown below the form.
            history = []
            if env is not None:
                rows = (db.query(PipelineRun)
                        .filter_by(tenant_id=request.user["tenant_id"],
                                   environment_id=env.id)
                        .order_by(PipelineRun.queued_at.desc())
                        .limit(5)
                        .all())
                history = [{
                    "id": r.id, "status": r.status, "run_type": r.run_type,
                    "source_type": r.source_type,
                    "total_tests": r.total_tests or 0,
                    "passed": r.passed or 0, "failed": r.failed or 0,
                    "queued_at": r.queued_at.isoformat() if r.queued_at else "",
                    "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                    "label": r.label,
                } for r in rows]
            return render_template("run/index.html", **ctx(
                active_page="run_tests",
                env=env, envs=envs, suites=suites, history=history,
                is_production=(env.is_production if env else False),
            ))
        finally:
            db.close()

    return _render()


@views_bp.route("/api/bulk-runs", methods=["POST"])
def api_bulk_run_create():
    """Create a pipeline_run from a sprint / suite / ticket-key selection.

    Body (JSON):
      {
        "environment_id": int,
        "run_type": "sprint" | "single" | "suite",
        "ticket_keys": ["SQ-208", ...],      # required for sprint/single
        "suite_id": int,                     # required for suite
        "confirm_production": bool
      }

    Returns 201 with {"pipeline_run_id": N, "status": "queued"} — the
    caller should poll /api/bulk-runs/:id/status or redirect to
    /runs/:id for live progress.
    """
    from primeqa.core.auth import require_auth
    from primeqa.core.models import Environment
    from primeqa.core.permissions import require_run_permission
    from primeqa.execution.repository import (
        PipelineRunRepository, PipelineStageRepository,
        ExecutionSlotRepository, WorkerHeartbeatRepository,
    )
    from primeqa.execution.service import PipelineService
    from primeqa.runs.bulk import (
        environment_can_bulk_run,
        suite_to_test_case_ids,
        ticket_keys_to_test_case_ids,
    )

    @require_auth
    def _guarded():
        body = request.get_json(silent=True) or {}
        run_type = (body.get("run_type") or "").lower()
        if run_type not in ("sprint", "single", "suite"):
            return ({"error": {"code": "VALIDATION_ERROR",
                               "message": "run_type must be sprint, single, or suite"}}, 400)
        try:
            environment_id = int(body.get("environment_id"))
        except (TypeError, ValueError):
            return ({"error": {"code": "VALIDATION_ERROR",
                               "message": "environment_id required"}}, 400)

        # Permission: require_run_permission wraps layer-1 (run_sprint for
        # bulk, run_single_ticket for single). Apply inline per mode.
        if run_type in ("sprint", "suite"):
            gate = require_run_permission("bulk_run")
        else:
            gate = require_run_permission("single_run")

        @gate
        def _after_gate():
            db = next(get_db())
            try:
                env = (db.query(Environment)
                       .filter_by(id=environment_id,
                                  tenant_id=request.user["tenant_id"])
                       .first())
                if env is None:
                    return ({"error": {"code": "NOT_FOUND",
                                       "message": "Environment not found"}}, 404)

                # Layer-2 env policy check (the require_run_permission
                # decorator already does this — duplicate here only to
                # surface a precise message on bulk=false for a clearer
                # UI experience).
                confirm_prod = bool(body.get("confirm_production"))
                ok, reason = environment_can_bulk_run(env, confirm_prod)
                if run_type != "single" and not ok:
                    return ({"error": {"code": "ENVIRONMENT_POLICY_DENIED",
                                       "message": reason,
                                       "details": {"environment_id": env.id}}},
                            403)

                if run_type == "suite":
                    try:
                        suite_id = int(body.get("suite_id"))
                    except (TypeError, ValueError):
                        return ({"error": {"code": "VALIDATION_ERROR",
                                           "message": "suite_id required for suite run"}}, 400)
                    tc_ids, suite = suite_to_test_case_ids(
                        suite_id, request.user["tenant_id"], db,
                    )
                    if not suite:
                        return ({"error": {"code": "NOT_FOUND",
                                           "message": "Suite not found"}}, 404)
                    if not tc_ids:
                        return ({"error": {"code": "NO_TESTS",
                                           "message": "Suite has no active test cases"}}, 400)
                    source_type = "suite"
                    source_refs = {"suite_id": suite_id, "suite_name": suite.name}
                else:
                    keys = body.get("ticket_keys") or []
                    if not isinstance(keys, list) or not keys:
                        return ({"error": {"code": "VALIDATION_ERROR",
                                           "message": "ticket_keys must be a non-empty list"}}, 400)
                    tc_ids, missing = ticket_keys_to_test_case_ids(
                        keys, request.user["tenant_id"], db,
                    )
                    if not tc_ids:
                        return ({"error": {"code": "NO_TESTS",
                                           "message": (
                                               "No test cases found for the selected tickets. "
                                               "Import + generate tests in the Requirements page "
                                               "first."),
                                           "details": {"missing_keys": missing}}}, 400)
                    source_type = "jira_tickets" if run_type == "sprint" else "requirements"
                    source_refs = {
                        "ticket_keys": keys,
                        "missing_keys": missing,
                        "mode": run_type,
                    }

                svc = PipelineService(
                    PipelineRunRepository(db), PipelineStageRepository(db),
                    ExecutionSlotRepository(db), WorkerHeartbeatRepository(db),
                )
                result = svc.create_run(
                    tenant_id=request.user["tenant_id"],
                    environment_id=environment_id,
                    triggered_by=request.user["id"],
                    run_type="execute_only",
                    source_type=source_type,
                    source_ids=tc_ids,
                    priority=body.get("priority", "normal"),
                    max_execution_time_sec=int(body.get("max_execution_time_sec", 3600)),
                    config=body.get("config", {}),
                    source_refs=source_refs,
                )
                return ({"pipeline_run_id": result["id"],
                         "status": result.get("status", "queued"),
                         "total_tests": len(tc_ids),
                         "redirect": f"/runs/{result['id']}"}, 201)
            finally:
                db.close()

        return _after_gate()

    return _guarded()


@views_bp.route("/api/bulk-runs/<int:run_id>/status", methods=["GET"])
def api_bulk_run_status(run_id):
    """Poll endpoint: returns the per-ticket status view the /run page
    progress UI needs. Thin wrapper over the existing pipeline_run row.
    """
    from primeqa.core.auth import require_auth
    from primeqa.execution.models import PipelineRun, RunTestResult
    from primeqa.test_management.models import Requirement, TestCase

    @require_auth
    def _do():
        db = next(get_db())
        try:
            r = db.query(PipelineRun).filter_by(id=run_id).first()
            if r is None or r.tenant_id != request.user["tenant_id"]:
                return ({"error": {"code": "NOT_FOUND", "message": "Run not found"}}, 404)

            elapsed_ms = None
            if r.started_at and r.completed_at:
                elapsed_ms = int((r.completed_at - r.started_at).total_seconds() * 1000)
            elif r.started_at:
                from datetime import datetime, timezone
                elapsed_ms = int((datetime.now(timezone.utc) - r.started_at).total_seconds() * 1000)

            # Per-test result summary.
            results = (db.query(RunTestResult, TestCase, Requirement)
                       .join(TestCase, TestCase.id == RunTestResult.test_case_id)
                       .outerjoin(Requirement, Requirement.id == TestCase.requirement_id)
                       .filter(RunTestResult.run_id == r.id)
                       .order_by(RunTestResult.executed_at.asc())
                       .all())
            tickets = []
            for (rtr, tc, req) in results:
                tickets.append({
                    "test_case_id": tc.id,
                    "key": (req.jira_key if req else None) or tc.title,
                    "status": rtr.status,
                    "duration_ms": rtr.duration_ms,
                    "failure_type": rtr.failure_type,
                })

            return ({
                "id": r.id,
                "status": r.status,
                "run_type": r.run_type,
                "source_type": r.source_type,
                "total_tickets": r.total_tests or 0,
                "completed_tickets": (r.passed or 0) + (r.failed or 0) + (r.skipped or 0),
                "passed_tickets": r.passed or 0,
                "failed_tickets": r.failed or 0,
                "elapsed_ms": elapsed_ms,
                "tickets": tickets,
            }, 200)
        finally:
            db.close()

    return _do()


@views_bp.route("/api/bulk-runs/<int:run_id>/cancel", methods=["POST"])
def api_bulk_run_cancel(run_id):
    """Cancel a queued/running bulk run. Queued tests are marked
    cancelled; any actively-running test is left to complete so the
    executor can clean up its SF scratch records.
    """
    from primeqa.core.auth import require_auth
    from primeqa.execution.models import PipelineRun

    @require_auth
    def _do():
        db = next(get_db())
        try:
            r = db.query(PipelineRun).filter_by(id=run_id).first()
            if r is None or r.tenant_id != request.user["tenant_id"]:
                return ({"error": {"code": "NOT_FOUND", "message": "Run not found"}}, 404)
            # Permission: triggering user OR someone with manage_environments.
            if r.triggered_by != request.user["id"]:
                from primeqa.core.permissions import user_has_permission
                if (request.user.get("role") != "superadmin"
                        and not user_has_permission(request.user["id"],
                                                    "manage_environments", db)):
                    return ({"error": {"code": "FORBIDDEN",
                                       "message": "Only the triggering user or an admin can cancel this run."}}, 403)
            if r.status in ("completed", "failed", "cancelled"):
                return ({"status": r.status, "already_terminal": True}, 200)
            r.status = "cancelled"
            from datetime import datetime, timezone
            r.completed_at = datetime.now(timezone.utc)
            db.commit()
            return ({"id": r.id, "status": "cancelled"}, 200)
        finally:
            db.close()

    return _do()


@views_bp.route("/api/runs/<int:run_id>/summary-text", methods=["GET"])
def api_run_summary_text(run_id):
    """Paste-ready plain-text summary of a run, for Slack / Jira reports.

    Style is intentionally narrow: the same block every team ends up
    hand-typing on Monday morning — timestamp, headline counts, list of
    failed/blocked/unexpected-pass tickets with a one-line cause each.
    """
    from primeqa.core.auth import require_auth
    from primeqa.execution.models import PipelineRun, RunTestResult, RunStepResult
    from primeqa.test_management.models import Requirement, TestCase

    @require_auth
    def _do():
        db = next(get_db())
        try:
            run = db.query(PipelineRun).filter_by(id=run_id).first()
            if run is None or run.tenant_id != request.user["tenant_id"]:
                return ({"error": {"code": "NOT_FOUND", "message": "Run not found"}}, 404)

            # Own-scope check: a view_own_results-only caller sees only
            # their runs. We can't easily import get_scoped_results_query
            # here without a circular import, so duplicate the check.
            from primeqa.core.permissions import get_effective_permissions
            perms = get_effective_permissions(request.user["id"], db)
            if (request.user.get("role") != "superadmin"
                    and "view_all_results" not in perms
                    and run.triggered_by != request.user["id"]):
                return ({"error": {"code": "FORBIDDEN",
                                   "message": "You can only view your own runs."}}, 403)

            rows = (db.query(RunTestResult, TestCase, Requirement)
                    .join(TestCase, TestCase.id == RunTestResult.test_case_id)
                    .outerjoin(Requirement, Requirement.id == TestCase.requirement_id)
                    .filter(RunTestResult.run_id == run.id)
                    .order_by(RunTestResult.executed_at.asc())
                    .all())

            # Count expected-failure outcomes separately (negative tests
            # that SF correctly rejected). Detect via failure_class on
            # the test's step rows — expected_fail_verified = correctly
            # rejected; expected_fail_unverified = unexpected pass.
            def _neg_class(rtr):
                sr = (db.query(RunStepResult)
                      .filter_by(run_test_result_id=rtr.id)
                      .order_by(RunStepResult.step_order.asc())
                      .all())
                classes = [s.failure_class for s in sr]
                if "expected_fail_unverified" in classes:
                    return "unexpected_pass"
                if "expected_fail_verified" in classes:
                    return "expected_failure"
                return None

            passed: list[str] = []
            failed: list[tuple[str, str, str]] = []      # (key, title, cause)
            blocked: list[tuple[str, str, str]] = []
            expected_failures: list[tuple[str, str]] = []
            unexpected_passes: list[tuple[str, str, str]] = []

            for (rtr, tc, req) in rows:
                key = (req.jira_key if req else None) or f"TC-{tc.id}"
                title = tc.title or ""
                neg = _neg_class(rtr)
                if rtr.status in ("failed", "error"):
                    cause = rtr.failure_summary or rtr.failure_type or "failure"
                    if neg == "unexpected_pass":
                        unexpected_passes.append((key, title, cause))
                    else:
                        failed.append((key, title, cause))
                elif rtr.status == "skipped":
                    cause = rtr.failure_summary or "blocked"
                    blocked.append((key, title, cause))
                elif rtr.status == "passed":
                    if neg == "expected_failure":
                        expected_failures.append((key, title))
                    else:
                        passed.append(key)

            # Compose the text.
            lines: list[str] = []
            ts = run.queued_at.strftime("%d %b %Y, %H:%M UTC") if run.queued_at else ""
            title = (run.source_refs or {}).get("suite_name") or run.source_type
            lines.append(f"PrimeQA Run #{run.id} — {title} — {ts}")
            summary_parts = [f"{len(passed)} passed"]
            if failed: summary_parts.append(f"{len(failed)} failed")
            if blocked: summary_parts.append(f"{len(blocked)} blocked")
            if expected_failures:
                summary_parts.append(f"{len(expected_failures)} expected failures")
            if unexpected_passes:
                summary_parts.append(f"{len(unexpected_passes)} unexpected pass")
            lines.append(" \u00b7 ".join(summary_parts))

            if failed:
                lines.append("")
                lines.append("Failed:")
                for (k, t, c) in failed:
                    lines.append(f"  {k}: {t} \u2014 {c}")
            if blocked:
                lines.append("")
                lines.append("Blocked:")
                for (k, t, c) in blocked:
                    lines.append(f"  {k}: {t} \u2014 {c}")
            if unexpected_passes:
                lines.append("")
                lines.append("Unexpected pass (negative tests that should have been rejected):")
                for (k, t, c) in unexpected_passes:
                    lines.append(f"  {k}: {t}")
            if expected_failures:
                lines.append("")
                lines.append("Expected failures (correctly rejected):")
                for (k, t) in expected_failures:
                    lines.append(f"  {k}: {t}")

            return ({"text": "\n".join(lines)}, 200)
        finally:
            db.close()

    return _do()


@views_bp.route("/api/run-step-results/<int:step_id>/diagnosis-text", methods=["GET"])
def api_step_diagnosis_text(step_id):
    """Paste-ready diagnosis for a single failed step.

    Same use-case as summary-text but at step granularity. Format:

        FAIL: Step N — <action> <object> (<failure_class>)
        Summary: <failure_summary or error_message[:200]>
        Details:
          Object: <target_object>
          Record: <target_record_id>
        Suggestion: (pulled from step payload if diagnosis json present)
    """
    from primeqa.core.auth import require_auth
    from primeqa.execution.models import PipelineRun, RunTestResult, RunStepResult

    @require_auth
    def _do():
        db = next(get_db())
        try:
            step = db.query(RunStepResult).filter_by(id=step_id).first()
            if step is None:
                return ({"error": {"code": "NOT_FOUND", "message": "Step not found"}}, 404)
            # Verify tenant + own-scope via the owning run.
            rtr = db.query(RunTestResult).filter_by(id=step.run_test_result_id).first()
            run = db.query(PipelineRun).filter_by(id=rtr.run_id).first() if rtr else None
            if run is None or run.tenant_id != request.user["tenant_id"]:
                return ({"error": {"code": "NOT_FOUND", "message": "Step not found"}}, 404)
            from primeqa.core.permissions import get_effective_permissions
            perms = get_effective_permissions(request.user["id"], db)
            if (request.user.get("role") != "superadmin"
                    and "view_all_diagnosis" not in perms
                    and "view_all_results" not in perms
                    and run.triggered_by != request.user["id"]):
                return ({"error": {"code": "FORBIDDEN",
                                   "message": "You can only view your own diagnosis."}}, 403)

            status_word = "FAIL" if step.status in ("failed", "error") else step.status.upper()
            header = f"{status_word}: Step {step.step_order} \u2014 {step.step_action}"
            if step.target_object:
                header += f" ({step.target_object})"
            if step.failure_class:
                header += f" [{step.failure_class}]"

            lines = [header]
            summary = step.error_message or ""
            if summary:
                lines.append(f"Summary: {summary[:400]}")
            details = []
            if step.target_object:
                details.append(f"  Object: {step.target_object}")
            if step.target_record_id:
                details.append(f"  Record: {step.target_record_id}")
            if step.http_status:
                details.append(f"  HTTP: {step.http_status}")
            if step.duration_ms is not None:
                details.append(f"  Duration: {step.duration_ms}ms")
            if details:
                lines.append("Details:")
                lines.extend(details)

            # Pull a suggestion from the llm_payload or api_response if
            # the diagnosis engine has written one.
            payload = step.llm_payload or {}
            suggestion = None
            if isinstance(payload, dict):
                diagnosis = (payload.get("diagnosis") or payload.get("output")
                             or {})
                if isinstance(diagnosis, dict):
                    suggestion = (diagnosis.get("suggestion")
                                  or diagnosis.get("fix_suggestion"))
            if suggestion:
                lines.append("")
                lines.append(f"Suggestion: {suggestion}")
            if step.correlation_id:
                lines.append(f"Correlation: {step.correlation_id}")

            return ({"text": "\n".join(lines)}, 200)
        finally:
            db.close()

    return _do()


@views_bp.route("/runs/new")
@role_required("admin", "tester", "superadmin")
def runs_new():
    """Unified Run Wizard (R1).

    User picks any combination of Jira sources (sprint / JQL / epic / issue keys),
    PrimeQA suites, requirements, sections, or hand-picked test cases, targets
    an environment, and clicks Preview.
    """
    db = next(get_db())
    try:
        from primeqa.test_management.repository import (
            TestSuiteRepository, SectionRepository, TestCaseRepository,
            RequirementRepository,
        )
        from primeqa.test_management.models import TestCase, TestSuite, SuiteTestCase
        from sqlalchemy import func

        tid = request.user["tenant_id"]
        uid = request.user["id"]

        envs = EnvironmentRepository(db).list_environments(tid)
        envs_data = [{
            "id": e.id, "name": e.name, "env_type": e.env_type,
            "sf_instance_url": e.sf_instance_url,
            "llm_connection_id": e.llm_connection_id,
            "has_meta": bool(e.current_meta_version_id),
        } for e in envs]

        # Suites with a quick test-count (so the user knows what each contains)
        suite_counts = dict(db.query(
            SuiteTestCase.suite_id, func.count(SuiteTestCase.id),
        ).group_by(SuiteTestCase.suite_id).all())
        suites = TestSuiteRepository(db).list_suites(tid)
        suites_data = [{
            "id": s.id, "name": s.name, "suite_type": s.suite_type,
            "test_count": int(suite_counts.get(s.id, 0)),
        } for s in suites]

        sections = SectionRepository(db).list_sections(tid)
        sections_data = [{"id": s.id, "name": s.name} for s in sections]

        # Requirements (with Jira key + summary so the user can recognise them)
        reqs = RequirementRepository(db).list_requirements(tid)
        reqs_data = [{
            "id": r.id, "jira_key": r.jira_key,
            "summary": (r.jira_summary or "")[:120],
            "source": r.source,
        } for r in reqs]

        # Test cases: show the most-recently-updated 500 active ones the user
        # can see (owner's privates + all shared). Client-side search filters
        # the rendered list.
        tcs = TestCaseRepository(db).list_test_cases(
            tid, include_private_for=uid,
        )
        tcs = tcs[:500]
        tcs_data = [{
            "id": t.id, "title": (t.title or "")[:140],
            "status": t.status, "visibility": t.visibility,
        } for t in tcs]

        jira_conns = ConnectionRepository(db).list_connections(tid, "jira")
        jira_conns_data = [{"id": c.id, "name": c.name} for c in jira_conns]

        return render_template("runs/wizard.html", **ctx(
            active_page="runs",
            environments=envs_data, suites=suites_data, sections=sections_data,
            requirements=reqs_data, test_cases=tcs_data,
            jira_connections=jira_conns_data,
        ))
    finally:
        db.close()


def _build_wizard_selection(form):
    """Parse a wizard form submission into a WizardSelection.

    Accepts checkbox-style multi-values (preferred) OR legacy CSV strings so
    both the new selectable UI and any older client still work.
    """
    from primeqa.runs.wizard import WizardSelection

    def _ints(key):
        # Preferred: repeated form fields from checkboxes (name="suite_id")
        vals = form.getlist(key) if hasattr(form, "getlist") else []
        # Legacy fallback: CSV single field (name="suite_ids")
        if not vals:
            csv = form.get(key + "s", "") or form.get(key, "")
            vals = [v.strip() for v in csv.split(",") if v.strip()]
        return [int(v) for v in vals if str(v).strip().lstrip("-").isdigit()]

    def _csv_ints(key):  # kept for jira/legacy paths
        raw = form.get(key, "")
        return [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]

    jira_entries = []
    jira_conn_id = form.get("jira_connection_id", type=int)
    if jira_conn_id:
        # Hand-typed keys (comma-separated): "PROJ-12, PROJ-13"
        keys_raw = (form.get("jira_issue_keys") or "").strip()
        if keys_raw:
            keys = [k.strip() for k in keys_raw.split(",") if k.strip()]
            jira_entries.append({"type": "issues", "connection_id": jira_conn_id, "issue_keys": keys})
        # Sprint by ID
        sprint_id = form.get("jira_sprint_id", type=int)
        if sprint_id:
            jira_entries.append({"type": "sprint", "connection_id": jira_conn_id, "sprint_id": sprint_id})
        # JQL
        jql = (form.get("jira_jql") or "").strip()
        if jql:
            jira_entries.append({"type": "jql", "connection_id": jira_conn_id, "jql": jql})
        # Epic
        epic_key = (form.get("jira_epic_key") or "").strip()
        if epic_key:
            jira_entries.append({
                "type": "epic", "connection_id": jira_conn_id, "epic_key": epic_key,
                "status": (form.get("jira_epic_status") or "").strip() or None,
            })

    return WizardSelection(
        suite_ids=_csv_ints("suite_ids") or [int(x) for x in form.getlist("suite_id") if x.isdigit()],
        section_ids=_csv_ints("section_ids"),
        test_case_ids=_csv_ints("test_case_ids"),
        requirement_ids=_csv_ints("requirement_ids"),
        jira=jira_entries,
    )


@views_bp.route("/runs/new/preview", methods=["POST"])
@role_required("admin", "tester", "superadmin")
def runs_new_preview():
    """Resolve the wizard selection, run pre-flight, render the preview screen."""
    from primeqa.runs.wizard import RunWizardResolver
    from primeqa.runs.preflight import Preflight
    from primeqa.test_management.repository import (
        TestSuiteRepository, SectionRepository, TestCaseRepository,
        RequirementRepository,
    )
    from primeqa.metadata.repository import MetadataRepository
    db = next(get_db())
    try:
        env_repo = EnvironmentRepository(db)
        conn_repo = ConnectionRepository(db)
        suite_repo = TestSuiteRepository(db)
        section_repo = SectionRepository(db)
        tc_repo = TestCaseRepository(db)
        req_repo = RequirementRepository(db)
        meta_repo = MetadataRepository(db)

        selection = _build_wizard_selection(request.form)
        environment_id = int(request.form["environment_id"])

        resolver = RunWizardResolver(
            db, suite_repo=suite_repo, section_repo=section_repo,
            tc_repo=tc_repo, req_repo=req_repo, connection_repo=conn_repo,
        )
        try:
            resolved = resolver.resolve(request.user["tenant_id"], selection)
        except Exception as e:
            from flask import flash
            flash(f"Selection failed: {e}", "error")
            return redirect("/runs/new")

        preflight = Preflight(
            db, env_repo=env_repo, conn_repo=conn_repo,
            tc_repo=tc_repo, meta_repo=meta_repo,
        )
        report = preflight.check(
            request.user["tenant_id"], request.user, environment_id, resolved,
        )

        env = env_repo.get_environment(environment_id, request.user["tenant_id"])

        # Optional cost forecast (Super Admin only)
        cost = None
        if request.user["role"] == "superadmin":
            cost = _estimate_run_cost(resolved.test_count, env)

        # F2: drift check. Cheap Tooling query (~1 s) tells us if any
        # field / VR / flow / trigger changed since the env's current meta
        # version was synced. Failure modes (no conn / OAuth error) are
        # surfaced as warnings, not hard blockers.
        from primeqa.metadata.service import MetadataService
        from primeqa.metadata.repository import MetadataRepository
        from primeqa.metadata.worker_runner import _oauth_token
        drift = None
        try:
            meta_svc = MetadataService(MetadataRepository(db), env_repo)
            drift = meta_svc.check_drift(
                environment_id, request.user["tenant_id"],
                oauth_token_fetcher=_oauth_token,
            )
        except Exception as _e:
            drift = {"error": str(_e), "drift_detected": False,
                     "has_current_meta": False, "counts": {}}

        return render_template("runs/preview.html", **ctx(
            active_page="runs",
            environment_id=environment_id,
            environment=env,
            resolved={
                "test_count": resolved.test_count,
                "test_case_ids": resolved.test_case_ids,
                "source_refs": resolved.source_refs,
                "resolution_warnings": resolved.resolution_warnings,
                "missing_jira_keys": resolved.missing_jira_keys,
            },
            report=report.to_dict(),
            cost=cost,
            drift=drift,
            priority=request.form.get("priority", "normal"),
            run_type=request.form.get("run_type", "execute_only"),
        ))
    finally:
        db.close()


def _estimate_run_cost(test_count, env, run_type="execute_only"):
    """Cost forecast delegated to primeqa.runs.cost."""
    from primeqa.runs.cost import estimate_run_cost
    # Pull LLM model from the connected LLM connection if available
    model = None
    if env and env.llm_connection_id:
        try:
            from primeqa.core.repository import ConnectionRepository
            from primeqa.db import SessionLocal
            db = SessionLocal()
            conn = ConnectionRepository(db).get_connection_decrypted(
                env.llm_connection_id, env.tenant_id,
            )
            db.close()
            if conn and conn.get("config"):
                model = conn["config"].get("model")
        except Exception:
            pass
    return estimate_run_cost(test_count, model=model, run_type=run_type)


@views_bp.route("/runs", methods=["POST"])
@role_required("admin", "tester", "superadmin")
def runs_create():
    """Queue a new run from the wizard preview."""
    from flask import flash
    import json as _json
    from primeqa.execution.repository import (
        PipelineRunRepository, PipelineStageRepository,
        ExecutionSlotRepository, WorkerHeartbeatRepository,
    )
    from primeqa.execution.service import PipelineService
    db = next(get_db())
    try:
        svc = PipelineService(
            PipelineRunRepository(db), PipelineStageRepository(db),
            ExecutionSlotRepository(db), WorkerHeartbeatRepository(db),
        )
        test_case_ids = _json.loads(request.form.get("test_case_ids_json", "[]"))
        source_refs = _json.loads(request.form.get("source_refs_json", "{}"))
        override_token = request.form.get("override_token") or None

        # Re-run preflight server-side (never trust client)
        if test_case_ids:
            from primeqa.runs.preflight import Preflight
            from primeqa.runs.wizard import ResolvedRun
            from primeqa.test_management.repository import TestCaseRepository
            from primeqa.metadata.repository import MetadataRepository
            preflight = Preflight(
                db, env_repo=EnvironmentRepository(db),
                conn_repo=ConnectionRepository(db),
                tc_repo=TestCaseRepository(db),
                meta_repo=MetadataRepository(db),
            )
            resolved = ResolvedRun(
                test_case_ids=test_case_ids,
                source_refs=source_refs,
                resolution_warnings=[],
                missing_jira_keys=[],
            )
            environment_id = int(request.form["environment_id"])
            report = preflight.check(
                request.user["tenant_id"], request.user, environment_id, resolved,
            )
            try:
                preflight.ensure_runnable(report, request.user, override_token)
            except Exception as e:
                flash(f"Pre-flight failed: {e}", "error")
                return redirect("/runs/new")
        else:
            flash("Selection produced zero tests; refine and try again.", "error")
            return redirect("/runs/new")

        result = svc.create_run(
            tenant_id=request.user["tenant_id"],
            environment_id=int(request.form["environment_id"]),
            triggered_by=request.user["id"],
            run_type=request.form.get("run_type", "execute_only"),
            source_type="test_cases",  # flat, rich details in source_refs
            source_ids=test_case_ids,
            priority=request.form.get("priority", "normal"),
            source_refs=source_refs,
        )
        return redirect(f"/runs/{result['id']}")
    finally:
        db.close()


@views_bp.route("/runs/<int:run_id>")
@login_required
def runs_detail(run_id):
    db = next(get_db())
    try:
        from primeqa.execution.repository import PipelineRunRepository, PipelineStageRepository, RunTestResultRepository, RunStepResultRepository
        run_repo = PipelineRunRepository(db)
        run = run_repo.get_run(run_id, request.user["tenant_id"])
        if not run:
            return redirect("/runs")
        stages = PipelineStageRepository(db).get_stages(run_id)
        results = RunTestResultRepository(db).list_results(run_id)
        step_repo = RunStepResultRepository(db)

        run_data = {
            "id": run.id, "status": run.status, "run_type": run.run_type,
            "priority": run.priority, "passed": run.passed, "failed": run.failed,
            "total_tests": run.total_tests,
            "environment_id": run.environment_id,
            "queued_at": run.queued_at.isoformat() if run.queued_at else None,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "source_refs": run.source_refs or {},
            "parent_run_id": run.parent_run_id,
            # Migration 030
            "label": run.label,
            "failure_summary_ai": run.failure_summary_ai,
            "failure_summary_at": run.failure_summary_at.isoformat() if run.failure_summary_at else None,
            "failure_summary_model": run.failure_summary_model,
        }

        # R5: agent fixes for this run
        from primeqa.intelligence.models import AgentFixAttempt
        agent_fixes = db.query(AgentFixAttempt).filter(
            AgentFixAttempt.run_id == run.id,
        ).order_by(AgentFixAttempt.created_at.desc()).all()
        agent_fixes_data = [{
            "id": f.id, "test_case_id": f.test_case_id,
            "failure_class": f.failure_class,
            "root_cause_summary": f.root_cause_summary,
            "confidence": float(f.confidence) if f.confidence is not None else None,
            "trust_band": f.trust_band,
            "proposed_fix_type": f.proposed_fix_type,
            "before_state": f.before_state,
            "after_state": f.after_state,
            "auto_applied": f.auto_applied,
            "rerun_run_id": f.rerun_run_id,
            "rerun_outcome": f.rerun_outcome,
            "user_decision": f.user_decision,
            "decided_at": f.decided_at.isoformat() if f.decided_at else None,
        } for f in agent_fixes]
        stages_data = [{"stage_name": s.stage_name, "status": s.status} for s in stages]
        # Batch-hydrate test-case titles so the redesigned run detail
        # page can show human-readable names instead of "Test #N".
        # Single query scoped to this tenant, no N+1.
        from primeqa.test_management.models import TestCase as _TCModel
        _tc_ids = [r.test_case_id for r in results if r.test_case_id]
        _titles = {}
        if _tc_ids:
            _tc_rows = db.query(_TCModel.id, _TCModel.title).filter(
                _TCModel.id.in_(_tc_ids),
                _TCModel.tenant_id == request.user["tenant_id"],
            ).all()
            _titles = {row.id: row.title for row in _tc_rows}

        results_data = []
        for r in results:
            steps = step_repo.list_step_results(r.id)
            results_data.append({
                "test_case_id": r.test_case_id, "status": r.status,
                "title": _titles.get(r.test_case_id),
                "failure_summary": r.failure_summary,
                "duration_ms": r.duration_ms,
                "steps": [{"step_order": s.step_order, "step_action": s.step_action,
                           "target_object": s.target_object, "status": s.status,
                           "error_message": s.error_message,
                           "failure_class": s.failure_class,
                           "duration_ms": s.duration_ms} for s in steps],
            })
        # ---- Cost + LLM breakdown (superadmin only) --------------------
        # Phase 3 switch: pull from llm_usage_log for accurate per-task
        # attribution of generation + agent_fix + failure_summary + any
        # future task. Cross-referenced via run_id / test_case_id /
        # generation_batch_id \u2014 populated by the LLMGateway.
        cost_panel = None
        if request.user.get("role") == "superadmin":
            from primeqa.test_management.models import TestCase
            from primeqa.intelligence.models import LLMUsageLog
            from sqlalchemy import or_, func as sf

            tc_ids = [r.test_case_id for r in results]
            # Fetch generation_batch_ids owned by these TCs so gen spend
            # attributes here even when the call site pre-dates run_id
            # forwarding.
            batch_ids = []
            if tc_ids:
                batch_ids = [row[0] for row in db.query(TestCase.generation_batch_id)
                              .filter(TestCase.id.in_(tc_ids),
                                      TestCase.generation_batch_id.isnot(None)).all()
                              if row[0]]

            rows = db.query(
                LLMUsageLog.task,
                LLMUsageLog.model,
                sf.count(LLMUsageLog.id).label("calls"),
                sf.coalesce(sf.sum(LLMUsageLog.input_tokens), 0).label("ti"),
                sf.coalesce(sf.sum(LLMUsageLog.output_tokens), 0).label("to"),
                sf.coalesce(sf.sum(LLMUsageLog.cached_input_tokens), 0).label("tc"),
                sf.coalesce(sf.sum(LLMUsageLog.cost_usd), 0).label("cost"),
            ).filter(
                or_(
                    LLMUsageLog.run_id == run.id,
                    LLMUsageLog.generation_batch_id.in_(batch_ids) if batch_ids else False,
                ),
                LLMUsageLog.status == "ok",
            ).group_by(LLMUsageLog.task, LLMUsageLog.model).all()

            by_task: dict[str, dict] = {}
            grand_total = 0.0
            for row in rows:
                task_bucket = by_task.setdefault(row.task, {
                    "calls": 0, "cost_usd": 0.0,
                    "tokens_in": 0, "tokens_out": 0, "cached_tokens": 0,
                    "models": set(),
                })
                task_bucket["calls"] += row.calls
                task_bucket["cost_usd"] += float(row.cost)
                task_bucket["tokens_in"] += int(row.ti)
                task_bucket["tokens_out"] += int(row.to)
                task_bucket["cached_tokens"] += int(row.tc)
                task_bucket["models"].add(row.model)
                grand_total += float(row.cost)

            # Sort models for deterministic rendering
            for b in by_task.values():
                b["models"] = sorted(m for m in b["models"] if m and m != "(blocked)")
                b["cost_usd"] = round(b["cost_usd"], 6)

            cost_panel = {
                "total_usd": round(grand_total, 6),
                "by_task": by_task,
                # Legacy keys kept so the template doesn't break; we'll
                # remove once the template is refactored in the same PR.
                "generation": by_task.get("test_plan_generation", {
                    "cost_usd": 0.0, "tokens_in": 0, "tokens_out": 0,
                    "cached_tokens": 0, "models": [], "calls": 0,
                }),
                "agent_fixes": {
                    "attempts": len(agent_fixes_data),
                    "cost_usd": by_task.get("agent_fix", {}).get("cost_usd", 0.0),
                    "calls": by_task.get("agent_fix", {}).get("calls", 0),
                },
                "failure_summary": by_task.get("failure_summary", {
                    "cost_usd": 0.0, "calls": 0,
                }),
            }

        return render_template("runs/detail.html", **ctx(
            active_page="runs", run=run_data, stages=stages_data, results=results_data,
            agent_fixes=agent_fixes_data, cost_panel=cost_panel,
        ))
    finally:
        db.close()


@views_bp.route("/runs/<int:run_id>/cancel", methods=["POST"])
@role_required("admin", "tester")
def runs_cancel(run_id):
    db = next(get_db())
    try:
        from primeqa.execution.repository import PipelineRunRepository, PipelineStageRepository, ExecutionSlotRepository, WorkerHeartbeatRepository
        from primeqa.execution.service import PipelineService
        svc = PipelineService(
            PipelineRunRepository(db), PipelineStageRepository(db),
            ExecutionSlotRepository(db), WorkerHeartbeatRepository(db),
        )
        svc.cancel_run(run_id, request.user["tenant_id"])
        return redirect(f"/runs/{run_id}")
    except Exception:
        return redirect(f"/runs/{run_id}")
    finally:
        db.close()


# --- Test Cases ---

@views_bp.route("/test-cases")
@login_required
def test_cases_library():
    """Test Library with two viewing modes:

    - **Grouped by requirement** (default once multi-TC is in play):
      Pages over requirements. For each requirement on the page, fetches
      all its visible TCs and renders them inline under the requirement
      header. TCs not linked to a requirement land in an "Unlinked"
      bucket that renders once on page 1 only.
    - **Flat** (?group_by=flat): current per-TC paginated view.

    Default is inferred: if any TC in this tenant was produced by the
    multi-TC generator (has generation_batch_id), grouped wins; else
    flat. Explicit ?group_by overrides.
    """
    from primeqa.test_management.repository import (
        SectionRepository, TestCaseRepository, RequirementRepository,
        TestSuiteRepository,
    )
    from primeqa.test_management.models import TestCase

    db = next(get_db())
    try:
        section_repo = SectionRepository(db)
        tc_repo = TestCaseRepository(db)
        req_repo = RequirementRepository(db)
        suite_repo = TestSuiteRepository(db)
        tid = request.user["tenant_id"]
        uid = request.user["id"]

        sections = section_repo.get_section_tree(tid)

        # Active suites for the per-group "Add to suite" dropdown. Small
        # list (tenants usually have < 30 suites); no pagination needed.
        suite_rows = suite_repo.list_suites(tid) if hasattr(suite_repo, "list_suites") else []
        # list_suites may not exist; fall back to a direct query
        if not suite_rows:
            from primeqa.test_management.models import TestSuite
            suite_rows = db.query(TestSuite).filter(
                TestSuite.tenant_id == tid, TestSuite.deleted_at.is_(None),
            ).order_by(TestSuite.name).all()
        suites_data = [{"id": s.id, "name": s.name, "suite_type": s.suite_type}
                       for s in suite_rows]

        # Shared list params
        page = request.args.get("page", 1, type=int) or 1
        per_page = request.args.get("per_page", 20, type=int) or 20
        q = (request.args.get("q") or "").strip()
        sort = request.args.get("sort", "updated_at")
        order = request.args.get("order", "desc")
        section_id = request.args.get("section_id", type=int)
        status = request.args.get("status") or None
        show_deleted = request.args.get("deleted", "").lower() in ("1", "true", "yes")
        # Requirement sort order (grouped view only). Default activity so the
        # most-recently-touched requirement sits on top; alternatives are
        # alphabetical by Jira key (nulls last) or by summary/name.
        req_sort = request.args.get("req_sort", "activity")
        if req_sort not in ("activity", "jira_key", "name"):
            req_sort = "activity"

        filters = {}
        if section_id:
            filters["section_id"] = section_id
        if status:
            filters["status"] = status
        if request.args.get("coverage_type"):
            filters["coverage_type"] = request.args.get("coverage_type")

        # Group-by mode: explicit param wins; otherwise infer from whether
        # any TC in the tenant has a generation_batch_id.
        explicit_group = request.args.get("group_by")
        if explicit_group in ("requirement", "flat"):
            group_by = explicit_group
        else:
            any_batched = (db.query(TestCase.id)
                           .filter(TestCase.tenant_id == tid,
                                   TestCase.generation_batch_id.isnot(None))
                           .first())
            group_by = "requirement" if any_batched else "flat"

        # Coverage-type sort order within a group: positive first so the
        # happy path sits on top, then the rest in a stable order.
        COV_ORDER = {
            "positive": 0, "negative_validation": 1, "boundary": 2,
            "edge_case": 3, "regression": 4,
        }

        query_error = None
        groups = []
        unlinked_tcs = []
        meta = {"total": 0, "page": 1, "per_page": per_page, "total_pages": 0}
        tc_data = []

        if group_by == "requirement":
            # Fetch bounded TC list, then group + sort + paginate groups
            try:
                all_tcs = tc_repo.list_for_grouping(
                    tid, user_id=uid, q=q, filters=filters,
                    include_deleted=show_deleted, max_items=500,
                )
            except Exception as e:
                query_error = str(e)
                all_tcs = []

            # Bucket by requirement_id
            by_req = {}
            for tc in all_tcs:
                if tc.requirement_id:
                    by_req.setdefault(tc.requirement_id, []).append(tc)
                else:
                    unlinked_tcs.append(tc)

            # Load requirement summaries in one shot. Include soft-deleted
            # rows so the group header still shows a meaningful title +
            # "req deleted" badge for orphaned TCs.
            reqs_by_id = req_repo.get_requirements_by_ids(
                by_req.keys(), tid, include_deleted=True,
            )

            # Sort each bucket by coverage then updated_at desc
            def _tc_sort_key(tc):
                return (
                    COV_ORDER.get(tc.coverage_type, 99),
                    -(tc.updated_at.timestamp() if tc.updated_at else 0),
                )
            for rid, tcs in by_req.items():
                tcs.sort(key=_tc_sort_key)

            # Sort groups per req_sort:
            #   activity  \u2014 most recent TC update (default, activity-first)
            #   jira_key  \u2014 alphabetical by Jira key (nulls last)
            #   name      \u2014 alphabetical by requirement summary / title
            def _group_recency(kv):
                _rid, tcs = kv
                return max((t.updated_at.timestamp() if t.updated_at else 0
                            for t in tcs), default=0)
            def _group_jira_key(kv):
                rid, _tcs = kv
                r = reqs_by_id.get(rid)
                # None / empty jira_key sorts LAST (tuple (1, '') > (0, anything))
                return (0, r.jira_key) if r and r.jira_key else (1, "")
            def _group_name(kv):
                rid, _tcs = kv
                r = reqs_by_id.get(rid)
                s = (r.jira_summary if r else None) or ""
                return s.lower()
            if req_sort == "jira_key":
                sorted_groups = sorted(by_req.items(), key=_group_jira_key)
            elif req_sort == "name":
                sorted_groups = sorted(by_req.items(), key=_group_name)
            else:
                sorted_groups = sorted(by_req.items(), key=_group_recency, reverse=True)

            # Paginate requirements (not TCs)
            total_groups = len(sorted_groups)
            per_page_grp = min(max(per_page, 1), 50)
            total_pages = max(1, (total_groups + per_page_grp - 1) // per_page_grp)
            page = max(1, min(page, total_pages))
            start = (page - 1) * per_page_grp
            page_slice = sorted_groups[start:start + per_page_grp]

            def _tc_dict(tc):
                return {
                    "id": tc.id, "title": tc.title, "status": tc.status,
                    "visibility": tc.visibility, "owner_id": tc.owner_id,
                    "updated_at": tc.updated_at.isoformat() if tc.updated_at else "",
                    "coverage_type": getattr(tc, "coverage_type", None),
                    "generation_batch_id": getattr(tc, "generation_batch_id", None),
                    "requirement_id": tc.requirement_id,
                }

            for rid, tcs in page_slice:
                req = reqs_by_id.get(rid)
                # Coverage breakdown per group for the header chip + the
                # "Add to suite" modal's coverage-filter sub-chips.
                cov_counts = {}
                cov_tc_ids = {}  # coverage_type -> [tc_id, ...]
                for tc in tcs:
                    k = tc.coverage_type or "other"
                    cov_counts[k] = cov_counts.get(k, 0) + 1
                    cov_tc_ids.setdefault(k, []).append(tc.id)
                groups.append({
                    "requirement_id": rid,
                    "jira_key": req.jira_key if req else None,
                    "summary": (req.jira_summary if req else None) or f"Requirement #{rid}",
                    "deleted": bool(req.deleted_at) if req else False,
                    "test_cases": [_tc_dict(tc) for tc in tcs],
                    "coverage_counts": cov_counts,
                    # JSON-stringifiable: the template passes this to JS as
                    # a data-* attribute and the modal reads it to build the
                    # coverage-filter chips.
                    "coverage_tc_ids": cov_tc_ids,
                    "all_tc_ids": [tc.id for tc in tcs],
                })

            unlinked_data = [_tc_dict(tc) for tc in unlinked_tcs]
            # Unlinked bucket only shows on page 1 to keep the mental model simple.
            if page != 1:
                unlinked_data = []

            meta = {"total": total_groups, "page": page,
                    "per_page": per_page_grp, "total_pages": total_pages}
        else:
            # Flat mode — original per-TC pagination
            try:
                result = tc_repo.list_page(
                    tid, user_id=uid,
                    page=page, per_page=per_page, q=q, sort=sort, order=order,
                    filters=filters, include_deleted=show_deleted,
                )
                tc_rows = result.items
                meta = {
                    "total": result.total, "page": result.page,
                    "per_page": result.per_page, "total_pages": result.total_pages,
                }
            except Exception as e:
                query_error = str(e)
                tc_rows = []
            tc_data = [{
                "id": tc.id, "title": tc.title, "status": tc.status,
                "visibility": tc.visibility, "owner_id": tc.owner_id,
                "updated_at": tc.updated_at.isoformat() if tc.updated_at else "",
                "deleted_at": tc.deleted_at.isoformat() if getattr(tc, "deleted_at", None) else None,
                "coverage_type": getattr(tc, "coverage_type", None),
                "generation_batch_id": getattr(tc, "generation_batch_id", None),
                "requirement_id": tc.requirement_id,
            } for tc in tc_rows]
            unlinked_data = []

        return render_template("test_cases/library.html", **ctx(
            active_page="test_cases", sections=sections,
            group_by=group_by,
            test_cases=tc_data,           # flat mode
            groups=groups,                # grouped mode
            unlinked_test_cases=unlinked_data,  # grouped mode, page 1 only
            suites=suites_data,           # for per-group "Add to suite" dropdown
            section_id=section_id, meta=meta,
            search=q, sort=sort, order=order, status_filter=status,
            coverage_filter=request.args.get("coverage_type"),
            req_sort=req_sort,
            show_deleted=show_deleted, query_error=query_error,
        ))
    finally:
        db.close()


@views_bp.route("/test-cases/<int:tc_id>")
@login_required
def test_cases_detail(tc_id):
    db = next(get_db())
    try:
        from primeqa.test_management.repository import TestCaseRepository
        tc_repo = TestCaseRepository(db)
        tc = tc_repo.get_test_case(tc_id, request.user["tenant_id"])
        if not tc:
            return redirect("/test-cases")
        if tc.visibility == "private" and tc.owner_id != request.user["id"]:
            return redirect("/test-cases")

        versions = tc_repo.get_versions(tc_id)
        current_version = None
        if tc.current_version_id:
            for v in versions:
                if v.id == tc.current_version_id:
                    current_version = v
                    break

        tc_data = {
            "id": tc.id, "title": tc.title, "status": tc.status,
            "visibility": tc.visibility, "owner_id": tc.owner_id,
            "version": tc.version, "current_version_id": tc.current_version_id,
            # Phase 7: thumbs buttons conditionally render on AI-generated
            # TCs. `generation_batch_id IS NOT NULL` is the canonical
            # "the AI produced this" marker.
            "generation_batch_id": getattr(tc, "generation_batch_id", None),
            "coverage_type": getattr(tc, "coverage_type", None),
            "updated_at": tc.updated_at.isoformat() if tc.updated_at else None,
        }
        cv_data = None
        validation_report = None
        if current_version:
            cv_data = {
                "version_number": current_version.version_number,
                "generation_method": current_version.generation_method,
                "steps": current_version.steps or [],
                "referenced_entities": current_version.referenced_entities or [],
                "confidence_score": current_version.confidence_score,
            }
            # Static validation report from migration 029. Drives the
            # banner on the detail page and the per-issue Apply button.
            validation_report = current_version.validation_report or None
        versions_data = [{
            "id": v.id, "version_number": v.version_number,
            "generation_method": v.generation_method,
            "confidence_score": v.confidence_score,
            "created_at": v.created_at.isoformat() if v.created_at else "",
        } for v in versions]

        # Run history
        from primeqa.execution.models import RunTestResult, PipelineRun
        run_results = db.query(RunTestResult).join(
            PipelineRun, RunTestResult.run_id == PipelineRun.id,
        ).filter(
            RunTestResult.test_case_id == tc_id,
            PipelineRun.tenant_id == request.user["tenant_id"],
        ).order_by(RunTestResult.executed_at.desc()).limit(10).all()
        run_history = [{
            "id": r.id, "run_id": r.run_id, "status": r.status,
            "failure_summary": r.failure_summary, "duration_ms": r.duration_ms,
            "executed_at": r.executed_at.isoformat() if r.executed_at else "",
        } for r in run_results]

        # Available environments
        envs = EnvironmentRepository(db).list_environments(
            request.user["tenant_id"], request.user["id"], request.user["role"],
        )
        envs_data = [{"id": e.id, "name": e.name} for e in envs]

        return render_template("test_cases/detail.html", **ctx(
            active_page="test_cases", tc=tc_data, current_version=cv_data,
            versions=versions_data, run_history=run_history, environments=envs_data,
            validation_report=validation_report,
        ))
    finally:
        db.close()


@views_bp.route("/test-cases/<int:tc_id>/share", methods=["POST"])
@login_required
def test_cases_share(tc_id):
    db = next(get_db())
    try:
        from primeqa.test_management.repository import TestCaseRepository
        tc_repo = TestCaseRepository(db)
        tc = tc_repo.get_test_case(tc_id, request.user["tenant_id"])
        if tc and tc.owner_id == request.user["id"]:
            tc_repo.update_test_case(tc_id, request.user["tenant_id"], {"visibility": "shared"})
        return redirect(f"/test-cases/{tc_id}")
    finally:
        db.close()


@views_bp.route("/test-cases/<int:tc_id>/run", methods=["POST"])
@role_required("admin", "tester")
def test_cases_run(tc_id):
    from flask import flash
    db = next(get_db())
    try:
        from primeqa.execution.repository import (
            PipelineRunRepository, PipelineStageRepository,
            ExecutionSlotRepository, WorkerHeartbeatRepository,
        )
        from primeqa.execution.service import PipelineService
        svc = PipelineService(
            PipelineRunRepository(db), PipelineStageRepository(db),
            ExecutionSlotRepository(db), WorkerHeartbeatRepository(db),
        )
        result = svc.create_run(
            tenant_id=request.user["tenant_id"],
            environment_id=int(request.form["environment_id"]),
            triggered_by=request.user["id"],
            run_type="execute_only",
            source_type="test_cases",
            source_ids=[tc_id],
            priority=request.form.get("priority", "normal"),
        )
        flash(f"Run #{result['id']} queued", "success")
        return redirect(f"/runs/{result['id']}")
    except Exception as e:
        flash(f"Run failed: {e}", "error")
        return redirect(f"/test-cases/{tc_id}")
    finally:
        db.close()


@views_bp.route("/test-cases/<int:tc_id>/edit", methods=["GET"])
@role_required("admin", "tester")
def test_cases_edit(tc_id):
    db = next(get_db())
    try:
        from primeqa.test_management.repository import TestCaseRepository
        from primeqa.test_management.step_schema import STEP_ACTIONS
        tc_repo = TestCaseRepository(db)
        tc = tc_repo.get_test_case(tc_id, request.user["tenant_id"])
        if not tc:
            return redirect("/test-cases")
        current_version = tc_repo.get_latest_version(tc.id)
        initial_steps = current_version.steps if current_version else []
        envs = EnvironmentRepository(db).list_environments(
            request.user["tenant_id"], request.user["id"], request.user["role"],
        )
        envs_data = [{"id": e.id, "name": e.name} for e in envs]
        env_id = envs_data[0]["id"] if envs_data else None
        tc_data = {"id": tc.id, "title": tc.title, "version": tc.version}
        return render_template("test_cases/edit.html", **ctx(
            active_page="test_cases", tc=tc_data, initial_steps=initial_steps,
            step_schema=STEP_ACTIONS, environments=envs_data, env_id=env_id, error=None,
        ))
    finally:
        db.close()


@views_bp.route("/test-cases/<int:tc_id>/edit", methods=["POST"])
@role_required("admin", "tester")
def test_cases_update_steps(tc_id):
    from flask import flash
    import json as _json
    db = next(get_db())
    try:
        from primeqa.test_management.repository import TestCaseRepository
        from primeqa.test_management.step_schema import StepValidator
        from primeqa.metadata.repository import MetadataRepository
        tc_repo = TestCaseRepository(db)
        tc = tc_repo.get_test_case(tc_id, request.user["tenant_id"])
        if not tc:
            return redirect("/test-cases")

        title = request.form.get("title") or tc.title
        if title != tc.title:
            try:
                expected_version = int(request.form.get("expected_version", tc.version))
            except (TypeError, ValueError):
                expected_version = tc.version
            updated, result = tc_repo.update_test_case(
                tc_id, request.user["tenant_id"],
                {"title": title}, expected_version=expected_version,
            )
            if result == "conflict":
                flash(
                    "This test case was modified by someone else while you were editing. "
                    "Reload to see the latest version before saving.", "error",
                )
                return redirect(f"/test-cases/{tc_id}/edit")

        try:
            steps = _json.loads(request.form.get("steps_json", "[]"))
        except Exception:
            steps = []

        env_id = request.form.get("environment_id", type=int)
        meta_version_id = tc.current_version_id
        if env_id:
            env = EnvironmentRepository(db).get_environment(env_id, request.user["tenant_id"])
            if env and env.current_meta_version_id:
                meta_version_id = env.current_meta_version_id
                validator = StepValidator(MetadataRepository(db), env.current_meta_version_id)
                ok, errors = validator.validate(steps)
                if not ok:
                    flash("Validation errors: " + "; ".join(errors[:5]), "error")
                    return redirect(f"/test-cases/{tc_id}/edit")

        if not meta_version_id:
            from primeqa.test_management.models import TestCaseVersion
            prev = db.query(TestCaseVersion).filter(
                TestCaseVersion.test_case_id == tc_id,
            ).order_by(TestCaseVersion.version_number.desc()).first()
            meta_version_id = prev.metadata_version_id if prev else None

        if not meta_version_id:
            flash("No metadata version available. Select an environment.", "error")
            return redirect(f"/test-cases/{tc_id}/edit")

        tc_repo.create_version(
            test_case_id=tc_id,
            metadata_version_id=meta_version_id,
            created_by=request.user["id"],
            steps=steps,
            expected_results=[s.get("expected_result", "") for s in steps],
            preconditions=[],
            generation_method=request.form.get("generation_method", "manual"),
            referenced_entities=[],
        )
        flash("Saved new version", "success")
        return redirect(f"/test-cases/{tc_id}")
    except Exception as e:
        flash(f"Save failed: {e}", "error")
        return redirect(f"/test-cases/{tc_id}/edit")
    finally:
        db.close()


# --- Reviews ---

@views_bp.route("/reviews")
@role_required("admin", "ba")
def reviews_queue():
    db = next(get_db())
    try:
        from primeqa.test_management.repository import BAReviewRepository
        repo = BAReviewRepository(db)
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 20, type=int)
        sort = request.args.get("sort", "created_at")
        order = request.args.get("order", "desc")
        show_deleted = request.args.get("deleted", "").lower() in ("1", "true", "yes")
        filters = {}
        status = request.args.get("status")
        if status:
            filters["status"] = status
        only_mine = request.args.get("mine", "1") != "0"
        if only_mine:
            filters["assigned_to"] = request.user["id"]

        try:
            result = repo.list_page(
                request.user["tenant_id"],
                page=page, per_page=per_page, q=None, sort=sort, order=order,
                filters=filters, include_deleted=show_deleted,
            )
            reviews = result.items
            meta = {"total": result.total, "page": result.page,
                    "per_page": result.per_page, "total_pages": result.total_pages}
            query_error = None
        except Exception as e:
            reviews, meta, query_error = [], {"total": 0, "page": 1, "per_page": per_page, "total_pages": 0}, str(e)

        reviews_data = [{
            "id": r.id, "test_case_version_id": r.test_case_version_id,
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else "",
        } for r in reviews]
        return render_template("reviews/queue.html", **ctx(
            active_page="reviews", reviews=reviews_data,
            meta=meta, status_filter=status, only_mine=only_mine,
            show_deleted=show_deleted, query_error=query_error,
        ))
    finally:
        db.close()


@views_bp.route("/reviews/<int:review_id>")
@role_required("admin", "ba")
def reviews_detail(review_id):
    db = next(get_db())
    try:
        from primeqa.test_management.repository import BAReviewRepository, TestCaseRepository
        from primeqa.test_management.models import TestCaseVersion, TestCase
        review = BAReviewRepository(db).get_review(review_id)
        if not review:
            return redirect("/reviews")
        tcv = db.query(TestCaseVersion).filter(TestCaseVersion.id == review.test_case_version_id).first()
        tc = db.query(TestCase).filter(TestCase.id == tcv.test_case_id).first() if tcv else None
        review_data = {
            "id": review.id, "test_case_version_id": review.test_case_version_id,
            "status": review.status, "feedback": review.feedback,
            "step_comments": review.step_comments or [],
            "created_at": review.created_at.isoformat() if review.created_at else "",
        }
        version_data = None
        if tcv:
            version_data = {
                "id": tcv.id, "version_number": tcv.version_number,
                "generation_method": tcv.generation_method,
                "confidence_score": tcv.confidence_score,
                "steps": tcv.steps or [],
                "referenced_entities": tcv.referenced_entities or [],
            }
        tc_data = {"id": tc.id, "title": tc.title} if tc else None
        return render_template("reviews/detail.html", **ctx(
            active_page="reviews", review=review_data, version=version_data, tc=tc_data,
        ))
    finally:
        db.close()


@views_bp.route("/reviews/<int:review_id>", methods=["POST"])
@role_required("admin", "ba")
def reviews_submit(review_id):
    from flask import flash
    import json as _json
    db = next(get_db())
    try:
        from primeqa.test_management.repository import BAReviewRepository, TestCaseRepository
        from primeqa.test_management.models import TestCaseVersion
        review_repo = BAReviewRepository(db)
        status = request.form.get("status")
        feedback = request.form.get("feedback")
        step_comments = []
        for key, val in request.form.items():
            if key.startswith("step_comment_") and val.strip():
                step_order = int(key.replace("step_comment_", ""))
                step_comments.append({"step_order": step_order, "comment": val.strip()})
        review = review_repo.update_review(
            review_id, status, feedback, request.user["id"], step_comments=step_comments,
        )
        if review and status == "approved":
            tcv = db.query(TestCaseVersion).filter(
                TestCaseVersion.id == review.test_case_version_id,
            ).first()
            if tcv:
                tc_repo = TestCaseRepository(db)
                tc_repo.update_test_case(
                    tcv.test_case_id, request.user["tenant_id"],
                    {"status": "approved", "visibility": "shared"},
                )
        flash(f"Review {status}", "success")
        return redirect("/reviews")
    finally:
        db.close()


# --- Environments ---

@views_bp.route("/environments")
@role_required("admin")
def environments_list():
    db = next(get_db())
    try:
        envs = EnvironmentRepository(db).list_environments(
            request.user["tenant_id"], request.user["id"], request.user["role"],
        )
        envs_data = [{
            "id": e.id, "name": e.name, "env_type": e.env_type,
            "sf_instance_url": e.sf_instance_url, "capture_mode": e.capture_mode,
            "execution_policy": e.execution_policy, "max_execution_slots": e.max_execution_slots,
        } for e in envs]
        return render_template("environments/list.html", **ctx(
            active_page="settings_environments", settings_page="environments", environments=envs_data,
        ))
    finally:
        db.close()


@views_bp.route("/environments/new")
@role_required("admin")
def environments_new():
    db = next(get_db())
    try:
        conn_repo = ConnectionRepository(db)
        tid = request.user["tenant_id"]
        sf_conns = [{"id": c.id, "name": c.name, "status": c.status,
                     "config": dict(c.config) if c.config else {}}
                    for c in conn_repo.list_connections(tid, "salesforce")]
        jira_conns = [{"id": c.id, "name": c.name, "status": c.status,
                       "config": dict(c.config) if c.config else {}}
                      for c in conn_repo.list_connections(tid, "jira")]
        llm_conns = [{"id": c.id, "name": c.name, "status": c.status,
                      "config": dict(c.config) if c.config else {}}
                     for c in conn_repo.list_connections(tid, "llm")]
        return render_template("environments/new.html", **ctx(
            active_page="settings_environments", settings_page="environments",
            sf_connections=sf_conns, jira_connections=jira_conns, llm_connections=llm_conns,
        ))
    finally:
        db.close()


@views_bp.route("/environments", methods=["POST"])
@role_required("admin")
def environments_create():
    db = next(get_db())
    try:
        conn_repo = ConnectionRepository(db)
        svc = EnvironmentService(EnvironmentRepository(db), conn_repo)
        connection_id = request.form.get("connection_id", type=int)
        jira_connection_id = request.form.get("jira_connection_id", type=int)
        llm_connection_id = request.form.get("llm_connection_id", type=int)
        svc.create_environment(
            tenant_id=request.user["tenant_id"],
            name=request.form["name"],
            env_type=request.form["env_type"],
            sf_instance_url=request.form.get("sf_instance_url") or None,
            sf_api_version=request.form.get("sf_api_version") or None,
            capture_mode=request.form.get("capture_mode", "smart"),
            max_execution_slots=int(request.form.get("max_execution_slots", 2)),
            created_by=request.user["id"],
            connection_id=connection_id or None,
            jira_connection_id=jira_connection_id or None,
            llm_connection_id=llm_connection_id or None,
        )
        return redirect("/environments")
    except ValueError as e:
        tid = request.user["tenant_id"]
        sf_conns = [{"id": c.id, "name": c.name, "status": c.status,
                     "config": dict(c.config) if c.config else {}}
                    for c in conn_repo.list_connections(tid, "salesforce")]
        jira_conns = [{"id": c.id, "name": c.name, "status": c.status,
                       "config": dict(c.config) if c.config else {}}
                      for c in conn_repo.list_connections(tid, "jira")]
        llm_conns = [{"id": c.id, "name": c.name, "status": c.status,
                      "config": dict(c.config) if c.config else {}}
                     for c in conn_repo.list_connections(tid, "llm")]
        return render_template("environments/new.html", **ctx(
            active_page="settings_environments", settings_page="environments",
            sf_connections=sf_conns, jira_connections=jira_conns,
            llm_connections=llm_conns, error=str(e),
        ))
    finally:
        db.close()


@views_bp.route("/environments/<int:env_id>")
@role_required("admin")
def environments_detail(env_id):
    db = next(get_db())
    try:
        env = EnvironmentRepository(db).get_environment(env_id, request.user["tenant_id"])
        if not env:
            return redirect("/environments")
        env_data = {
            "id": env.id, "name": env.name, "env_type": env.env_type,
            "sf_instance_url": env.sf_instance_url, "sf_api_version": env.sf_api_version,
            "capture_mode": env.capture_mode, "execution_policy": env.execution_policy,
            "max_execution_slots": env.max_execution_slots,
            "cleanup_mandatory": env.cleanup_mandatory,
        }

        # R3: per-category sync status for the current meta_version
        sync_statuses = {}
        meta_version_id = env.current_meta_version_id
        if meta_version_id:
            from primeqa.metadata.models import MetaSyncStatus
            rows = db.query(MetaSyncStatus).filter(
                MetaSyncStatus.meta_version_id == meta_version_id,
            ).all()
            for r in rows:
                sync_statuses[r.category] = {
                    "status": r.status,
                    "items_count": r.items_count,
                    "error_message": r.error_message,
                    "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                }

        return render_template("environments/detail.html", **ctx(
            active_page="settings_environments", settings_page="environments",
            breadcrumb_section="Environments", breadcrumb_item=env.name,
            env=env_data, message=request.args.get("message"),
            sync_statuses=sync_statuses, meta_version_id=meta_version_id,
        ))
    finally:
        db.close()


@views_bp.route("/environments/<int:env_id>/edit", methods=["GET"])
@role_required("admin")
def environments_edit(env_id):
    db = next(get_db())
    try:
        env = EnvironmentRepository(db).get_environment(env_id, request.user["tenant_id"])
        if not env:
            return redirect("/environments")
        env_data = {
            "id": env.id, "name": env.name, "env_type": env.env_type,
            "capture_mode": env.capture_mode, "execution_policy": env.execution_policy,
            "max_execution_slots": env.max_execution_slots, "cleanup_mandatory": env.cleanup_mandatory,
        }
        return render_template("environments/edit.html", **ctx(
            active_page="settings_environments", settings_page="environments",
            breadcrumb_section="Environments", breadcrumb_item=f"Edit {env.name}",
            env=env_data, error=None,
        ))
    finally:
        db.close()


@views_bp.route("/environments/<int:env_id>/edit", methods=["POST"])
@role_required("admin")
def environments_update(env_id):
    from flask import flash
    db = next(get_db())
    try:
        svc = EnvironmentService(EnvironmentRepository(db))
        svc.update_environment(env_id, request.user["tenant_id"], {
            "name": request.form.get("name"),
            "env_type": request.form.get("env_type"),
            "capture_mode": request.form.get("capture_mode"),
            "execution_policy": request.form.get("execution_policy"),
            "max_execution_slots": int(request.form.get("max_execution_slots", 2)),
            "cleanup_mandatory": "cleanup_mandatory" in request.form,
        })
        flash("Environment updated successfully", "success")
        return redirect(f"/environments/{env_id}")
    except ValueError as e:
        flash(str(e), "error")
        return redirect(f"/environments/{env_id}/edit")
    finally:
        db.close()


@views_bp.route("/environments/<int:env_id>/test-connection", methods=["POST"])
@role_required("admin")
def environments_test_connection(env_id):
    from flask import flash
    db = next(get_db())
    try:
        conn_repo = ConnectionRepository(db)
        env_repo = EnvironmentRepository(db)
        env = env_repo.get_environment(env_id, request.user["tenant_id"])
        if not env:
            flash("Environment not found", "error")
            return redirect("/environments")
        if env.connection_id:
            svc = ConnectionService(conn_repo)
            result = svc.test_connection(env.connection_id, request.user["tenant_id"])
            if result.get("status") == "connected":
                flash("Connection successful!", "success")
            else:
                flash(f"Connection failed: {result.get('detail', 'Unknown error')}", "error")
        else:
            flash("No Salesforce connection linked to this environment", "error")
    except Exception as e:
        flash(f"Error: {e}", "error")
    finally:
        db.close()
    return redirect(f"/environments/{env_id}")


@views_bp.route("/environments/<int:env_id>/refresh-metadata", methods=["POST"])
@role_required("admin", "superadmin")
def environments_refresh_metadata(env_id):
    """Queue a metadata-sync job and redirect to the progress page.

    This used to run the sync inline on the web worker; now we just INSERT
    a queued meta_version + meta_sync_status rows and return in ~100ms.
    The Railway worker service picks up queued rows and executes.
    """
    from flask import flash
    db = next(get_db())
    try:
        from primeqa.metadata.models import MetaVersion, MetaSyncStatus
        from primeqa.metadata.sync_engine import ALL_CATEGORIES
        from datetime import datetime, timezone as _tz

        env_repo = EnvironmentRepository(db)
        env = env_repo.get_environment(env_id, request.user["tenant_id"])
        if not env:
            flash("Environment not found", "error"); return redirect("/environments")
        if not env.connection_id:
            flash("No Salesforce connection linked \u2014 cannot refresh metadata", "error")
            return redirect(f"/environments/{env_id}")

        # Single-flight: refuse if a sync is already queued/running for this env
        active = db.query(MetaVersion).filter(
            MetaVersion.environment_id == env_id,
            MetaVersion.status.in_(("queued", "in_progress")),
        ).order_by(MetaVersion.queued_at.desc().nullslast()).first()
        if active:
            flash("A sync is already running for this environment. "
                  "View its progress or cancel it first.", "warning")
            return redirect(f"/environments/{env_id}/sync/{active.id}")

        # Pick next unused version label (matches earlier fix for failed v1)
        all_labels = {row[0] for row in db.query(MetaVersion.version_label)
                                          .filter(MetaVersion.environment_id == env_id)
                                          .all()}
        n = 1
        while f"v{n}" in all_labels:
            n += 1

        cats_raw = request.form.getlist("categories") or list(ALL_CATEGORIES)
        requested_cats = [c for c in cats_raw if c in ALL_CATEGORIES] or list(ALL_CATEGORIES)

        now = datetime.now(_tz.utc)
        mv = MetaVersion(
            environment_id=env_id,
            version_label=f"v{n}",
            status="queued",
            queued_at=now,
            triggered_by=request.user["id"],
            categories_requested=requested_cats,
        )
        db.add(mv); db.commit(); db.refresh(mv)

        # Seed status rows so the progress page has content immediately
        for cat in ALL_CATEGORIES:
            status = "pending" if cat in requested_cats else "skipped"
            db.add(MetaSyncStatus(meta_version_id=mv.id, category=cat, status=status))
        db.commit()

        return redirect(f"/environments/{env_id}/sync/{mv.id}", code=303)
    except Exception as e:
        flash(f"Could not queue metadata sync: {e}", "error")
        return redirect(f"/environments/{env_id}")
    finally:
        db.close()


@views_bp.route("/environments/<int:env_id>/sync/<int:mv_id>")
@role_required("admin", "superadmin")
def environments_sync_progress(env_id, mv_id):
    """Metadata-sync progress page. Reads from DB, opens SSE for live updates."""
    db = next(get_db())
    try:
        from primeqa.metadata.models import MetaVersion, MetaSyncStatus
        from primeqa.metadata.sync_engine import ALL_CATEGORIES
        env_repo = EnvironmentRepository(db)
        env = env_repo.get_environment(env_id, request.user["tenant_id"])
        if not env:
            return redirect("/environments")
        mv = db.query(MetaVersion).filter(
            MetaVersion.id == mv_id, MetaVersion.environment_id == env_id,
        ).first()
        if not mv:
            return redirect(f"/environments/{env_id}")
        rows = db.query(MetaSyncStatus).filter_by(meta_version_id=mv_id).all()
        # Ensure consistent order (DAG order)
        cat_order = {c: i for i, c in enumerate(ALL_CATEGORIES)}
        rows = sorted(rows, key=lambda r: cat_order.get(r.category, 99))

        # ETA: rolling-avg duration per category.
        #  - Use only the **last 5 successful** rows (parallel-describe era,
        #    F1+) so pre-F1 slow runs stop polluting the baseline.
        #  - Default of 10s when we have no history (was 30s; F1 reality is
        #    closer to 5-15s per category on typical orgs).
        #  - Cap at 60s per category so a single outlier row can't spike
        #    the ETA into the tens of minutes.
        from sqlalchemy import func as sa_func
        DEFAULT_CAT_MS = 10_000
        MAX_CAT_MS = 60_000
        avg_durations = {}
        for cat in ALL_CATEGORIES:
            recent = (db.query(
                    sa_func.extract(
                        "epoch",
                        MetaSyncStatus.completed_at - MetaSyncStatus.started_at,
                    ) * 1000
                )
                .join(MetaVersion, MetaSyncStatus.meta_version_id == MetaVersion.id)
                .filter(
                    MetaVersion.environment_id == env_id,
                    MetaSyncStatus.category == cat,
                    MetaSyncStatus.status == "complete",
                    MetaSyncStatus.started_at.isnot(None),
                    MetaSyncStatus.completed_at.isnot(None),
                )
                .order_by(MetaSyncStatus.completed_at.desc())
                .limit(5)
                .all())
            if recent:
                samples = [float(r[0]) for r in recent if r[0] is not None]
                avg_ms = int(sum(samples) / len(samples)) if samples else DEFAULT_CAT_MS
            else:
                avg_ms = DEFAULT_CAT_MS
            avg_durations[cat] = min(avg_ms, MAX_CAT_MS)

        mv_data = {
            "id": mv.id, "version_label": mv.version_label, "status": mv.status,
            "env_id": env.id, "env_name": env.name,
            "queued_at": mv.queued_at.isoformat() if mv.queued_at else None,
            "started_at": mv.started_at.isoformat() if mv.started_at else None,
            "completed_at": mv.completed_at.isoformat() if mv.completed_at else None,
            "cancel_requested": mv.cancel_requested,
            "categories_requested": mv.categories_requested or list(ALL_CATEGORIES),
            "worker_id": mv.worker_id,
            "triggered_by": mv.triggered_by,
            "parent_meta_version_id": mv.parent_meta_version_id,
        }
        rows_data = [{
            "category": r.category, "status": r.status,
            "items_count": r.items_count or 0,
            "error_message": r.error_message,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        } for r in rows]
        return render_template("environments/sync_progress.html", **ctx(
            active_page="settings_environments", settings_page="environments",
            env=env, mv=mv_data, rows=rows_data, avg_durations=avg_durations,
        ))
    finally:
        db.close()


@views_bp.route("/environments/<int:env_id>/quick-refresh", methods=["POST"])
@role_required("admin", "superadmin")
def environments_quick_refresh(env_id):
    """F2 quick-refresh: queue a delta-sync meta_version pinned to the
    current's completed_at. Skips the objects list fetch's describe loop
    except for objects whose fields changed since the cutoff \u2014 typical
    outcome is a handful of describes + 3 filtered Tooling queries,
    finishing in seconds."""
    from flask import flash
    from datetime import datetime, timezone as _tz
    db = next(get_db())
    try:
        from primeqa.metadata.models import MetaVersion, MetaSyncStatus
        from primeqa.metadata.repository import MetadataRepository
        from primeqa.metadata.sync_engine import ALL_CATEGORIES

        meta_repo = MetadataRepository(db)
        current = meta_repo.get_current_version(env_id)
        if not current or not current.completed_at:
            flash("No prior sync to delta against \u2014 use Refresh metadata for a full sync.",
                  "warning")
            return redirect(f"/environments/{env_id}")

        # Single-flight guard
        active = db.query(MetaVersion).filter(
            MetaVersion.environment_id == env_id,
            MetaVersion.status.in_(("queued", "in_progress")),
        ).first()
        if active:
            return redirect(f"/environments/{env_id}/sync/{active.id}")

        # Next version label
        all_labels = {row[0] for row in db.query(MetaVersion.version_label)
                                          .filter(MetaVersion.environment_id == env_id).all()}
        n = 1
        while f"v{n}" in all_labels:
            n += 1

        mv = MetaVersion(
            environment_id=env_id,
            version_label=f"v{n}",
            status="queued",
            queued_at=datetime.now(_tz.utc),
            triggered_by=request.user["id"],
            categories_requested=list(ALL_CATEGORIES),  # refresh everything, filtered by delta
            parent_meta_version_id=current.id,
            delta_since_ts=current.completed_at,
        )
        db.add(mv); db.commit(); db.refresh(mv)

        for cat in ALL_CATEGORIES:
            db.add(MetaSyncStatus(meta_version_id=mv.id, category=cat, status="pending"))
        db.commit()

        return redirect(f"/environments/{env_id}/sync/{mv.id}", code=303)
    finally:
        db.close()


@views_bp.route("/environments/<int:env_id>/sync/<int:mv_id>/cancel", methods=["POST"])
@role_required("admin", "superadmin")
def environments_sync_cancel(env_id, mv_id):
    """User-initiated cancel. Flips cancel_requested; worker checks between
    categories and bails cleanly."""
    from flask import flash
    db = next(get_db())
    try:
        from primeqa.metadata.models import MetaVersion
        mv = db.query(MetaVersion).filter(
            MetaVersion.id == mv_id, MetaVersion.environment_id == env_id,
        ).first()
        if not mv:
            flash("Sync not found", "error")
            return redirect(f"/environments/{env_id}")
        if mv.status not in ("queued", "in_progress"):
            flash(f"Sync is already {mv.status} \u2014 nothing to cancel", "warning")
            return redirect(f"/environments/{env_id}/sync/{mv_id}")
        mv.cancel_requested = True
        # If still queued (no worker has claimed it), cancel immediately
        if mv.status == "queued":
            from datetime import datetime, timezone
            mv.status = "cancelled"
            mv.completed_at = datetime.now(timezone.utc)
        db.commit()
        flash("Cancel requested. Worker will stop at the next category boundary.", "success")
    finally:
        db.close()
    return redirect(f"/environments/{env_id}/sync/{mv_id}")


@views_bp.route("/environments/<int:env_id>/sync/<int:mv_id>/retry", methods=["POST"])
@role_required("admin", "superadmin")
def environments_sync_retry(env_id, mv_id):
    """Queue a new sync for the failed + skipped_parent_failed categories
    of a prior sync, linked via parent_meta_version_id."""
    from flask import flash
    db = next(get_db())
    try:
        from primeqa.metadata.models import MetaVersion, MetaSyncStatus
        from primeqa.metadata.sync_engine import ALL_CATEGORIES
        from datetime import datetime, timezone

        parent = db.query(MetaVersion).filter(
            MetaVersion.id == mv_id, MetaVersion.environment_id == env_id,
        ).first()
        if not parent:
            return redirect(f"/environments/{env_id}")

        # Single-flight
        active = db.query(MetaVersion).filter(
            MetaVersion.environment_id == env_id,
            MetaVersion.status.in_(("queued", "in_progress")),
        ).first()
        if active:
            flash("A sync is already running for this environment.", "warning")
            return redirect(f"/environments/{env_id}/sync/{active.id}")

        # Pick categories to retry
        retry_cats = [
            r.category for r in db.query(MetaSyncStatus).filter_by(meta_version_id=mv_id).all()
            if r.status in ("failed", "skipped_parent_failed", "cancelled")
        ]
        if not retry_cats:
            flash("No failed categories to retry. Use Start over for a full refresh.", "warning")
            return redirect(f"/environments/{env_id}/sync/{mv_id}")

        # Next unused version label
        all_labels = {row[0] for row in db.query(MetaVersion.version_label)
                                          .filter(MetaVersion.environment_id == env_id).all()}
        n = 1
        while f"v{n}" in all_labels:
            n += 1

        now = datetime.now(timezone.utc)
        new_mv = MetaVersion(
            environment_id=env_id, version_label=f"v{n}",
            status="queued", queued_at=now,
            triggered_by=request.user["id"],
            categories_requested=retry_cats,
            parent_meta_version_id=mv_id,
        )
        db.add(new_mv); db.commit(); db.refresh(new_mv)

        # Seed status rows
        for cat in ALL_CATEGORIES:
            status = "pending" if cat in retry_cats else "skipped"
            db.add(MetaSyncStatus(meta_version_id=new_mv.id, category=cat, status=status))
        db.commit()
        return redirect(f"/environments/{env_id}/sync/{new_mv.id}", code=303)
    finally:
        db.close()


@views_bp.route("/environments/<int:env_id>/delete", methods=["POST"])
@role_required("admin")
def environments_delete(env_id):
    from flask import flash
    db = next(get_db())
    try:
        env_repo = EnvironmentRepository(db)
        env_repo.update_environment(env_id, request.user["tenant_id"], {"is_active": False})
        flash("Environment deactivated successfully", "success")
    except Exception as e:
        flash(str(e), "error")
    finally:
        db.close()
    return redirect("/environments")


# --- Users ---

@views_bp.route("/users")
@role_required("admin")
def users_list():
    db = next(get_db())
    try:
        from flask import flash
        search = request.args.get("search", "").strip()
        sort = request.args.get("sort", "full_name")
        order = request.args.get("order", "asc")
        page = request.args.get("page", 1, type=int)
        per_page = 20

        svc = AuthService(UserRepository(db), RefreshTokenRepository(db))
        all_users = svc.list_users(request.user["tenant_id"])

        if search:
            all_users = [u for u in all_users if search.lower() in u["full_name"].lower() or search.lower() in u["email"].lower()]

        reverse = order == "desc"
        if sort in ("full_name", "email", "role"):
            all_users.sort(key=lambda u: (u.get(sort) or "").lower(), reverse=reverse)

        total = len(all_users)
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, total_pages)
        paginated = all_users[(page - 1) * per_page:page * per_page]

        extra = ""
        if search:
            extra += f"&search={search}"
        if sort != "full_name":
            extra += f"&sort={sort}"
        if order != "asc":
            extra += f"&order={order}"

        return render_template("users/list.html", **ctx(
            active_page="settings_users", settings_page="users",
            breadcrumb_section="Users",
            users=paginated, total=total, page=page, total_pages=total_pages,
            search=search, sort=sort, order=order, extra_params=extra,
        ))
    finally:
        db.close()


@views_bp.route("/users/new", methods=["GET"])
@role_required("admin")
def users_new():
    return render_template("users/form.html", **ctx(
        active_page="settings_users", settings_page="users",
        breadcrumb_section="Users", breadcrumb_item="New User",
        edit_user=None, error=None,
    ))


@views_bp.route("/users/new", methods=["POST"])
@role_required("admin")
def users_create():
    from flask import flash
    db = next(get_db())
    try:
        svc = AuthService(UserRepository(db), RefreshTokenRepository(db))
        svc.create_user(
            tenant_id=request.user["tenant_id"],
            email=request.form["email"],
            password=request.form["password"],
            full_name=request.form["full_name"],
            role=request.form["role"],
        )
        flash(f"User {request.form['full_name']} created successfully", "success")
        return redirect("/users")
    except ValueError as e:
        return render_template("users/form.html", **ctx(
            active_page="settings_users", settings_page="users",
            breadcrumb_section="Users", breadcrumb_item="New User",
            edit_user=None, error=str(e),
        ))
    finally:
        db.close()


@views_bp.route("/users/<int:user_id>/edit", methods=["GET"])
@role_required("admin")
def users_edit(user_id):
    db = next(get_db())
    try:
        user_repo = UserRepository(db)
        edit_user = user_repo.get_user_by_id(user_id)
        if not edit_user or edit_user.tenant_id != request.user["tenant_id"]:
            return redirect("/users")
        user_data = {
            "id": edit_user.id, "email": edit_user.email,
            "full_name": edit_user.full_name, "role": edit_user.role,
            "is_active": edit_user.is_active,
        }
        return render_template("users/form.html", **ctx(
            active_page="settings_users", settings_page="users",
            breadcrumb_section="Users", breadcrumb_item=edit_user.full_name,
            edit_user=user_data, error=None,
        ))
    finally:
        db.close()


@views_bp.route("/users/<int:user_id>/edit", methods=["POST"])
@role_required("admin")
def users_update(user_id):
    from flask import flash
    db = next(get_db())
    try:
        svc = AuthService(UserRepository(db), RefreshTokenRepository(db))
        updates = {
            "full_name": request.form.get("full_name"),
            "role": request.form.get("role"),
            "is_active": "is_active" in request.form,
        }
        svc.update_user(user_id, **updates)
        flash("User updated successfully", "success")
        return redirect("/users")
    except ValueError as e:
        flash(str(e), "error")
        return redirect(f"/users/{user_id}/edit")
    finally:
        db.close()


@views_bp.route("/users/<int:user_id>/toggle-active", methods=["POST"])
@role_required("admin")
def users_toggle_active(user_id):
    from flask import flash
    db = next(get_db())
    try:
        # Migration 039: self-deactivation prevention. An admin who
        # deactivates their own account would be locked out on next page
        # load; the superadmin escape hatch is tenant-wide, not
        # user-specific. Block at the view layer.
        if user_id == request.user["id"] and request.user.get("role") != "superadmin":
            flash("You cannot deactivate your own account.", "error")
            return redirect("/settings/users")
        user_repo = UserRepository(db)
        user = user_repo.get_user_by_id(user_id)
        if user and user.tenant_id == request.user["tenant_id"]:
            new_status = not user.is_active
            svc = AuthService(user_repo, RefreshTokenRepository(db))
            svc.update_user(user_id, is_active=new_status)
            flash(f"User {'activated' if new_status else 'deactivated'} successfully", "success")
    except ValueError as e:
        flash(str(e), "error")
    finally:
        db.close()
    return redirect("/settings/users")


# --- Permission-set admin UI (migration 039+) -----------------------------

# Permission category mapping used in the user detail page and the
# assignment modal. Kept in one place so the UI groups consistently
# wherever permissions are listed.
PERMISSION_CATEGORIES = {
    "Execution": [
        "connect_personal_org", "run_single_ticket", "run_sprint",
        "run_suite", "rerun_own_ticket", "trigger_metadata_sync",
    ],
    "Reporting": [
        "view_own_results", "view_own_diagnosis", "view_all_results",
        "view_all_diagnosis", "view_all_results_summary",
        "view_intelligence_report", "view_intelligence_summary",
        "view_trends", "view_dashboard", "view_knowledge_attribution",
    ],
    "Test Management": [
        "review_test_cases", "manage_test_suites", "view_test_library",
        "view_coverage_map", "view_suite_quality_gates",
        "share_dashboard", "revoke_shared_links", "approve_release",
    ],
    "Administration": [
        "manage_environments", "manage_jira_connections", "manage_sf_connections",
        "manage_ai_models", "manage_users", "manage_permission_sets",
        "manage_knowledge", "manage_skills", "view_audit_log", "view_api_usage",
        "configure_scheduled_runs", "manage_rate_limits", "override_quality_gate",
        "view_all_personal_environments", "delete_any_personal_environment",
    ],
    "API & Automation": [
        "api_authenticate", "webhook_notifications",
    ],
}


def _settings_users_payload(db, tenant_id, search: str = ""):
    """Build the list-page payload: users + their assigned permission sets."""
    from primeqa.core.models import User
    from primeqa.core.permissions import PermissionSet, UserPermissionSet

    users = (db.query(User)
             .filter_by(tenant_id=tenant_id)
             .order_by(User.is_active.desc(), User.full_name.asc())
             .all())
    if search:
        s = search.lower()
        users = [u for u in users
                 if s in (u.full_name or "").lower() or s in (u.email or "").lower()]

    # One query: all assignments for these users.
    uids = [u.id for u in users]
    ps_map: dict[int, list[PermissionSet]] = {}
    if uids:
        rows = (db.query(UserPermissionSet, PermissionSet)
                .join(PermissionSet, PermissionSet.id == UserPermissionSet.permission_set_id)
                .filter(UserPermissionSet.user_id.in_(uids))
                .order_by(PermissionSet.is_base.desc(), PermissionSet.name.asc())
                .all())
        for ups, ps in rows:
            ps_map.setdefault(ups.user_id, []).append(ps)
    payload = []
    for u in users:
        sets = ps_map.get(u.id, [])
        base = next((p for p in sets if p.is_base), None)
        extras = [p for p in sets if not p.is_base]
        payload.append({
            "id": u.id,
            "full_name": u.full_name,
            "email": u.email,
            "is_active": u.is_active,
            "role": u.role,
            "base_set": base,
            "extra_sets": extras,
        })
    return payload


@views_bp.route("/settings/users")
@login_required
def settings_users():
    """List all users in the tenant with their assigned permission sets."""
    from primeqa.core.permissions import require_page_permission

    @require_page_permission("manage_users")
    def _render():
        db = next(get_db())
        try:
            search = (request.args.get("search") or "").strip()
            users = _settings_users_payload(
                db, request.user["tenant_id"], search=search,
            )
            return render_template("settings/users_list.html", **ctx(
                active_page="settings_users", settings_page="users",
                breadcrumb_section="Users",
                users=users, search=search,
            ))
        finally:
            db.close()

    return _render()


@views_bp.route("/settings/users/<int:user_id>")
@login_required
def settings_user_detail(user_id):
    """User detail: info, assigned sets, effective permissions by category."""
    from flask import abort, flash
    from primeqa.core.models import User
    from primeqa.core.permissions import (
        PermissionSet, UserPermissionSet, get_effective_permissions,
        require_page_permission,
    )
    from primeqa.core.navigation import get_landing_page

    @require_page_permission("manage_users")
    def _render():
        db = next(get_db())
        try:
            u = db.query(User).filter_by(id=user_id).first()
            if u is None or u.tenant_id != request.user["tenant_id"]:
                flash("User not found.", "error")
                return redirect("/settings/users")

            # Assigned sets in display order (base first).
            rows = (db.query(UserPermissionSet, PermissionSet)
                    .join(PermissionSet,
                          PermissionSet.id == UserPermissionSet.permission_set_id)
                    .filter(UserPermissionSet.user_id == u.id)
                    .order_by(PermissionSet.is_base.desc(),
                              PermissionSet.name.asc())
                    .all())
            assigned = [{
                "id": ps.id, "name": ps.name, "api_name": ps.api_name,
                "is_base": ps.is_base, "is_system": ps.is_system,
                "assigned_at": ups.assigned_at,
                "contains_admin": "manage_users" in (ps.permissions or []),
            } for (ups, ps) in rows]

            # Available (unassigned) sets for the assignment modal.
            assigned_ids = {a["id"] for a in assigned}
            all_sets = (db.query(PermissionSet)
                        .filter_by(tenant_id=u.tenant_id)
                        .order_by(PermissionSet.is_base.desc(),
                                  PermissionSet.is_system.desc(),
                                  PermissionSet.name.asc())
                        .all())

            # Effective permissions grouped by category, with attribution
            # (which assigned set introduced each one).
            effective = get_effective_permissions(u.id, db)
            attribution: dict[str, str] = {}
            for (ups, ps) in rows:
                for p in (ps.permissions or []):
                    # Base sets win the attribution tie.
                    if p not in attribution or ps.is_base:
                        attribution[p] = ps.name
            grouped: list[dict] = []
            for cat, perms in PERMISSION_CATEGORIES.items():
                entries = [{"perm": p, "source": attribution.get(p, "—")}
                           for p in perms if p in effective]
                if entries:
                    grouped.append({"category": cat, "entries": entries})
            # Fallback for any permissions not in our category map.
            uncategorized = [p for p in effective
                             if not any(p in v for v in PERMISSION_CATEGORIES.values())]
            if uncategorized:
                grouped.append({
                    "category": "Other",
                    "entries": [{"perm": p, "source": attribution.get(p, "—")}
                                for p in sorted(uncategorized)],
                })

            preferred = u.preferred_landing_page
            computed = get_landing_page(
                effective, preferred=preferred,
                is_superadmin=(u.role == "superadmin"),
            )

            is_self = (u.id == request.user["id"])

            return render_template("settings/user_detail.html", **ctx(
                active_page="settings_users", settings_page="users",
                breadcrumb_section="Users",
                breadcrumb_section_url="settings/users",
                breadcrumb_item=u.full_name,
                edit_user=u, assigned=assigned, all_sets=all_sets,
                grouped=grouped, landing_preferred=preferred,
                landing_computed=computed, is_self=is_self,
            ))
        finally:
            db.close()

    return _render()


@views_bp.route("/settings/permission-sets")
@login_required
def settings_permission_sets():
    """List all permission sets in the tenant: base, granular, custom."""
    from primeqa.core.permissions import (
        PermissionSet, UserPermissionSet, require_page_permission,
    )
    from sqlalchemy import func as sf

    @require_page_permission("manage_permission_sets")
    def _render():
        db = next(get_db())
        try:
            sets = (db.query(PermissionSet)
                    .filter_by(tenant_id=request.user["tenant_id"])
                    .order_by(PermissionSet.is_base.desc(),
                              PermissionSet.is_system.desc(),
                              PermissionSet.name.asc())
                    .all())
            # User-count per set, in one query.
            counts = dict((r[0], r[1]) for r in (
                db.query(UserPermissionSet.permission_set_id,
                         sf.count(UserPermissionSet.user_id))
                .group_by(UserPermissionSet.permission_set_id)
                .all()))
            rows = [{
                "id": ps.id, "name": ps.name, "api_name": ps.api_name,
                "description": ps.description,
                "is_base": ps.is_base, "is_system": ps.is_system,
                "permissions_count": len(ps.permissions or []),
                "users_count": counts.get(ps.id, 0),
            } for ps in sets]
            base = [r for r in rows if r["is_base"] and r["is_system"]]
            granular = [r for r in rows if not r["is_base"] and r["is_system"]]
            custom = [r for r in rows if not r["is_system"]]
            return render_template("settings/permission_sets_list.html", **ctx(
                active_page="settings_permission_sets",
                settings_page="permission_sets",
                breadcrumb_section="Permission Sets",
                base_sets=base, granular_sets=granular, custom_sets=custom,
            ))
        finally:
            db.close()

    return _render()


# --- Permission-set assignment APIs ---------------------------------------
# NOTE: these use require_auth (not login_required) so Bearer tokens from
# /api/auth/login work as the canonical auth path — matches the rest of
# /api/* and avoids the test-client-cookie-leak behaviour on mixed auth.

from primeqa.core.auth import require_auth as _require_auth_api


@views_bp.route("/api/users/<int:user_id>/permission-sets", methods=["POST"])
@_require_auth_api
def api_assign_permission_sets(user_id):
    """Assign one or more permission sets to a user.

    Body: {"permission_set_ids": [1, 5, 12]}
    Idempotent — already-assigned sets are skipped silently.
    """
    from primeqa.core.models import User
    from primeqa.core.permissions import (
        PermissionSet, assign_permission_set, require_permission,
    )

    @require_permission("manage_users")
    def _do():
        body = request.get_json(silent=True) or {}
        raw_ids = body.get("permission_set_ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            return ({"error": {"code": "VALIDATION_ERROR",
                               "message": "permission_set_ids must be a non-empty list"}}, 400)
        try:
            ids = [int(x) for x in raw_ids]
        except (TypeError, ValueError):
            return ({"error": {"code": "VALIDATION_ERROR",
                               "message": "permission_set_ids must be integers"}}, 400)

        db = next(get_db())
        try:
            u = db.query(User).filter_by(id=user_id).first()
            if u is None or u.tenant_id != request.user["tenant_id"]:
                return ({"error": {"code": "NOT_FOUND", "message": "User not found"}}, 404)
            # Tenant-scope all requested sets.
            sets = (db.query(PermissionSet)
                    .filter(PermissionSet.id.in_(ids),
                            PermissionSet.tenant_id == u.tenant_id)
                    .all())
            found_ids = {ps.id for ps in sets}
            missing = [i for i in ids if i not in found_ids]
            if missing:
                return ({"error": {"code": "VALIDATION_ERROR",
                                   "message": f"Unknown permission set ids: {missing}"}},
                        400)
            added = 0
            for ps in sets:
                if assign_permission_set(u.id, ps.id, db,
                                         assigned_by=request.user["id"]):
                    added += 1
            db.commit()
            return ({"assigned": added, "requested": len(ids)}, 200)
        finally:
            db.close()

    return _do()


@views_bp.route("/api/users/<int:user_id>/permission-sets/<int:pset_id>",
                methods=["DELETE"])
@_require_auth_api
def api_revoke_permission_set(user_id, pset_id):
    """Revoke a permission-set assignment from a user.

    Self-protect: an admin can't remove their own manage_users grant.
    """
    from primeqa.core.models import User
    from primeqa.core.permissions import (
        PermissionSet, require_permission, revoke_permission_set,
    )

    @require_permission("manage_users")
    def _do():
        db = next(get_db())
        try:
            u = db.query(User).filter_by(id=user_id).first()
            if u is None or u.tenant_id != request.user["tenant_id"]:
                return ({"error": {"code": "NOT_FOUND", "message": "User not found"}}, 404)
            ps = db.query(PermissionSet).filter_by(id=pset_id).first()
            if ps is None or ps.tenant_id != u.tenant_id:
                return ({"error": {"code": "NOT_FOUND", "message": "Permission set not found"}}, 404)
            # Self-protect: prevent lock-out.
            if (u.id == request.user["id"]
                    and "manage_users" in (ps.permissions or [])
                    and request.user.get("role") != "superadmin"):
                return ({"error": {
                    "code": "SELF_ADMIN_REVOKE",
                    "message": "Cannot remove your own admin permissions.",
                }}, 400)
            removed = revoke_permission_set(u.id, ps.id, db)
            db.commit()
            if not removed:
                return ({"error": {"code": "NOT_FOUND",
                                   "message": "Assignment not found"}}, 404)
            return ("", 204)
        finally:
            db.close()

    return _do()


@views_bp.route("/api/users/<int:user_id>/deactivate", methods=["POST"])
@_require_auth_api
def api_deactivate_user(user_id):
    """Deactivate a user. Blocks self-deactivation."""
    from primeqa.core.models import User
    from primeqa.core.permissions import require_permission

    @require_permission("manage_users")
    def _do():
        if user_id == request.user["id"] and request.user.get("role") != "superadmin":
            return ({"error": {"code": "SELF_DEACTIVATE",
                               "message": "Cannot deactivate your own account."}}, 400)
        db = next(get_db())
        try:
            u = db.query(User).filter_by(id=user_id).first()
            if u is None or u.tenant_id != request.user["tenant_id"]:
                return ({"error": {"code": "NOT_FOUND", "message": "User not found"}}, 404)
            u.is_active = False
            db.commit()
            return ("", 204)
        finally:
            db.close()

    return _do()


@views_bp.route("/api/users/<int:user_id>/activate", methods=["POST"])
@_require_auth_api
def api_activate_user(user_id):
    """Re-activate a user."""
    from primeqa.core.models import User
    from primeqa.core.permissions import require_permission

    @require_permission("manage_users")
    def _do():
        db = next(get_db())
        try:
            u = db.query(User).filter_by(id=user_id).first()
            if u is None or u.tenant_id != request.user["tenant_id"]:
                return ({"error": {"code": "NOT_FOUND", "message": "User not found"}}, 404)
            u.is_active = True
            db.commit()
            return ("", 204)
        finally:
            db.close()

    return _do()


# --- Impacts ---

@views_bp.route("/impacts")
@role_required("admin", "tester")
def impacts_list():
    db = next(get_db())
    try:
        from primeqa.test_management.repository import MetadataImpactRepository
        repo = MetadataImpactRepository(db)
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 20, type=int)
        q = (request.args.get("q") or "").strip()
        sort = request.args.get("sort", "created_at")
        order = request.args.get("order", "desc")
        show_deleted = request.args.get("deleted", "").lower() in ("1", "true", "yes")
        filters = {}
        resolution = request.args.get("resolution", "pending")
        if resolution:
            filters["resolution"] = resolution
        if request.args.get("impact_type"):
            filters["impact_type"] = request.args.get("impact_type")

        try:
            result = repo.list_page(
                request.user["tenant_id"],
                page=page, per_page=per_page, q=q, sort=sort, order=order,
                filters=filters, include_deleted=show_deleted,
            )
            impacts = result.items
            meta = {"total": result.total, "page": result.page,
                    "per_page": result.per_page, "total_pages": result.total_pages}
            query_error = None
        except Exception as e:
            impacts, meta, query_error = [], {"total": 0, "page": 1, "per_page": per_page, "total_pages": 0}, str(e)

        impacts_data = [{
            "id": i.id, "test_case_id": i.test_case_id,
            "impact_type": i.impact_type, "entity_ref": i.entity_ref,
            "resolution": i.resolution,
            "created_at": i.created_at.isoformat() if i.created_at else "",
        } for i in impacts]
        return render_template("impacts/list.html", **ctx(
            active_page="impacts", impacts=impacts_data,
            meta=meta, search=q, resolution_filter=resolution,
            show_deleted=show_deleted, query_error=query_error,
        ))
    finally:
        db.close()


@views_bp.route("/impacts/<int:impact_id>")
@role_required("admin", "tester", "superadmin")
def impacts_detail(impact_id):
    """Detail page for one metadata impact \u2014 shows the entity, change
    details (diff), affected test case, and resolve/regenerate actions."""
    db = next(get_db())
    try:
        from primeqa.test_management.repository import (
            MetadataImpactRepository, TestCaseRepository,
        )
        from primeqa.test_management.models import MetadataImpact, TestCase

        tid = request.user["tenant_id"]
        impact_repo = MetadataImpactRepository(db)
        impact = impact_repo.get_impact(impact_id, tid, include_deleted=True)
        if not impact:
            return redirect("/impacts")

        tc = TestCaseRepository(db).get_test_case(impact.test_case_id, tid)
        tc_data = None
        if tc:
            tc_data = {
                "id": tc.id, "title": tc.title, "status": tc.status,
                "visibility": tc.visibility,
            }

        # Meta version labels for context
        from primeqa.metadata.repository import MetadataRepository
        meta_repo = MetadataRepository(db)
        new_mv = meta_repo.get_version(impact.new_meta_version_id)
        prev_mv = meta_repo.get_version(impact.prev_meta_version_id)

        impact_data = {
            "id": impact.id, "impact_type": impact.impact_type,
            "entity_ref": impact.entity_ref,
            "resolution": impact.resolution,
            "test_case_id": impact.test_case_id,
            "change_details": impact.change_details or {},
            "resolved_by": impact.resolved_by,
            "resolved_at": impact.resolved_at.isoformat() if impact.resolved_at else None,
            "created_at": impact.created_at.isoformat() if impact.created_at else "",
            "deleted_at": impact.deleted_at.isoformat() if getattr(impact, "deleted_at", None) else None,
            "new_meta_version_label": new_mv.version_label if new_mv else None,
            "prev_meta_version_label": prev_mv.version_label if prev_mv else None,
        }
        return render_template("impacts/detail.html", **ctx(
            active_page="impacts", impact=impact_data, test_case=tc_data,
        ))
    finally:
        db.close()


@views_bp.route("/impacts/<int:impact_id>/resolve", methods=["POST"])
@role_required("admin", "tester")
def impacts_resolve(impact_id):
    db = next(get_db())
    try:
        from primeqa.test_management.repository import MetadataImpactRepository
        repo = MetadataImpactRepository(db)
        repo.resolve_impact(impact_id, request.form["resolution"], request.user["id"])
        return redirect("/impacts")
    finally:
        db.close()


@views_bp.route("/impacts/<int:impact_id>/regenerate", methods=["POST"])
@role_required("admin", "tester")
def impacts_regenerate(impact_id):
    from flask import flash
    db = next(get_db())
    try:
        from primeqa.test_management.repository import (
            SectionRepository, RequirementRepository, TestCaseRepository,
            TestSuiteRepository, BAReviewRepository, MetadataImpactRepository,
        )
        from primeqa.test_management.service import TestManagementService
        from primeqa.metadata.repository import MetadataRepository
        svc = TestManagementService(
            SectionRepository(db), RequirementRepository(db),
            TestCaseRepository(db), TestSuiteRepository(db),
            BAReviewRepository(db), MetadataImpactRepository(db),
        )
        svc.review_repo = BAReviewRepository(db)
        result = svc.regenerate_for_impact(
            tenant_id=request.user["tenant_id"], impact_id=impact_id,
            created_by=request.user["id"],
            env_repo=EnvironmentRepository(db),
            conn_repo=ConnectionRepository(db),
            metadata_repo=MetadataRepository(db),
        )
        flash(f"Regenerated test case #{result['test_case_id']}", "success")
        return redirect(f"/test-cases/{result['test_case_id']}")
    except Exception as e:
        flash(f"Regeneration failed: {e}", "error")
    finally:
        db.close()
    return redirect("/impacts")


# --- Connections ---

@views_bp.route("/connections")
@role_required("admin")
def connections_list():
    db = next(get_db())
    try:
        svc = ConnectionService(ConnectionRepository(db))
        conns = svc.list_connections(request.user["tenant_id"])
        return render_template("connections/list.html", **ctx(
            active_page="settings_connections", settings_page="connections", connections=conns,
        ))
    finally:
        db.close()


@views_bp.route("/connections/new")
@role_required("admin")
def connections_new():
    return render_template("connections/new.html", **ctx(active_page="settings_connections", settings_page="connections", error=None))


@views_bp.route("/connections", methods=["POST"])
@role_required("admin")
def connections_create():
    db = next(get_db())
    try:
        svc = ConnectionService(ConnectionRepository(db))
        ctype = request.form.get("connection_type", "salesforce")
        config = {}
        if ctype == "salesforce":
            config = {
                "org_type": request.form.get("sf_org_type", "sandbox"),
                "auth_flow": request.form.get("sf_auth_flow", "client_credentials"),
                "instance_url": request.form.get("sf_instance_url", ""),
                "api_version": request.form.get("sf_api_version", "59.0"),
                "client_id": request.form.get("sf_client_id", ""),
                "client_secret": request.form.get("sf_client_secret", ""),
            }
            if config["auth_flow"] == "password":
                config["username"] = request.form.get("sf_username", "")
                config["password"] = request.form.get("sf_password", "")
        elif ctype == "jira":
            config = {
                "base_url": request.form.get("jira_base_url", ""),
                "auth_type": "basic",
                "username": request.form.get("jira_username", ""),
                "api_token": request.form.get("jira_api_token", ""),
            }
        elif ctype == "llm":
            config = {
                "provider": request.form.get("llm_provider", "anthropic"),
                "api_key": request.form.get("llm_api_key", ""),
                "model": request.form.get("llm_model", "claude-sonnet-4-20250514"),
            }
        svc.create_connection(
            request.user["tenant_id"], ctype,
            request.form.get("name", ""), config, request.user["id"],
        )
        return redirect("/connections")
    except ValueError as e:
        return render_template("connections/new.html", **ctx(
            active_page="settings_connections", settings_page="connections", error=str(e),
        ))
    finally:
        db.close()


@views_bp.route("/connections/<int:conn_id>")
@role_required("admin")
def connections_detail(conn_id):
    db = next(get_db())
    try:
        svc = ConnectionService(ConnectionRepository(db))
        conn = svc.get_connection(conn_id, request.user["tenant_id"])
        if not conn:
            return redirect("/connections")
        # `conn` is a dict (ConnectionService.get_connection returns
        # get_connection_decrypted which returns a dict, not an ORM
        # object). Prior use of `conn.name` AttributeError'd.
        return render_template("connections/detail.html", **ctx(
            active_page="settings_connections", settings_page="connections", conn=conn,
            breadcrumb_section="Connections", breadcrumb_section_url="/connections",
            breadcrumb_item=conn.get("name") if isinstance(conn, dict) else getattr(conn, "name", ""),
            message=request.args.get("message"),
        ))
    finally:
        db.close()


@views_bp.route("/connections/<int:conn_id>/test", methods=["POST"])
@role_required("admin")
def connections_test(conn_id):
    db = next(get_db())
    try:
        svc = ConnectionService(ConnectionRepository(db))
        result = svc.test_connection(conn_id, request.user["tenant_id"])
        msg = "Connected successfully!" if result.get("status") == "connected" else f"Failed: {result.get('detail', 'Unknown error')}"
        return redirect(f"/connections/{conn_id}?message={msg}")
    except Exception as e:
        return redirect(f"/connections/{conn_id}?message=Error: {e}")
    finally:
        db.close()


@views_bp.route("/connections/<int:conn_id>/delete", methods=["POST"])
@role_required("admin")
def connections_delete(conn_id):
    db = next(get_db())
    try:
        svc = ConnectionService(ConnectionRepository(db))
        svc.delete_connection(conn_id, request.user["tenant_id"])
        return redirect("/connections")
    except Exception:
        return redirect("/connections")
    finally:
        db.close()


@views_bp.route("/connections/<int:conn_id>/edit")
@role_required("admin")
def connections_edit(conn_id):
    db = next(get_db())
    try:
        svc = ConnectionService(ConnectionRepository(db))
        conn = svc.get_connection(conn_id, request.user["tenant_id"])
        if not conn:
            return redirect("/connections")
        return render_template("connections/edit.html", **ctx(
            active_page="settings_connections", settings_page="connections", conn=conn, error=None,
        ))
    finally:
        db.close()


@views_bp.route("/connections/<int:conn_id>/edit", methods=["POST"])
@role_required("admin")
def connections_update(conn_id):
    db = next(get_db())
    try:
        repo = ConnectionRepository(db)
        svc = ConnectionService(repo)
        conn = repo.get_connection(conn_id, request.user["tenant_id"])
        if not conn:
            return redirect("/connections")

        updates = {"name": request.form.get("name", conn.name)}
        old_config = dict(conn.config) if conn.config else {}

        if conn.connection_type == "salesforce":
            new_config = {
                "org_type": request.form.get("sf_org_type", old_config.get("org_type", "sandbox")),
                "instance_url": request.form.get("sf_instance_url") or old_config.get("instance_url", ""),
                "api_version": request.form.get("sf_api_version") or old_config.get("api_version", "59.0"),
                "username": request.form.get("sf_username") or old_config.get("username", ""),
            }
            if request.form.get("sf_client_id"):
                new_config["client_id"] = request.form["sf_client_id"]
            elif "client_id" in old_config:
                new_config["client_id"] = old_config["client_id"]
            if request.form.get("sf_client_secret"):
                new_config["client_secret"] = request.form["sf_client_secret"]
            elif "client_secret" in old_config:
                new_config["client_secret"] = old_config["client_secret"]
            if request.form.get("sf_password"):
                new_config["password"] = request.form["sf_password"]
            elif "password" in old_config:
                new_config["password"] = old_config["password"]
            updates["config"] = new_config
        elif conn.connection_type == "jira":
            new_config = {
                "base_url": request.form.get("jira_base_url") or old_config.get("base_url", ""),
                "auth_type": "basic",
                "username": request.form.get("jira_username") or old_config.get("username", ""),
            }
            if request.form.get("jira_api_token"):
                new_config["api_token"] = request.form["jira_api_token"]
            elif "api_token" in old_config:
                new_config["api_token"] = old_config["api_token"]
            updates["config"] = new_config
        elif conn.connection_type == "llm":
            new_config = {
                "provider": old_config.get("provider", "anthropic"),
                "model": request.form.get("llm_model") or old_config.get("model", "claude-sonnet-4-20250514"),
            }
            if request.form.get("llm_api_key"):
                new_config["api_key"] = request.form["llm_api_key"]
            elif "api_key" in old_config:
                new_config["api_key"] = old_config["api_key"]
            updates["config"] = new_config

        svc.update_connection(conn_id, request.user["tenant_id"], updates)
        return redirect(f"/connections/{conn_id}")
    except ValueError as e:
        conn_data = svc.get_connection(conn_id, request.user["tenant_id"])
        return render_template("connections/edit.html", **ctx(
            active_page="settings_connections", settings_page="connections", conn=conn_data, error=str(e),
        ))
    finally:
        db.close()


# --- Groups ---

@views_bp.route("/groups")
@login_required
def groups_list():
    db = next(get_db())
    try:
        svc = GroupService(GroupRepository(db))
        groups = svc.list_groups(
            request.user["tenant_id"], request.user["id"], request.user["role"],
        )
        return render_template("groups/list.html", **ctx(
            active_page="settings_groups", settings_page="groups", groups=groups,
        ))
    finally:
        db.close()


@views_bp.route("/groups/new")
@role_required("admin")
def groups_new():
    return render_template("groups/new.html", **ctx(active_page="settings_groups", settings_page="groups"))


@views_bp.route("/groups", methods=["POST"])
@role_required("admin")
def groups_create():
    db = next(get_db())
    try:
        svc = GroupService(GroupRepository(db))
        svc.create_group(
            request.user["tenant_id"], request.form["name"],
            request.user["id"], request.form.get("description"),
        )
        from flask import flash
        flash("Group created successfully", "success")
        return redirect("/groups")
    finally:
        db.close()


@views_bp.route("/groups/<int:group_id>/edit", methods=["GET"])
@role_required("admin")
def groups_edit(group_id):
    db = next(get_db())
    try:
        svc = GroupService(GroupRepository(db))
        group = svc.get_group_detail(group_id, request.user["tenant_id"])
        if not group:
            return redirect("/groups")
        return render_template("groups/edit.html", **ctx(
            active_page="settings_groups", settings_page="groups",
            breadcrumb_section="Groups", breadcrumb_item=f"Edit {group['name']}",
            group=group, error=None,
        ))
    finally:
        db.close()


@views_bp.route("/groups/<int:group_id>/edit", methods=["POST"])
@role_required("admin")
def groups_update(group_id):
    from flask import flash
    db = next(get_db())
    try:
        group_repo = GroupRepository(db)
        group_repo.update_group(group_id, request.user["tenant_id"], {
            "name": request.form.get("name"),
            "description": request.form.get("description"),
        })
        flash("Group updated successfully", "success")
        return redirect(f"/groups/{group_id}")
    except Exception as e:
        flash(str(e), "error")
        return redirect(f"/groups/{group_id}/edit")
    finally:
        db.close()


@views_bp.route("/groups/<int:group_id>")
@login_required
def groups_detail(group_id):
    db = next(get_db())
    try:
        svc = GroupService(GroupRepository(db))
        group = svc.get_group_detail(group_id, request.user["tenant_id"])
        if not group:
            return redirect("/groups")

        member_ids = {m["id"] for m in group["members"]}
        all_users = UserRepository(db).list_users(request.user["tenant_id"])
        available_users = [{"id": u.id, "full_name": u.full_name, "email": u.email}
                           for u in all_users if u.id not in member_ids and u.is_active]

        env_ids = {e["id"] for e in group["environments"]}
        all_envs = EnvironmentRepository(db).list_environments(request.user["tenant_id"])
        available_envs = [{"id": e.id, "name": e.name, "env_type": e.env_type}
                          for e in all_envs if e.id not in env_ids]

        return render_template("groups/detail.html", **ctx(
            active_page="settings_groups", settings_page="groups", group=group,
            breadcrumb_section="Groups", breadcrumb_section_url="/groups",
            breadcrumb_item=group.get("name") if isinstance(group, dict) else getattr(group, "name", None),
            available_users=available_users, available_envs=available_envs,
        ))
    finally:
        db.close()


@views_bp.route("/groups/<int:group_id>/members", methods=["POST"])
@role_required("admin")
def groups_add_member(group_id):
    db = next(get_db())
    try:
        svc = GroupService(GroupRepository(db))
        svc.add_member(group_id, request.user["tenant_id"],
                       int(request.form["user_id"]), request.user["id"])
        return redirect(f"/groups/{group_id}")
    except Exception:
        return redirect(f"/groups/{group_id}")
    finally:
        db.close()


@views_bp.route("/groups/<int:group_id>/members/<int:user_id>/remove", methods=["POST"])
@role_required("admin")
def groups_remove_member(group_id, user_id):
    db = next(get_db())
    try:
        svc = GroupService(GroupRepository(db))
        svc.remove_member(group_id, request.user["tenant_id"], user_id)
        return redirect(f"/groups/{group_id}")
    except Exception:
        return redirect(f"/groups/{group_id}")
    finally:
        db.close()


@views_bp.route("/groups/<int:group_id>/environments", methods=["POST"])
@role_required("admin")
def groups_add_environment(group_id):
    db = next(get_db())
    try:
        svc = GroupService(GroupRepository(db))
        svc.add_environment(group_id, request.user["tenant_id"],
                            int(request.form["environment_id"]), request.user["id"])
        return redirect(f"/groups/{group_id}")
    except Exception:
        return redirect(f"/groups/{group_id}")
    finally:
        db.close()


@views_bp.route("/groups/<int:group_id>/environments/<int:env_id>/remove", methods=["POST"])
@role_required("admin")
def groups_remove_environment(group_id, env_id):
    db = next(get_db())
    try:
        svc = GroupService(GroupRepository(db))
        svc.remove_environment(group_id, request.user["tenant_id"], env_id)
        return redirect(f"/groups/{group_id}")
    except Exception:
        return redirect(f"/groups/{group_id}")
    finally:
        db.close()


@views_bp.route("/groups/<int:group_id>/delete", methods=["POST"])
@role_required("admin")
def groups_delete(group_id):
    db = next(get_db())
    try:
        svc = GroupService(GroupRepository(db))
        svc.delete_group(group_id, request.user["tenant_id"])
        return redirect("/groups")
    except Exception:
        return redirect("/groups")
    finally:
        db.close()


# --- Settings (General) ---

@views_bp.route("/settings")
@login_required
def settings_general():
    db = next(get_db())
    try:
        from primeqa.core.models import Connection, Group, Environment, Tenant
        tid = request.user["tenant_id"]
        tenant = db.query(Tenant).filter(Tenant.id == tid).first()
        conn_count = db.query(Connection).filter(Connection.tenant_id == tid).count()
        env_count = db.query(Environment).filter(Environment.tenant_id == tid).count()
        group_count = db.query(Group).filter(Group.tenant_id == tid).count()
        setup_complete = conn_count > 0 and env_count > 0 and group_count > 0
        tenant_data = {"name": tenant.name if tenant else "Default", "slug": tenant.slug if tenant else "default"}
        return render_template("settings/general.html", **ctx(
            active_page="settings_general", settings_page="general",
            tenant=tenant_data, setup_complete=setup_complete,
            stats={"connections": conn_count, "environments": env_count, "groups": group_count},
        ))
    finally:
        db.close()


# R6 \u2014 Rerun subset + comparison + flake ------------------------------------

@views_bp.route("/runs/<int:run_id>/rerun-failed", methods=["POST"])
@role_required("admin", "tester")
def runs_rerun_failed(run_id):
    """Rerun only the failed tests of this run (R6)."""
    from flask import flash
    from primeqa.execution.repository import (
        PipelineRunRepository, PipelineStageRepository,
        ExecutionSlotRepository, WorkerHeartbeatRepository,
        RunTestResultRepository,
    )
    from primeqa.execution.service import PipelineService
    db = next(get_db())
    try:
        parent = PipelineRunRepository(db).get_run(run_id, request.user["tenant_id"])
        if not parent:
            flash("Run not found", "error"); return redirect("/runs")
        results = RunTestResultRepository(db).list_results(run_id)
        failed_ids = [r.test_case_id for r in results if r.status in ("failed", "error")]
        if not failed_ids:
            flash("No failed tests to rerun.", "error"); return redirect(f"/runs/{run_id}")

        svc = PipelineService(
            PipelineRunRepository(db), PipelineStageRepository(db),
            ExecutionSlotRepository(db), WorkerHeartbeatRepository(db),
        )
        created = svc.create_run(
            tenant_id=parent.tenant_id, environment_id=parent.environment_id,
            triggered_by=request.user["id"], run_type="execute_only",
            source_type="test_cases", source_ids=failed_ids,
            priority=parent.priority, parent_run_id=parent.id,
            source_refs={"rerun_failed_of": parent.id, "test_case_ids": failed_ids},
        )
        return redirect(f"/runs/{created['id']}")
    finally:
        db.close()


@views_bp.route("/runs/<int:run_id>/rerun-one", methods=["POST"])
@role_required("admin", "tester")
def runs_rerun_one(run_id):
    """Rerun a single test case from a previous run in a NEW run.
    Form: test_case_id (int). Used by the per-row "Rerun" action on
    the run detail timeline, next to each failed/errored test."""
    from flask import flash
    from primeqa.execution.repository import (
        PipelineRunRepository, PipelineStageRepository,
        ExecutionSlotRepository, WorkerHeartbeatRepository,
    )
    from primeqa.execution.service import PipelineService
    db = next(get_db())
    try:
        parent = PipelineRunRepository(db).get_run(run_id, request.user["tenant_id"])
        if not parent:
            flash("Run not found", "error"); return redirect("/runs")
        try:
            tc_id = int(request.form["test_case_id"])
        except (KeyError, ValueError):
            flash("Missing test_case_id", "error")
            return redirect(f"/runs/{run_id}")

        svc = PipelineService(
            PipelineRunRepository(db), PipelineStageRepository(db),
            ExecutionSlotRepository(db), WorkerHeartbeatRepository(db),
        )
        created = svc.create_run(
            tenant_id=parent.tenant_id, environment_id=parent.environment_id,
            triggered_by=request.user["id"], run_type="execute_only",
            source_type="test_cases", source_ids=[tc_id],
            priority=parent.priority, parent_run_id=parent.id,
            source_refs={"rerun_one_of": parent.id, "test_case_ids": [tc_id]},
        )
        return redirect(f"/runs/{created['id']}")
    finally:
        db.close()


@views_bp.route("/runs/<int:run_id>/rerun-verbatim", methods=["POST"])
@role_required("admin", "tester")
def runs_rerun_verbatim(run_id):
    """Replay a prior run with the EXACT same test case versions.

    Most reruns hit whatever the current_version_id is now. "Verbatim"
    pins each TC to the version_id that was executed in the parent
    run \u2014 useful to reproduce a failure when the TC has moved on.
    Pinned versions travel in run.config.version_pin; the worker
    honours that ahead of current_version_id.
    """
    from flask import flash
    from primeqa.execution.repository import (
        PipelineRunRepository, PipelineStageRepository,
        ExecutionSlotRepository, WorkerHeartbeatRepository,
        RunTestResultRepository,
    )
    from primeqa.execution.service import PipelineService
    db = next(get_db())
    try:
        parent = PipelineRunRepository(db).get_run(run_id, request.user["tenant_id"])
        if not parent:
            flash("Run not found", "error"); return redirect("/runs")
        results = RunTestResultRepository(db).list_results(run_id)
        if not results:
            flash("Original run has no test results to replay.", "error")
            return redirect(f"/runs/{run_id}")

        tc_ids = [r.test_case_id for r in results]
        # str keys so JSON round-trips cleanly; worker coerces back to int
        version_pin = {str(r.test_case_id): r.test_case_version_id for r in results}

        svc = PipelineService(
            PipelineRunRepository(db), PipelineStageRepository(db),
            ExecutionSlotRepository(db), WorkerHeartbeatRepository(db),
        )
        created = svc.create_run(
            tenant_id=parent.tenant_id, environment_id=parent.environment_id,
            triggered_by=request.user["id"], run_type="execute_only",
            source_type="rerun", source_ids=tc_ids,
            priority=parent.priority, parent_run_id=parent.id,
            config={"version_pin": version_pin},
            source_refs={"rerun_verbatim_of": parent.id,
                         "test_case_ids": tc_ids,
                         "version_pin": version_pin},
        )
        return redirect(f"/runs/{created['id']}")
    except Exception as e:
        flash(f"Could not rerun verbatim: {e}", "error")
        return redirect(f"/runs/{run_id}")
    finally:
        db.close()


@views_bp.route("/runs/<int:run_id>/label", methods=["POST"])
@role_required("admin", "tester")
def runs_set_label(run_id):
    """Inline edit of a run's free-form label."""
    from primeqa.execution.repository import PipelineRunRepository
    from primeqa.execution.models import PipelineRun
    db = next(get_db())
    try:
        run = PipelineRunRepository(db).get_run(run_id, request.user["tenant_id"])
        if not run:
            return jsonify(error="Run not found"), 404
        data = request.get_json(silent=True) or {}
        label = (data.get("label") or "").strip()[:100] or None
        r = db.query(PipelineRun).filter(
            PipelineRun.id == run_id,
            PipelineRun.tenant_id == request.user["tenant_id"],
        ).first()
        if not r:
            return jsonify(error="Run not found"), 404
        r.label = label
        db.commit()
        return jsonify({"label": label}), 200
    finally:
        db.close()


@views_bp.route("/runs/<int:run_id>/summarise-failures", methods=["POST"])
@role_required("superadmin")
def runs_summarise_failures(run_id):
    """Generate (or refresh) an AI rollup of why tests failed on this run.

    Superadmin only \u2014 costs LLM tokens. Sends failed steps'
    error_message + step_action + target_object to the tenant's default
    LLM, asks for a 2-4 sentence summary grouping by failure class.
    Caches on pipeline_runs.failure_summary_ai; re-clicking regenerates.
    """
    from flask import flash
    from datetime import datetime, timezone
    from primeqa.execution.repository import (
        PipelineRunRepository, RunTestResultRepository, RunStepResultRepository,
    )
    from primeqa.execution.models import PipelineRun
    from primeqa.core.repository import ConnectionRepository, EnvironmentRepository
    db = next(get_db())
    try:
        tid = request.user["tenant_id"]
        run = PipelineRunRepository(db).get_run(run_id, tid)
        if not run:
            flash("Run not found", "error"); return redirect("/runs")

        # Collect failed step error messages
        results = RunTestResultRepository(db).list_results(run_id)
        failed_tcs = [r for r in results if r.status in ("failed", "error")]
        if not failed_tcs:
            flash("No failures on this run \u2014 nothing to summarise.", "info")
            return redirect(f"/runs/{run_id}")

        step_repo = RunStepResultRepository(db)
        failure_lines = []
        for r in failed_tcs:
            steps = step_repo.list_step_results(r.id)
            for s in steps:
                if s.status in ("failed", "error") and s.error_message:
                    failure_lines.append(
                        f"- TC#{r.test_case_id} step {s.step_order} "
                        f"{s.step_action} on {s.target_object or '?'}: "
                        f"{(s.error_message or '')[:300]}"
                    )

        if not failure_lines:
            flash("No step-level failure messages found.", "info")
            return redirect(f"/runs/{run_id}")

        # Need an LLM connection; use the env's configured one
        env = EnvironmentRepository(db).get_environment(run.environment_id, tid)
        if not env or not env.llm_connection_id:
            flash("This environment has no LLM connection.", "error")
            return redirect(f"/runs/{run_id}")
        conn = ConnectionRepository(db).get_connection_decrypted(env.llm_connection_id, tid)
        if not conn:
            flash("LLM connection could not be loaded.", "error")
            return redirect(f"/runs/{run_id}")

        # Route through the LLM Gateway \u2014 Haiku by default (see prompts
        # router), backoff + usage log handled automatically.
        from primeqa.intelligence.llm import llm_call, LLMError
        api_key = conn["config"].get("api_key", "")
        try:
            resp = llm_call(
                task="failure_summary",
                tenant_id=tid, user_id=request.user["id"],
                api_key=api_key,
                context={"failure_lines": failure_lines, "run_id": run_id},
                run_id=run_id,
            )
            summary = resp.parsed_content
        except LLMError as e:
            if e.status == "quota_exceeded":
                flash("AI summarisation failed: Anthropic credits "
                      "exhausted. Top up at console.anthropic.com.", "error")
            elif e.status == "auth_error":
                flash("AI summarisation failed: LLM connection API key "
                      "is invalid. Check Settings \u2192 Connections.", "error")
            else:
                flash(f"AI summarisation failed: {e.message[:200]}", "error")
            return redirect(f"/runs/{run_id}")

        r = db.query(PipelineRun).filter(
            PipelineRun.id == run_id, PipelineRun.tenant_id == tid,
        ).first()
        r.failure_summary_ai = summary
        r.failure_summary_at = datetime.now(timezone.utc)
        r.failure_summary_model = resp.model
        db.commit()
        return redirect(f"/runs/{run_id}")
    finally:
        db.close()


@views_bp.route("/runs/<int:run_id>/compare", methods=["GET"])
@login_required
def runs_compare_last_green(run_id):
    """Compare this run with the most recent successful run against the same env."""
    from primeqa.execution.models import PipelineRun, RunTestResult
    db = next(get_db())
    try:
        run = db.query(PipelineRun).filter(
            PipelineRun.id == run_id,
            PipelineRun.tenant_id == request.user["tenant_id"],
        ).first()
        if not run:
            return redirect("/runs")
        prev = db.query(PipelineRun).filter(
            PipelineRun.tenant_id == run.tenant_id,
            PipelineRun.environment_id == run.environment_id,
            PipelineRun.status == "completed",
            PipelineRun.failed == 0,
            PipelineRun.id < run.id,
        ).order_by(PipelineRun.id.desc()).first()

        def _status_map(rid):
            rows = db.query(RunTestResult).filter(RunTestResult.run_id == rid).all()
            return {r.test_case_id: r.status for r in rows}

        this_map = _status_map(run.id)
        prev_map = _status_map(prev.id) if prev else {}

        flipped_green_to_red, flipped_red_to_green, newly_added = [], [], []
        for tc_id, status in this_map.items():
            prev_status = prev_map.get(tc_id)
            if prev_status is None:
                newly_added.append(tc_id)
            elif prev_status == "passed" and status in ("failed", "error"):
                flipped_green_to_red.append(tc_id)
            elif prev_status in ("failed", "error") and status == "passed":
                flipped_red_to_green.append(tc_id)

        return render_template("runs/compare.html", **ctx(
            active_page="runs", this_run=run, prev_run=prev,
            flipped_green_to_red=flipped_green_to_red,
            flipped_red_to_green=flipped_red_to_green,
            newly_added=newly_added,
        ))
    finally:
        db.close()


@views_bp.route("/test-cases/<int:tc_id>/quarantine/lift", methods=["POST"])
@role_required("admin", "tester")
def test_case_lift_quarantine(tc_id):
    from flask import flash
    from primeqa.execution.flake import lift_quarantine
    db = next(get_db())
    try:
        ok = lift_quarantine(db, test_case_id=tc_id, tenant_id=request.user["tenant_id"])
        flash("Quarantine lifted" if ok else "Test case not found",
              "success" if ok else "error")
        return redirect(f"/test-cases/{tc_id}")
    finally:
        db.close()


# Agent fix user decisions (R5) --------------------------------------------

@views_bp.route("/runs/agent-fixes/<int:fix_id>/accept", methods=["POST"])
@role_required("admin", "tester", "ba")
def agent_fix_accept(fix_id):
    from flask import flash
    from primeqa.intelligence.agent import AgentOrchestrator
    db = next(get_db())
    try:
        orch = AgentOrchestrator(db)
        ok = orch.accept(fix_id, request.user["tenant_id"], request.user["id"])
        flash("Fix accepted" if ok else "Could not accept (not found)", "success" if ok else "error")
        # Return to originating run
        from primeqa.intelligence.models import AgentFixAttempt
        row = db.query(AgentFixAttempt).filter_by(id=fix_id).first()
        return redirect(f"/runs/{row.run_id}" if row else "/runs")
    finally:
        db.close()


@views_bp.route("/runs/agent-fixes/<int:fix_id>/revert", methods=["POST"])
@role_required("admin", "tester", "ba")
def agent_fix_revert(fix_id):
    from flask import flash
    from primeqa.intelligence.agent import AgentOrchestrator
    db = next(get_db())
    try:
        orch = AgentOrchestrator(db)
        ok = orch.revert(fix_id, request.user["tenant_id"], request.user["id"])
        flash("Fix reverted; before-state restored" if ok else "Revert failed",
              "success" if ok else "error")
        from primeqa.intelligence.models import AgentFixAttempt
        row = db.query(AgentFixAttempt).filter_by(id=fix_id).first()
        return redirect(f"/runs/{row.run_id}" if row else "/runs")
    finally:
        db.close()


# Scheduled runs (R4) -------------------------------------------------------

@views_bp.route("/runs/scheduled")
@role_required("admin", "tester")
def scheduled_runs_list():
    db = next(get_db())
    try:
        from primeqa.runs.schedule import ScheduledRunRepository
        from primeqa.test_management.models import TestSuite
        from primeqa.core.models import Environment
        rows = ScheduledRunRepository(db).list_for_tenant(request.user["tenant_id"])
        suites = {s.id: s for s in db.query(TestSuite).all()}
        envs = {e.id: e for e in db.query(Environment).all()}
        data = []
        for r in rows:
            suite = suites.get(r.suite_id); env = envs.get(r.environment_id)
            data.append({
                "id": r.id, "suite_id": r.suite_id,
                "suite_name": suite.name if suite else f"#{r.suite_id}",
                "env_name": env.name if env else f"#{r.environment_id}",
                "env_type": env.env_type if env else "",
                "cron_expr": r.cron_expr, "preset_label": r.preset_label,
                "priority": r.priority, "enabled": r.enabled,
                "max_silence_hours": r.max_silence_hours,
                "next_fire_at": r.next_fire_at.isoformat() if r.next_fire_at else None,
                "last_fired_at": r.last_fired_at.isoformat() if r.last_fired_at else None,
                "last_run_id": r.last_run_id,
            })
        return render_template("runs/scheduled_list.html", **ctx(
            active_page="runs", schedules=data,
        ))
    finally:
        db.close()


def _schedule_form_context(db, tenant_id, schedule=None, error=None):
    from primeqa.runs.schedule import PRESETS, PRESET_LABELS
    from primeqa.test_management.models import TestSuite
    suites = [{"id": s.id, "name": s.name, "suite_type": s.suite_type}
              for s in db.query(TestSuite).filter(
                  TestSuite.tenant_id == tenant_id,
                  TestSuite.deleted_at.is_(None),
              ).order_by(TestSuite.name.asc()).all()]
    envs = EnvironmentRepository(db).list_environments(tenant_id)
    envs_data = [{"id": e.id, "name": e.name, "env_type": e.env_type} for e in envs]
    sched_dict = None
    if schedule:
        sched_dict = {
            "id": schedule.id, "suite_id": schedule.suite_id,
            "environment_id": schedule.environment_id,
            "cron_expr": schedule.cron_expr, "preset_label": schedule.preset_label,
            "priority": schedule.priority, "enabled": schedule.enabled,
            "max_silence_hours": schedule.max_silence_hours,
        }
    return {
        "suites": suites, "environments": envs_data,
        "presets": [(k, PRESET_LABELS[k]) for k in PRESETS],
        "presets_map": PRESETS,
        "schedule": sched_dict, "error": error,
    }


@views_bp.route("/runs/scheduled/new", methods=["GET"])
@role_required("admin", "tester")
def scheduled_runs_new_form():
    db = next(get_db())
    try:
        kwargs = _schedule_form_context(db, request.user["tenant_id"])
        return render_template("runs/scheduled_form.html", **ctx(
            active_page="runs", action_url="/runs/scheduled/new", **kwargs,
        ))
    finally:
        db.close()


@views_bp.route("/runs/scheduled/new", methods=["POST"])
@role_required("admin", "tester")
def scheduled_runs_create():
    from flask import flash
    from primeqa.runs.schedule import ScheduledRunRepository
    db = next(get_db())
    try:
        msh = request.form.get("max_silence_hours") or None
        try:
            repo = ScheduledRunRepository(db)
            repo.create(
                tenant_id=request.user["tenant_id"],
                suite_id=int(request.form["suite_id"]),
                environment_id=int(request.form["environment_id"]),
                cron_expr=request.form["cron_expr"].strip(),
                preset_label=None,  # repo derives from cron_expr
                priority=request.form.get("priority", "normal"),
                max_silence_hours=int(msh) if msh else None,
                created_by=request.user["id"],
            )
            flash("Schedule created", "success")
            return redirect("/runs/scheduled")
        except ValueError as e:
            kwargs = _schedule_form_context(db, request.user["tenant_id"], error=str(e))
            return render_template("runs/scheduled_form.html", **ctx(
                active_page="runs", action_url="/runs/scheduled/new", **kwargs,
            ))
    finally:
        db.close()


@views_bp.route("/runs/scheduled/<int:sid>/edit", methods=["GET"])
@role_required("admin", "tester")
def scheduled_runs_edit_form(sid):
    from primeqa.runs.schedule import ScheduledRunRepository
    db = next(get_db())
    try:
        row = ScheduledRunRepository(db).get(sid, request.user["tenant_id"])
        if not row:
            return redirect("/runs/scheduled")
        kwargs = _schedule_form_context(db, request.user["tenant_id"], schedule=row)
        return render_template("runs/scheduled_form.html", **ctx(
            active_page="runs", action_url=f"/runs/scheduled/{sid}/edit", **kwargs,
        ))
    finally:
        db.close()


@views_bp.route("/runs/scheduled/<int:sid>/edit", methods=["POST"])
@role_required("admin", "tester")
def scheduled_runs_edit(sid):
    from flask import flash
    from primeqa.runs.schedule import ScheduledRunRepository
    db = next(get_db())
    try:
        repo = ScheduledRunRepository(db)
        msh = request.form.get("max_silence_hours") or None
        try:
            repo.update(
                sid, request.user["tenant_id"], updated_by=request.user["id"],
                environment_id=int(request.form["environment_id"]),
                cron_expr=request.form["cron_expr"].strip(),
                priority=request.form.get("priority", "normal"),
                max_silence_hours=int(msh) if msh else None,
            )
            flash("Schedule updated", "success")
        except ValueError as e:
            flash(str(e), "error")
        return redirect("/runs/scheduled")
    finally:
        db.close()


@views_bp.route("/runs/scheduled/<int:sid>/enable", methods=["POST"])
@role_required("admin", "tester")
def scheduled_runs_enable(sid):
    from primeqa.runs.schedule import ScheduledRunRepository
    db = next(get_db())
    try:
        ScheduledRunRepository(db).update(
            sid, request.user["tenant_id"], updated_by=request.user["id"], enabled=True,
        )
        return redirect("/runs/scheduled")
    finally:
        db.close()


@views_bp.route("/runs/scheduled/<int:sid>/disable", methods=["POST"])
@role_required("admin", "tester")
def scheduled_runs_disable(sid):
    from primeqa.runs.schedule import ScheduledRunRepository
    db = next(get_db())
    try:
        ScheduledRunRepository(db).update(
            sid, request.user["tenant_id"], updated_by=request.user["id"], enabled=False,
        )
        return redirect("/runs/scheduled")
    finally:
        db.close()


@views_bp.route("/runs/scheduled/<int:sid>/delete", methods=["POST"])
@role_required("admin", "tester")
def scheduled_runs_delete(sid):
    from primeqa.runs.schedule import ScheduledRunRepository
    db = next(get_db())
    try:
        ScheduledRunRepository(db).delete(sid, request.user["tenant_id"])
        return redirect("/runs/scheduled")
    finally:
        db.close()


# Super Admin settings: Agent autonomy (R2) --------------------------------
@views_bp.route("/settings/llm-usage")
@role_required("superadmin")
def settings_llm_usage():
    """Superadmin LLM-usage dashboard (Phase 3).

    Three stacked views:
      Cost control  \u2014 who spent what, per feature, per model, per day
      Efficiency    \u2014 cache hit rate, avg cost/generation, escalation rate, errors
      Quality proxy \u2014 regeneration rate, validation-critical rate, post-gen fail rate

    Every query runs over llm_usage_log (migration 031) with indexes
    added for this exact workload. Window defaults to 30 days; override
    via ?days=7 or ?days=90.
    """
    from primeqa.intelligence.llm import dashboard
    db = next(get_db())
    try:
        days = max(1, min(180, request.args.get("days", 30, type=int) or 30))
        cost = dashboard.cost_summary(db, days=days)
        eff = dashboard.efficiency_summary(db, days=days)
        quality = dashboard.quality_proxy_summary(db, days=days)
        spenders = dashboard.top_spenders(db, days=days)
        # Enrich by_tenant with name + current tier + correction rate.
        from primeqa.intelligence.llm import tiers as _tiers
        if cost["by_tenant"]:
            from primeqa.core.models import Tenant, TenantAgentSettings
            from sqlalchemy import text as _sql
            from datetime import datetime, timedelta, timezone
            tids = [row["key"] for row in cost["by_tenant"]]
            name_rows = db.query(Tenant.id, Tenant.name).filter(
                Tenant.id.in_(tids),
            ).all()
            name_by_id = {r[0]: r[1] for r in name_rows}
            tier_rows = db.query(
                TenantAgentSettings.tenant_id,
                TenantAgentSettings.llm_tier,
            ).filter(TenantAgentSettings.tenant_id.in_(tids)).all()
            tier_by_id = {r[0]: r[1] for r in tier_rows}

            # Correction rate across ALL visible tenants in ONE query.
            # Audit U3 (2026-04-19): previously called feedback_rules.
            # correction_rate() in a loop — one query per tenant × Railway
            # RTT. At 20 tenants = 13 seconds. Now one CTE does the lot.
            start = datetime.now(timezone.utc) - timedelta(days=days)
            rate_rows = db.execute(_sql("""
                WITH tc_per_tenant AS (
                  SELECT tenant_id, COUNT(*)::int AS denom
                  FROM test_cases
                  WHERE tenant_id = ANY(:tids)
                    AND generation_batch_id IS NOT NULL
                    AND deleted_at IS NULL
                    AND updated_at >= :start
                  GROUP BY tenant_id
                ),
                corrected_per_tenant AS (
                  SELECT tenant_id, COUNT(DISTINCT test_case_id)::int AS corrected
                  FROM generation_quality_signals
                  WHERE tenant_id = ANY(:tids)
                    AND captured_at >= :start
                    AND test_case_id IS NOT NULL
                    AND signal_type IN ('user_edited', 'ba_rejected', 'user_thumbs_down')
                  GROUP BY tenant_id
                )
                SELECT t.id AS tenant_id,
                       COALESCE(d.denom, 0) AS denom,
                       COALESCE(c.corrected, 0) AS corrected
                FROM (SELECT unnest(:tids) AS id) t
                LEFT JOIN tc_per_tenant d ON d.tenant_id = t.id
                LEFT JOIN corrected_per_tenant c ON c.tenant_id = t.id
            """), {"tids": list(tids), "start": start}).all()
            rate_by_id = {
                r._mapping["tenant_id"]: (r._mapping["corrected"], r._mapping["denom"])
                for r in rate_rows
            }

            for row in cost["by_tenant"]:
                row["tenant_name"] = name_by_id.get(row["key"], f"Tenant #{row['key']}")
                row["tier"] = tier_by_id.get(row["key"], _tiers.TIER_STARTER)
                corrected, total = rate_by_id.get(row["key"], (0, 0))
                row["correction_total"] = int(total)
                row["correction_rate"] = (float(corrected) / float(total)) if total else 0.0
        return render_template("settings/llm_usage.html", **ctx(
            active_page="settings_llm_usage", settings_page="llm_usage",
            cost=cost, efficiency=eff, quality=quality,
            top_spenders=spenders, days=days,
            all_tiers=_tiers.all_presets(),
        ))
    finally:
        db.close()


# Tenant self-service LLM usage + tier (Phase 6) ----------------------------
@views_bp.route("/settings/my-llm-usage")
@role_required("admin")
def settings_my_llm_usage():
    """Tenant-scoped LLM usage view.

    Surfaces:
      - current tier + preset values (plain-English copy, not raw caps)
      - live soft-cap progress bars (80%+ shows amber banner)
      - number of calls blocked by rate limits in window
      - per-task spend for their tenant only
      - daily spend bars

    Visible to `admin` (plus superadmin via the role_required bypass).
    Non-admins see `/settings/agent` and friends already; this one lives
    alongside Test Data in the general admin flow — it's not a
    superadmin-only concern the way /settings/llm-usage is.
    """
    from primeqa.intelligence.llm import dashboard, limits, tiers
    from primeqa.core.models import TenantAgentSettings

    tenant_id = request.user["tenant_id"]
    days = max(1, min(180, request.args.get("days", 30, type=int) or 30))

    db = next(get_db())
    try:
        # Single-session pass-through (audit U2, 2026-04-19): all
        # dashboard helpers share this one `db` so we amortise Railway's
        # ~650ms RTT over fewer connections. `return_row=True` hands back
        # the raw TenantAgentSettings row so we don't re-query it for
        # the tier picker below.
        tl, _tp, tas_row = limits.load_tenant_config(
            tenant_id, db=db, return_row=True,
        )
        snap = limits.current_usage(tenant_id, tl, db=db)
        summary = dashboard.tenant_summary(db, tenant_id, days=days)
        # Phase 7: AI-quality feedback block — correction rate is the
        # north-star, plus top-5 recurring issues + per-signal counts.
        feedback_view = dashboard.tenant_feedback_summary(
            db, tenant_id, days=days,
        )

        tier_name = (getattr(tas_row, "llm_tier", None)
                     if tas_row else None) or tiers.TIER_STARTER
        preset = tiers.get_preset(tier_name)
        all_tier_presets = tiers.all_presets()

        return render_template("settings/my_llm_usage.html", **ctx(
            active_page="settings_my_llm_usage",
            settings_page="my_llm_usage",
            days=days,
            summary=summary,
            feedback=feedback_view,
            snapshot=snap,
            tier=tier_name,
            preset=preset,
            all_tiers=all_tier_presets,
        ))
    finally:
        db.close()


@views_bp.route("/settings/tenant-tier/<int:tenant_id>", methods=["POST"])
@role_required("superadmin")
def settings_change_tenant_tier(tenant_id):
    """Superadmin-only: change a tenant's LLM tier.

    Accepts form field `llm_tier` ∈ {starter, pro, enterprise, custom}.
    Logs to activity_log so the change is audit-trail visible. Redirects
    back to /settings/llm-usage (the superadmin view where the tier
    picker lives).
    """
    from flask import flash
    from primeqa.intelligence.llm import tiers
    from primeqa.core.models import TenantAgentSettings, ActivityLog

    new_tier = (request.form.get("llm_tier") or "").strip().lower()
    if new_tier not in tiers.ALL_TIERS:
        flash(f"Unknown tier: {new_tier!r}", "error")
        return redirect("/settings/llm-usage")

    db = next(get_db())
    try:
        row = db.query(TenantAgentSettings).filter(
            TenantAgentSettings.tenant_id == tenant_id,
        ).first()
        if not row:
            row = TenantAgentSettings(tenant_id=tenant_id, llm_tier=new_tier)
            db.add(row)
        else:
            old_tier = row.llm_tier
            row.llm_tier = new_tier
            db.flush()
            db.add(ActivityLog(
                tenant_id=tenant_id,
                user_id=request.user["id"],
                action="update",
                entity_type="tenant_llm_tier",
                entity_id=tenant_id,
                details={"old": old_tier, "new": new_tier},
            ))
        db.commit()
        flash(f"Tenant #{tenant_id} moved to {new_tier}", "success")
    except Exception as e:
        db.rollback()
        flash(f"Failed to change tier: {e}", "error")
    finally:
        db.close()
    return redirect("/settings/llm-usage")


@views_bp.route("/settings/agent", methods=["GET"])
@role_required("superadmin")
def settings_agent_get():
    db = next(get_db())
    try:
        from primeqa.core.agent_settings import AgentSettingsRepository
        settings = AgentSettingsRepository(db).get(request.user["tenant_id"])
        return render_template("settings/agent.html", **ctx(
            active_page="settings_agent", settings_page="agent",
            settings=settings,
        ))
    finally:
        db.close()


@views_bp.route("/settings/agent", methods=["POST"])
@role_required("superadmin")
def settings_agent_post():
    from flask import flash
    db = next(get_db())
    try:
        from primeqa.core.agent_settings import AgentSettingsRepository
        repo = AgentSettingsRepository(db)
        agent_enabled = bool(request.form.get("agent_enabled"))
        try:
            repo.update(
                request.user["tenant_id"],
                updated_by=request.user["id"],
                agent_enabled=agent_enabled,
                trust_threshold_high=float(request.form.get("trust_threshold_high") or 0.85),
                trust_threshold_medium=float(request.form.get("trust_threshold_medium") or 0.60),
                max_fix_attempts_per_run=int(request.form.get("max_fix_attempts_per_run") or 3),
            )
            flash("Agent settings saved", "success")
        except ValueError as e:
            flash(str(e), "error")
        return redirect("/settings/agent")
    finally:
        db.close()


# Settings URL aliases
@views_bp.route("/settings/connections")
@role_required("admin")
def settings_connections(): return redirect("/connections")

@views_bp.route("/settings/environments")
@role_required("admin")
def settings_environments(): return redirect("/environments")

@views_bp.route("/settings/groups")
@login_required
def settings_groups(): return redirect("/groups")

# /settings/users is defined above (permission-set aware). The legacy
# redirect shim is retired — Admin UI lives at /settings/users now.


# --- Setup Wizard ---

@views_bp.route("/setup")
@role_required("admin")
def setup_wizard():
    db = next(get_db())
    try:
        from primeqa.core.models import Connection, Group, Environment
        tid = request.user["tenant_id"]
        conn_count = db.query(Connection).filter(Connection.tenant_id == tid).count()
        env_count = db.query(Environment).filter(Environment.tenant_id == tid).count()
        group_count = db.query(Group).filter(Group.tenant_id == tid).count()
        return render_template("setup/wizard.html", **ctx(
            active_page="settings_setup", settings_page="general",
            connections_ok=conn_count > 0,
            environments_ok=env_count > 0,
            groups_ok=group_count > 0,
            connection_count=conn_count,
            environment_count=env_count,
            group_count=group_count,
        ))
    finally:
        db.close()


# --- Test Data ---

@views_bp.route("/settings/test-data")
@login_required
def test_data_list():
    db = next(get_db())
    try:
        from primeqa.execution.data_engine import DataEngineService
        svc = DataEngineService(db)
        tid = request.user["tenant_id"]
        templates = svc.list_templates(tid)
        factories = svc.list_factories(tid)
        t_data = [{"id": t.id, "name": t.name, "object_type": t.object_type,
                   "description": t.description, "field_values": t.field_values} for t in templates]
        f_data = [{"id": f.id, "name": f.name, "factory_type": f.factory_type,
                   "description": f.description, "config": f.config} for f in factories]
        return render_template("test_data/list.html", **ctx(
            active_page="settings_test_data", settings_page="test_data",
            templates=t_data, factories=f_data,
        ))
    finally:
        db.close()


@views_bp.route("/settings/test-data/templates", methods=["POST"])
@role_required("admin", "tester")
def test_data_templates_create():
    from flask import flash
    import json as _json
    db = next(get_db())
    try:
        from primeqa.execution.data_engine import DataEngineService
        svc = DataEngineService(db)
        try:
            field_values = _json.loads(request.form.get("field_values") or "{}")
        except Exception:
            field_values = {}
        svc.create_template(
            request.user["tenant_id"], request.form["name"],
            request.form["object_type"], field_values, request.user["id"],
            description=request.form.get("description"),
        )
        flash("Template created", "success")
    except Exception as e:
        flash(str(e), "error")
    finally:
        db.close()
    return redirect("/settings/test-data")


@views_bp.route("/settings/test-data/factories", methods=["POST"])
@role_required("admin", "tester")
def test_data_factories_create():
    from flask import flash
    db = next(get_db())
    try:
        from primeqa.execution.data_engine import DataEngineService
        svc = DataEngineService(db)
        svc.create_factory(
            request.user["tenant_id"], request.form["name"],
            request.form["factory_type"], {}, request.user["id"],
            description=request.form.get("description"),
        )
        flash("Factory created", "success")
    except Exception as e:
        flash(str(e), "error")
    finally:
        db.close()
    return redirect("/settings/test-data")


# --- Requirements + AI Generation ---

@views_bp.route("/requirements")
@login_required
def requirements_list():
    db = next(get_db())
    try:
        from primeqa.test_management.repository import RequirementRepository, SectionRepository
        req_repo = RequirementRepository(db)
        sec_repo = SectionRepository(db)
        tid = request.user["tenant_id"]
        sections = sec_repo.list_sections(tid)
        envs = EnvironmentRepository(db).list_environments(tid, request.user["id"], request.user["role"])
        conns = ConnectionRepository(db).list_connections(tid, "jira")

        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 20, type=int)
        q = (request.args.get("q") or "").strip()
        sort = request.args.get("sort", "updated_at")
        order = request.args.get("order", "desc")
        show_deleted = request.args.get("deleted", "").lower() in ("1", "true", "yes")
        filters = {}
        if request.args.get("section_id", type=int):
            filters["section_id"] = request.args.get("section_id", type=int)
        if request.args.get("source"):
            filters["source"] = request.args.get("source")

        try:
            result = req_repo.list_page(
                tid, page=page, per_page=per_page, q=q, sort=sort, order=order,
                filters=filters, include_deleted=show_deleted,
            )
            reqs = result.items
            meta = {"total": result.total, "page": result.page,
                    "per_page": result.per_page, "total_pages": result.total_pages}
            query_error = None
        except Exception as e:
            reqs, meta, query_error = [], {"total": 0, "page": 1, "per_page": per_page, "total_pages": 0}, str(e)

        # Load TC counts + coverage breakdown + generation state per
        # visible requirement in one query, so the list can show "5
        # tests · positive 1 · negative 2..." badges and pick the right
        # button label (Generate / Regenerate / Generate again).
        tc_stats = {}  # requirement_id -> {total, coverage, any_draft_mine, any_approved_or_active}
        if reqs:
            from sqlalchemy import func as _sf
            from primeqa.test_management.models import TestCase
            rows = (db.query(
                        TestCase.requirement_id,
                        TestCase.status,
                        TestCase.coverage_type,
                        TestCase.owner_id,
                    )
                    .filter(
                        TestCase.tenant_id == tid,
                        TestCase.deleted_at.is_(None),
                        TestCase.requirement_id.in_([r.id for r in reqs]),
                    ).all())
            for rid, status_, cov, owner in rows:
                stats = tc_stats.setdefault(rid, {
                    "total": 0, "coverage": {},
                    "my_draft_count": 0,
                    "approved_or_active_count": 0,
                })
                stats["total"] += 1
                k = cov or "other"
                stats["coverage"][k] = stats["coverage"].get(k, 0) + 1
                if status_ == "draft" and owner == request.user["id"]:
                    stats["my_draft_count"] += 1
                if status_ in ("approved", "active"):
                    stats["approved_or_active_count"] += 1

        def _button_state(s):
            """Pick between Generate / Regenerate / Generate again based
            on what the user already has for this requirement."""
            if not s:
                return "generate"       # first-time generation
            if s["my_draft_count"] > 0:
                return "regenerate"     # supersede-in-place
            if s["approved_or_active_count"] > 0:
                return "generate_again" # alongside approved work
            return "generate"

        reqs_data = [{
            "id": r.id, "jira_key": r.jira_key, "jira_summary": r.jira_summary,
            "acceptance_criteria": r.acceptance_criteria, "is_stale": r.is_stale,
            "source": r.source,
            "updated_at": r.updated_at.isoformat() if r.updated_at else "",
            "tc_count": (tc_stats.get(r.id) or {}).get("total", 0),
            "coverage_counts": (tc_stats.get(r.id) or {}).get("coverage", {}),
            "button_state": _button_state(tc_stats.get(r.id)),
        } for r in reqs]
        # Envs with their readiness for AI generation (needs LLM + metadata)
        envs_data = [{
            "id": e.id, "name": e.name,
            "has_llm": bool(e.llm_connection_id),
            "has_meta": bool(e.current_meta_version_id),
            "ready": bool(e.llm_connection_id and e.current_meta_version_id),
        } for e in envs]
        sections_data = [{"id": s.id, "name": s.name} for s in sections]
        conns_data = [{"id": c.id, "name": c.name} for c in conns]
        return render_template("requirements/list.html", **ctx(
            active_page="requirements",
            requirements=reqs_data, sections=sections_data,
            environments=envs_data, jira_connections=conns_data,
            meta=meta, search=q, sort=sort, order=order,
            show_deleted=show_deleted, query_error=query_error,
        ))
    finally:
        db.close()


@views_bp.route("/requirements/<int:req_id>")
@login_required
def requirements_detail(req_id):
    """Detail page for a single requirement \u2014 Jira context, acceptance
    criteria, linked test cases, inline edit, Re-sync, Generate, Delete."""
    db = next(get_db())
    try:
        from primeqa.test_management.repository import (
            RequirementRepository, SectionRepository, TestCaseRepository,
        )
        from primeqa.test_management.models import TestCase
        tid = request.user["tenant_id"]
        req_repo = RequirementRepository(db)
        req = req_repo.get_requirement(req_id, tid, include_deleted=True)
        if not req:
            return redirect("/requirements")

        section = None
        if req.section_id:
            section = SectionRepository(db).get_section(req.section_id, tid)

        # Linked test cases (non-deleted, visible-to-user)
        tcs = db.query(TestCase).filter(
            TestCase.tenant_id == tid,
            TestCase.requirement_id == req_id,
            TestCase.deleted_at.is_(None),
        ).order_by(TestCase.updated_at.desc()).all()
        tcs_data = [{
            "id": t.id, "title": t.title, "status": t.status,
            "visibility": t.visibility,
            "coverage_type": getattr(t, "coverage_type", None),
            "generation_batch_id": getattr(t, "generation_batch_id", None),
            "updated_at": t.updated_at.isoformat() if t.updated_at else "",
        } for t in tcs]

        # Latest generation batch for this requirement (by this user or any
        # user if none of mine). Surfaces the AI's rationale + cost.
        from primeqa.test_management.models import GenerationBatch
        latest_batch = (db.query(GenerationBatch)
                        .filter(GenerationBatch.tenant_id == tid,
                                GenerationBatch.requirement_id == req_id)
                        .order_by(GenerationBatch.created_at.desc())
                        .first())
        is_superadmin = request.user.get("role") == "superadmin"
        batch_data = None
        if latest_batch:
            batch_data = {
                "id": latest_batch.id,
                "explanation": latest_batch.explanation,
                "coverage_types": latest_batch.coverage_types or [],
                "model": latest_batch.llm_model,
                "created_at": latest_batch.created_at.isoformat() if latest_batch.created_at else None,
                # Cost + tokens only surfaced to superadmin; keep the
                # flat user experience clean of $$ and internal plumbing.
                "input_tokens": latest_batch.input_tokens if is_superadmin else None,
                "output_tokens": latest_batch.output_tokens if is_superadmin else None,
                "cost_usd": float(latest_batch.cost_usd) if (is_superadmin and latest_batch.cost_usd is not None) else None,
            }

        envs = EnvironmentRepository(db).list_environments(
            tid, request.user["id"], request.user["role"])
        envs_data = [{
            "id": e.id, "name": e.name,
            "has_llm": bool(e.llm_connection_id),
            "has_meta": bool(e.current_meta_version_id),
            "ready": bool(e.llm_connection_id and e.current_meta_version_id),
        } for e in envs]

        req_data = {
            "id": req.id, "jira_key": req.jira_key,
            "jira_summary": req.jira_summary or "",
            "jira_description": req.jira_description or "",
            "acceptance_criteria": req.acceptance_criteria or "",
            "source": req.source, "is_stale": req.is_stale,
            "jira_version": req.jira_version,
            "jira_last_synced": req.jira_last_synced.isoformat() if req.jira_last_synced else None,
            "created_at": req.created_at.isoformat() if req.created_at else "",
            "updated_at": req.updated_at.isoformat() if req.updated_at else "",
            "deleted_at": req.deleted_at.isoformat() if req.deleted_at else None,
            "section_id": req.section_id,
            "section_name": section.name if section else None,
            "version": req.version,
        }
        return render_template("requirements/detail.html", **ctx(
            active_page="requirements", req=req_data,
            test_cases=tcs_data, environments=envs_data,
            generation_batch=batch_data,
        ))
    finally:
        db.close()


@views_bp.route("/requirements/<int:req_id>/edit", methods=["POST"])
@role_required("admin", "tester", "superadmin")
def requirements_edit(req_id):
    """Inline edit for acceptance_criteria + summary (for manual requirements
    where Jira is not the source of truth)."""
    from flask import flash
    db = next(get_db())
    try:
        from primeqa.test_management.repository import RequirementRepository
        repo = RequirementRepository(db)
        tid = request.user["tenant_id"]
        updates = {}
        if request.form.get("acceptance_criteria") is not None:
            updates["acceptance_criteria"] = request.form["acceptance_criteria"]
        if request.form.get("jira_summary") is not None:
            updates["jira_summary"] = request.form["jira_summary"]
        if request.form.get("jira_description") is not None:
            updates["jira_description"] = request.form["jira_description"]
        if request.form.get("is_stale") == "0":
            updates["is_stale"] = False

        _req, result = repo.update_requirement(req_id, tid, updates)
        if result == "not_found":
            flash("Requirement not found", "error")
        elif result == "conflict":
            flash("Conflict: someone edited this requirement \u2014 please refresh", "error")
        else:
            flash("Requirement updated", "success")
        return redirect(f"/requirements/{req_id}")
    finally:
        db.close()


@views_bp.route("/requirements/new", methods=["POST"])
@role_required("admin", "tester")
def requirements_create_manual():
    """Create a manual (non-Jira) requirement from the + New Requirement
    modal on /requirements.

    Maps the form's `title` into jira_summary so the list/detail views
    (which render jira_summary as the headline) keep working uniformly
    for manual and Jira-sourced rows. `source='manual'` tells the detail
    view not to show Re-sync, and the title-partial-unique index is
    skipped because jira_key is NULL.
    """
    from flask import flash
    db = next(get_db())
    try:
        from primeqa.test_management.repository import (
            SectionRepository, RequirementRepository, TestCaseRepository,
            TestSuiteRepository, BAReviewRepository, MetadataImpactRepository,
        )
        from primeqa.test_management.service import TestManagementService
        svc = TestManagementService(
            SectionRepository(db), RequirementRepository(db),
            TestCaseRepository(db), TestSuiteRepository(db),
            BAReviewRepository(db), MetadataImpactRepository(db),
        )
        title = (request.form.get("title") or "").strip()
        section_id = request.form.get("section_id", type=int)
        if not title:
            flash("Title is required.", "error")
            return redirect("/requirements")
        if not section_id:
            flash("Section is required.", "error")
            return redirect("/requirements")
        description = (request.form.get("description") or "").strip() or None
        acceptance = (request.form.get("acceptance_criteria") or "").strip() or None
        jira_key = (request.form.get("jira_key") or "").strip() or None

        result = svc.create_requirement(
            tenant_id=request.user["tenant_id"],
            section_id=section_id,
            source="manual",
            created_by=request.user["id"],
            jira_key=jira_key,
            jira_summary=title,
            jira_description=description,
            acceptance_criteria=acceptance,
        )
        flash(f"Created requirement: {title}", "success")
        req_id = result.get("id") if isinstance(result, dict) else getattr(result, "id", None)
        if req_id:
            return redirect(f"/requirements/{req_id}")
    except Exception as e:
        flash(f"Could not create requirement: {e}", "error")
    finally:
        db.close()
    return redirect("/requirements")


@views_bp.route("/requirements/import-jira", methods=["POST"])
@role_required("admin", "tester")
def requirements_import_jira():
    """Import one or many Jira tickets as requirements.

    Accepts either:
      - jira_key  (single-ticket legacy path)
      - jira_keys (comma/newline-separated list from the chip picker)
    Reports imported / skipped (already exists) / failed counts via flash.
    """
    from flask import flash
    db = next(get_db())
    try:
        from primeqa.test_management.repository import (
            SectionRepository, RequirementRepository, TestCaseRepository,
            TestSuiteRepository, BAReviewRepository, MetadataImpactRepository,
        )
        from primeqa.test_management.service import TestManagementService
        svc = TestManagementService(
            SectionRepository(db), RequirementRepository(db),
            TestCaseRepository(db), TestSuiteRepository(db),
            BAReviewRepository(db), MetadataImpactRepository(db),
        )
        conn_id = int(request.form["jira_connection_id"])
        conn_data = ConnectionRepository(db).get_connection_decrypted(conn_id, request.user["tenant_id"])
        if not conn_data:
            flash("Jira connection not found", "error")
            return redirect("/requirements")
        cfg = conn_data["config"]
        jira_auth = None
        if cfg.get("auth_type") == "basic" and cfg.get("username") and cfg.get("api_token"):
            import base64
            jira_auth = base64.b64encode(f"{cfg['username']}:{cfg['api_token']}".encode()).decode()

        # Parse keys \u2014 either `jira_keys` (multi from chip picker, comma-
        # or newline-separated) or legacy `jira_key` (single).
        raw_multi = (request.form.get("jira_keys") or "").strip()
        if raw_multi:
            import re
            keys = [k.strip() for k in re.split(r"[\s,]+", raw_multi) if k.strip()]
        else:
            single = (request.form.get("jira_key") or "").strip()
            keys = [single] if single else []

        # Dedupe while preserving order
        seen = set()
        keys = [k for k in keys if not (k in seen or seen.add(k))]

        if not keys:
            flash("No Jira keys provided.", "error")
            return redirect("/requirements")

        section_id = int(request.form["section_id"])
        tenant_id = request.user["tenant_id"]
        base_url = cfg.get("base_url", "")

        imported, skipped, failed = [], [], []
        for key in keys:
            try:
                svc.import_jira_requirement(
                    tenant_id=tenant_id,
                    section_id=section_id,
                    jira_base_url=base_url,
                    jira_key=key,
                    created_by=request.user["id"],
                    jira_auth=jira_auth,
                )
                imported.append(key)
            except ValueError as ve:
                # "already exists" is the common skip case
                if "already exists" in str(ve).lower():
                    skipped.append(key)
                else:
                    failed.append((key, str(ve)))
            except Exception as ex:
                failed.append((key, str(ex)[:100]))

        # One consolidated flash per outcome
        if imported:
            flash(f"Imported {len(imported)}: {', '.join(imported)}", "success")
        if skipped:
            flash(f"Skipped {len(skipped)} already-imported: {', '.join(skipped)}", "info")
        if failed:
            detail = "; ".join(f"{k} \u2014 {err}" for k, err in failed)
            flash(f"Failed {len(failed)}: {detail}", "error")
    except Exception as e:
        flash(f"Import failed: {e}", "error")
    finally:
        db.close()
    return redirect("/requirements")


@views_bp.route("/requirements/<int:req_id>/run", methods=["POST"])
@role_required("admin", "tester")
def requirements_run(req_id):
    """Queue a run of every active TC linked to this requirement. Tenant-
    scoped; TCs the user can't see (private, other owner) are excluded.
    """
    from flask import flash
    db = next(get_db())
    try:
        from primeqa.test_management.models import TestCase
        from primeqa.execution.repository import (
            PipelineRunRepository, PipelineStageRepository,
            ExecutionSlotRepository, WorkerHeartbeatRepository,
        )
        from primeqa.execution.service import PipelineService
        tid = request.user["tenant_id"]
        env_id = request.form.get("environment_id", type=int)
        if not env_id:
            flash("Pick an environment to run against.", "error")
            return redirect(f"/requirements/{req_id}")

        # Resolve TC ids visible to this user under this requirement
        tcs = db.query(TestCase).filter(
            TestCase.tenant_id == tid,
            TestCase.requirement_id == req_id,
            TestCase.deleted_at.is_(None),
            ((TestCase.visibility == "shared") |
             (TestCase.owner_id == request.user["id"])),
        ).all()
        if not tcs:
            flash("No runnable test cases found for this requirement.", "error")
            return redirect(f"/requirements/{req_id}")

        svc = PipelineService(
            PipelineRunRepository(db), PipelineStageRepository(db),
            ExecutionSlotRepository(db), WorkerHeartbeatRepository(db),
        )
        result = svc.create_run(
            tenant_id=tid, environment_id=env_id,
            triggered_by=request.user["id"],
            run_type="execute_only",
            source_type="requirements",
            source_ids=[req_id],
            priority="normal",
            source_refs={"requirement_ids": [req_id],
                         "test_case_ids": [t.id for t in tcs]},
        )
        flash(f"Run #{result['id']} queued ({len(tcs)} test case"
              f"{'s' if len(tcs) != 1 else ''})", "success")
        return redirect(f"/runs/{result['id']}")
    except Exception as e:
        flash(f"Could not queue run: {e}", "error")
        return redirect(f"/requirements/{req_id}")
    finally:
        db.close()


@views_bp.route("/requirements/<int:req_id>/generate", methods=["POST"])
@role_required("admin", "tester")
def requirements_generate(req_id):
    from flask import flash
    db = next(get_db())
    try:
        from primeqa.test_management.repository import (
            SectionRepository, RequirementRepository, TestCaseRepository,
            TestSuiteRepository, BAReviewRepository, MetadataImpactRepository,
        )
        from primeqa.test_management.service import TestManagementService
        from primeqa.metadata.repository import MetadataRepository
        svc = TestManagementService(
            SectionRepository(db), RequirementRepository(db),
            TestCaseRepository(db), TestSuiteRepository(db),
            BAReviewRepository(db), MetadataImpactRepository(db),
        )
        svc.review_repo = BAReviewRepository(db)
        env_id = int(request.form["environment_id"])
        try:
            # Generate a multi-TC test plan instead of a single test. Click
            # count stays the same; coverage breadth jumps (positive +
            # negative + boundary + edge + regression). See migration 028
            # and generate_test_plan in test_management/service.py.
            plan = svc.generate_test_plan(
                tenant_id=request.user["tenant_id"],
                requirement_id=req_id,
                environment_id=env_id,
                created_by=request.user["id"],
                env_repo=EnvironmentRepository(db),
                conn_repo=ConnectionRepository(db),
                metadata_repo=MetadataRepository(db),
            )
        except ValueError as e:
            # Actionable flash for the common blocker: no metadata yet / no LLM
            msg = str(e)
            if "metadata version" in msg.lower() or "refresh metadata" in msg.lower():
                flash(f"Generation blocked: {msg} Refresh the env's metadata from "
                      f"Settings \u2192 Environments \u2192 this env.", "error")
            elif "llm" in msg.lower():
                flash(f"Generation blocked: {msg} Attach an LLM connection in "
                      f"Settings \u2192 Environments \u2192 this env.", "error")
            else:
                flash(msg, "error")
            return redirect(f"/requirements/{req_id}")

        tcs = plan.get("test_cases", [])
        n = len(tcs)
        cov_counts = {}
        for tc in tcs:
            cov_counts[tc["coverage_type"]] = cov_counts.get(tc["coverage_type"], 0) + 1
        cov_breakdown = ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in sorted(cov_counts.items()))
        superseded = plan.get("superseded_count", 0)

        if n == 0:
            flash("Generator produced no test cases. Try regenerating.", "error")
        else:
            parts = [f"Generated {n} test case{'s' if n != 1 else ''}"]
            if cov_breakdown:
                parts.append(f"({cov_breakdown})")
            if superseded:
                parts.append(f"\u2014 superseded {superseded} stale draft{'s' if superseded != 1 else ''}")
            if plan.get("auto_reviews_created"):
                parts.append(f"\u2014 {plan['auto_reviews_created']} auto-assigned for BA review")
            flash(" ".join(parts) + ".", "success")
        return redirect(f"/requirements/{req_id}")
    except Exception as e:
        # Turn common upstream errors into actionable UX instead of raw
        # tracebacks. The noisiest is Anthropic returning 400 with
        # "credit balance too low" buried inside a nested JSON body.
        msg = str(e)
        lower = msg.lower()
        if "credit balance" in lower and "anthropic" in lower:
            flash("Generation blocked: your Anthropic account is out of credits. "
                  "Top up at https://console.anthropic.com/settings/billing "
                  "and try again. No test cases were created.", "error")
        elif "invalid x-api-key" in lower or "authentication_error" in lower:
            flash("Generation blocked: the LLM connection's API key is invalid. "
                  "Update it in Settings \u2192 Connections.", "error")
        elif "tenant limit" in lower or "tenant daily spend" in lower:
            # Hit by the per-tenant rate limiter in LLMGateway (Phase 2).
            flash(f"Generation paused: {msg}. Contact your administrator "
                  "to raise your tenant's LLM cap.", "error")
        elif "rate_limit" in lower or "rate limit" in lower or "429" in msg:
            flash("Generation blocked: Anthropic rate limit hit. Wait a minute "
                  "and retry, or reduce bulk size.", "error")
        else:
            flash(f"Generation failed: {msg[:400]}", "error")
    finally:
        db.close()
    return redirect(f"/requirements/{req_id}")


# --- Suites ---

@views_bp.route("/suites")
@login_required
def suites_list():
    db = next(get_db())
    try:
        from primeqa.test_management.repository import TestSuiteRepository
        repo = TestSuiteRepository(db)
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 20, type=int)
        q = (request.args.get("q") or "").strip()
        sort = request.args.get("sort", "updated_at")
        order = request.args.get("order", "desc")
        show_deleted = request.args.get("deleted", "").lower() in ("1", "true", "yes")
        filters = {}
        if request.args.get("suite_type"):
            filters["suite_type"] = request.args.get("suite_type")

        try:
            result = repo.list_page(
                request.user["tenant_id"],
                page=page, per_page=per_page, q=q, sort=sort, order=order,
                filters=filters, include_deleted=show_deleted,
            )
            suites = result.items
            meta = {"total": result.total, "page": result.page,
                    "per_page": result.per_page, "total_pages": result.total_pages}
            query_error = None
        except Exception as e:
            suites, meta, query_error = [], {"total": 0, "page": 1, "per_page": per_page, "total_pages": 0}, str(e)

        # Load TC count + coverage breakdown + requirement count per suite
        # in a single JOIN so the list doesn't N+1 as tenants grow.
        counts_by_suite = repo.get_counts_by_suite([s.id for s in suites])

        suites_data = []
        for s in suites:
            counts = counts_by_suite.get(s.id, {
                "total": 0, "coverage": {}, "requirement_count": 0,
            })
            suites_data.append({
                "id": s.id, "name": s.name, "suite_type": s.suite_type,
                "description": s.description,
                "updated_at": s.updated_at.isoformat() if s.updated_at else "",
                "created_at": s.created_at.isoformat() if s.created_at else "",
                "tc_count": counts["total"],
                "coverage_counts": counts["coverage"],
                "requirement_count": counts["requirement_count"],
            })
        return render_template("suites/list.html", **ctx(
            active_page="suites", suites=suites_data,
            meta=meta, search=q, sort=sort, order=order,
            show_deleted=show_deleted, query_error=query_error,
        ))
    finally:
        db.close()


@views_bp.route("/suites", methods=["POST"])
@role_required("admin", "tester")
def suites_create():
    from flask import flash
    db = next(get_db())
    try:
        from primeqa.test_management.repository import TestSuiteRepository
        repo = TestSuiteRepository(db)
        repo.create_suite(
            request.user["tenant_id"], request.form["name"],
            request.form.get("suite_type", "custom"), request.user["id"],
            description=request.form.get("description"),
        )
        flash("Suite created", "success")
    except Exception as e:
        flash(str(e), "error")
    finally:
        db.close()
    return redirect("/suites")


@views_bp.route("/suites/<int:suite_id>")
@login_required
def suites_detail(suite_id):
    """Suite detail with full curate-and-run UX: add/remove/reorder TCs,
    edit metadata, coverage breakdown, requirements-covered summary."""
    db = next(get_db())
    try:
        from primeqa.test_management.repository import (
            TestSuiteRepository, TestCaseRepository, RequirementRepository,
        )
        suite_repo = TestSuiteRepository(db)
        tc_repo = TestCaseRepository(db)
        req_repo = RequirementRepository(db)
        tid = request.user["tenant_id"]

        suite = suite_repo.get_suite(suite_id, tid)
        if not suite:
            return redirect("/suites")

        stcs = suite_repo.get_suite_test_cases(suite_id)
        # Load all TC rows in one query to avoid N+1
        from primeqa.test_management.models import TestCase
        tc_ids = [s.test_case_id for s in stcs]
        tc_rows = []
        if tc_ids:
            tc_rows = db.query(TestCase).filter(
                TestCase.id.in_(tc_ids), TestCase.tenant_id == tid,
                TestCase.deleted_at.is_(None),
            ).all()
        tc_by_id = {t.id: t for t in tc_rows}

        # Preserve suite ordering (stcs is position-ordered)
        test_cases = []
        coverage_counts = {}
        requirement_ids = set()
        for stc in stcs:
            tc = tc_by_id.get(stc.test_case_id)
            if not tc:
                continue
            cov = tc.coverage_type or None
            if cov:
                coverage_counts[cov] = coverage_counts.get(cov, 0) + 1
            if tc.requirement_id:
                requirement_ids.add(tc.requirement_id)
            test_cases.append({
                "id": tc.id, "title": tc.title, "status": tc.status,
                "visibility": tc.visibility, "coverage_type": cov,
                "requirement_id": tc.requirement_id,
                "position": stc.position,
            })

        # Summary: which requirements this suite covers
        reqs_by_id = req_repo.get_requirements_by_ids(
            requirement_ids, tid, include_deleted=True,
        )
        requirements_covered = [{
            "id": rid,
            "jira_key": reqs_by_id[rid].jira_key if rid in reqs_by_id else None,
            "summary": (reqs_by_id[rid].jira_summary if rid in reqs_by_id else None) or f"Requirement #{rid}",
            "deleted": bool(reqs_by_id[rid].deleted_at) if rid in reqs_by_id else False,
        } for rid in sorted(requirement_ids)]

        # All active requirements in this tenant, minimal shape. Passed to
        # the picker modal as a data-attribute so the client can group the
        # flat /api/test-cases response by requirement without a second
        # fetch. Small payload \u2014 usually < 100 rows.
        from primeqa.test_management.models import Requirement
        all_req_rows = db.query(Requirement).filter(
            Requirement.tenant_id == tid, Requirement.deleted_at.is_(None),
        ).order_by(Requirement.jira_key.asc().nullslast(),
                   Requirement.jira_summary.asc()).all()
        all_requirements = [{
            "id": r.id,
            "jira_key": r.jira_key,
            "summary": r.jira_summary or f"Requirement #{r.id}",
        } for r in all_req_rows]

        envs = EnvironmentRepository(db).list_environments(
            tid, request.user["id"], request.user["role"],
        )
        envs_data = [{"id": e.id, "name": e.name} for e in envs]
        suite_data = {
            "id": suite.id, "name": suite.name, "suite_type": suite.suite_type,
            "description": suite.description,
            "created_at": suite.created_at.isoformat() if getattr(suite, "created_at", None) else None,
            "updated_at": suite.updated_at.isoformat() if getattr(suite, "updated_at", None) else None,
        }
        return render_template("suites/detail.html", **ctx(
            active_page="suites", suite=suite_data,
            test_cases=test_cases, environments=envs_data,
            coverage_counts=coverage_counts,
            requirements_covered=requirements_covered,
            all_requirements=all_requirements,
        ))
    finally:
        db.close()


@views_bp.route("/suites/<int:suite_id>/run", methods=["POST"])
@role_required("admin", "tester")
def suites_run(suite_id):
    from flask import flash
    db = next(get_db())
    try:
        from primeqa.execution.repository import (
            PipelineRunRepository, PipelineStageRepository,
            ExecutionSlotRepository, WorkerHeartbeatRepository,
        )
        from primeqa.execution.service import PipelineService
        svc = PipelineService(
            PipelineRunRepository(db), PipelineStageRepository(db),
            ExecutionSlotRepository(db), WorkerHeartbeatRepository(db),
        )
        result = svc.create_run(
            tenant_id=request.user["tenant_id"],
            environment_id=int(request.form["environment_id"]),
            triggered_by=request.user["id"],
            run_type="execute_only",
            source_type="suite",
            source_ids=[suite_id],
            priority="normal",
        )
        flash(f"Suite run #{result['id']} queued", "success")
        return redirect(f"/runs/{result['id']}")
    except Exception as e:
        flash(str(e), "error")
        return redirect(f"/suites/{suite_id}")
    finally:
        db.close()


# --- Milestones ---

@views_bp.route("/milestones")
@login_required
def milestones_list():
    db = next(get_db())
    try:
        from primeqa.test_management.models import Milestone
        tid = request.user["tenant_id"]
        page = max(1, request.args.get("page", 1, type=int))
        per_page = min(50, max(5, request.args.get("per_page", 20, type=int)))
        q = (request.args.get("q") or "").strip()
        status_filter = request.args.get("status") or None

        base = db.query(Milestone).filter(Milestone.tenant_id == tid)
        if q:
            like = f"%{q.replace('%', chr(92) + '%')}%"
            base = base.filter(Milestone.name.ilike(like, escape="\\"))
        if status_filter:
            base = base.filter(Milestone.status == status_filter)
        total = base.order_by(None).count()
        milestones = base.order_by(Milestone.due_date.asc().nullslast()) \
                         .offset((page - 1) * per_page).limit(per_page).all()
        data = [{"id": m.id, "name": m.name, "description": m.description,
                 "status": m.status, "due_date": m.due_date.isoformat() if m.due_date else None}
                for m in milestones]
        from math import ceil
        meta = {
            "total": total, "page": page, "per_page": per_page,
            "total_pages": max(1, ceil(total / per_page)) if total else 0,
        }
        return render_template("milestones/list.html", **ctx(
            active_page="milestones", milestones=data, meta=meta,
            search=q, status_filter=status_filter,
        ))
    finally:
        db.close()


# Sections list (audit finding — previously 404). Minimal management UI;
# tree view + create/rename/soft-delete. Deep organisation still happens
# inline in the Test Library where sections are embedded.
@views_bp.route("/sections")
@login_required
def sections_list():
    from primeqa.test_management.repository import (
        SectionRepository, TestCaseRepository,
    )
    from primeqa.test_management.models import Section, TestCase
    from sqlalchemy import func as sf

    db = next(get_db())
    try:
        tenant_id = request.user["tenant_id"]
        # Fetch all non-deleted sections for the tenant in one query,
        # plus a TC count per section via a separate grouped count. At
        # typical section counts (<200) this is fine; at scale, promote
        # test_case_count to a materialized column.
        rows = db.query(Section).filter(
            Section.tenant_id == tenant_id,
            Section.deleted_at.is_(None),
        ).order_by(Section.parent_id.nullsfirst(), Section.position).all()

        tc_counts = dict(db.query(
            TestCase.section_id, sf.count(TestCase.id),
        ).filter(
            TestCase.tenant_id == tenant_id,
            TestCase.deleted_at.is_(None),
            TestCase.section_id.isnot(None),
        ).group_by(TestCase.section_id).all())

        # Build a flat ordered list with depth so the template can just
        # indent rather than recursively nest divs.
        by_parent = {}
        for r in rows:
            by_parent.setdefault(r.parent_id, []).append(r)

        flat = []
        def _walk(parent_id, depth):
            for node in by_parent.get(parent_id, []):
                flat.append({
                    "id": node.id, "name": node.name,
                    "parent_id": node.parent_id,
                    "depth": depth,
                    "test_case_count": tc_counts.get(node.id, 0),
                })
                _walk(node.id, depth + 1)
        _walk(None, 0)

        return render_template("sections/list.html", **ctx(
            active_page="sections", sections=flat,
        ))
    finally:
        db.close()


@views_bp.route("/milestones", methods=["POST"])
@role_required("admin", "tester")
def milestones_create():
    from flask import flash
    from datetime import datetime as _dt
    db = next(get_db())
    try:
        from primeqa.test_management.models import Milestone
        due = request.form.get("due_date")
        due_date = _dt.fromisoformat(due) if due else None
        m = Milestone(
            tenant_id=request.user["tenant_id"],
            name=request.form["name"],
            description=request.form.get("description"),
            due_date=due_date,
            created_by=request.user["id"],
        )
        db.add(m)
        db.commit()
        flash("Milestone created", "success")
    except Exception as e:
        flash(str(e), "error")
    finally:
        db.close()
    return redirect("/milestones")


# --- Releases ---

@views_bp.route("/releases")
@login_required
def releases_list():
    db = next(get_db())
    try:
        svc = ReleaseService(ReleaseRepository(db))
        status_filter = request.args.get("status")
        releases = svc.list_releases(request.user["tenant_id"], status=status_filter)
        return render_template("releases/list.html", **ctx(
            active_page="releases", releases=releases, status_filter=status_filter,
        ))
    finally:
        db.close()


@views_bp.route("/releases/new")
@role_required("admin", "tester")
def releases_new():
    return render_template("releases/new.html", **ctx(active_page="releases", error=None))


@views_bp.route("/releases", methods=["POST"])
@role_required("admin", "tester")
def releases_create():
    from flask import flash
    db = next(get_db())
    try:
        svc = ReleaseService(ReleaseRepository(db))
        criteria = {
            "min_pass_rate": int(request.form.get("min_pass_rate", 95)),
            "max_flaky_percent": int(request.form.get("max_flaky_percent", 10)),
            "critical_tests_must_pass": "critical_tests_must_pass" in request.form,
            "no_unresolved_high_risk_impacts": "no_unresolved_high_risk_impacts" in request.form,
        }
        target_date = request.form.get("target_date") or None
        result = svc.create_release(
            request.user["tenant_id"], request.form["name"], request.user["id"],
            version_tag=request.form.get("version_tag") or None,
            description=request.form.get("description") or None,
            target_date=target_date,
            decision_criteria=criteria,
        )
        flash(f"Release '{result['name']}' created", "success")
        return redirect(f"/releases/{result['id']}")
    except ValueError as e:
        return render_template("releases/new.html", **ctx(active_page="releases", error=str(e)))
    finally:
        db.close()


@views_bp.route("/releases/<int:release_id>/run", methods=["POST"])
@role_required("admin", "tester")
def releases_run(release_id):
    """Queue a run of every test_plan item for this release."""
    from flask import flash
    db = next(get_db())
    try:
        from primeqa.release.repository import ReleaseRepository
        from primeqa.release.service import ReleaseService
        from primeqa.execution.repository import (
            PipelineRunRepository, PipelineStageRepository,
            ExecutionSlotRepository, WorkerHeartbeatRepository,
        )
        from primeqa.execution.service import PipelineService
        tid = request.user["tenant_id"]
        env_id = request.form.get("environment_id", type=int)
        priority = request.form.get("priority", "normal")
        if not env_id:
            flash("Pick an environment to run against.", "error")
            return redirect(f"/releases/{release_id}")
        rel_svc = ReleaseService(ReleaseRepository(db))
        release = rel_svc.get_release_detail(release_id, tid)
        if not release:
            flash("Release not found.", "error")
            return redirect("/releases")
        tc_ids = [item["test_case_id"] for item in release.get("test_plan", [])
                  if not item.get("deleted")]
        if not tc_ids:
            flash("Release test plan is empty.", "error")
            return redirect(f"/releases/{release_id}?tab=test_plan")

        svc = PipelineService(
            PipelineRunRepository(db), PipelineStageRepository(db),
            ExecutionSlotRepository(db), WorkerHeartbeatRepository(db),
        )
        result = svc.create_run(
            tenant_id=tid, environment_id=env_id,
            triggered_by=request.user["id"],
            run_type="execute_only",
            source_type="release",
            source_ids=[release_id],
            priority=priority,
            source_refs={"release_id": release_id, "test_case_ids": tc_ids},
        )
        flash(f"Run #{result['id']} queued ({len(tc_ids)} test case"
              f"{'s' if len(tc_ids) != 1 else ''})", "success")
        return redirect(f"/runs/{result['id']}")
    except Exception as e:
        flash(f"Could not queue run: {e}", "error")
        return redirect(f"/releases/{release_id}?tab=test_plan")
    finally:
        db.close()


@views_bp.route("/releases/<int:release_id>/evaluate-decision", methods=["POST"])
@role_required("admin", "tester")
def releases_evaluate_decision(release_id):
    from flask import flash
    from primeqa.release.decision_engine import DecisionEngine
    db = next(get_db())
    try:
        release = ReleaseRepository(db).get_release(release_id, request.user["tenant_id"])
        if not release:
            return redirect("/releases")
        engine = DecisionEngine(db)
        result = engine.evaluate(release)
        ReleaseRepository(db).create_decision(
            release_id=release_id,
            recommendation=result["recommendation"],
            confidence=result["confidence"],
            reasoning=result,
            criteria_met=result["criteria_met"],
            recommended_by="ai",
        )
        rec = result["recommendation"].upper().replace("_", " ")
        flash(f"Decision: {rec} ({int(result['confidence']*100)}% confidence)", "success")
    except Exception as e:
        flash(f"Evaluation failed: {e}", "error")
    finally:
        db.close()
    return redirect(f"/releases/{release_id}?tab=decision")


@views_bp.route("/releases/<int:release_id>/score-risks", methods=["POST"])
@role_required("admin", "tester")
def releases_score_risks(release_id):
    from flask import flash
    from primeqa.intelligence.risk_engine import RiskEngine
    db = next(get_db())
    try:
        release = ReleaseRepository(db).get_release(release_id, request.user["tenant_id"])
        if not release:
            return redirect("/releases")
        engine = RiskEngine(db)
        impact_count = engine.score_all_release_impacts(release_id)
        plan_count = engine.rank_release_test_plan(release_id)
        flash(f"Scored {impact_count} impacts, ranked {plan_count} test plan items", "success")
    except Exception as e:
        flash(f"Risk scoring failed: {e}", "error")
    finally:
        db.close()
    return redirect(f"/releases/{release_id}")


@views_bp.route("/releases/<int:release_id>")
@login_required
def releases_detail(release_id):
    db = next(get_db())
    try:
        svc = ReleaseService(ReleaseRepository(db))
        release = svc.get_release_detail(release_id, request.user["tenant_id"])
        if not release:
            return redirect("/releases")
        tab = request.args.get("tab", "requirements")

        # Picker data for the "+ Add" modals on Requirements and Test Plan
        # tabs. All active requirements + an index for client-side grouping
        # of test cases by requirement in the Test Plan picker.
        tid = request.user["tenant_id"]
        from primeqa.test_management.models import Requirement
        req_rows = db.query(Requirement).filter(
            Requirement.tenant_id == tid, Requirement.deleted_at.is_(None),
        ).order_by(Requirement.jira_key.asc().nullslast(),
                   Requirement.jira_summary.asc()).all()
        all_requirements = [{
            "id": r.id, "jira_key": r.jira_key,
            "summary": r.jira_summary or f"Requirement #{r.id}",
        } for r in req_rows]

        # Env list for the "Run test plan" modal
        envs = EnvironmentRepository(db).list_environments(
            tid, request.user["id"], request.user["role"],
        )
        envs_data = [{"id": e.id, "name": e.name} for e in envs]

        return render_template("releases/detail.html", **ctx(
            active_page="releases", release=release, tab=tab,
            all_requirements=all_requirements,
            environments=envs_data,
        ))
    finally:
        db.close()
