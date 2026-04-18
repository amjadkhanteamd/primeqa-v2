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
    token = request.cookies.get("access_token")
    if not token:
        return None
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return {
            "id": int(payload["sub"]),
            "tenant_id": payload["tenant_id"],
            "email": payload["email"],
            "role": payload["role"],
            "full_name": payload["full_name"],
        }
    except jwt.InvalidTokenError:
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
        result = svc.login(1, email, password)
        if not result:
            return render_template("auth/login.html", user=None, error="Invalid email or password")
        resp = make_response(redirect("/"))
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
    db = next(get_db())
    try:
        from primeqa.execution.models import PipelineRun
        from primeqa.test_management.models import TestCase, BAReview
        from primeqa.core.models import User, Environment, Connection, Group

        tc_count = db.query(TestCase).filter(TestCase.tenant_id == request.user["tenant_id"]).count()
        runs_today = db.query(PipelineRun).filter(PipelineRun.tenant_id == request.user["tenant_id"]).count()
        pending = db.query(BAReview).filter(
            BAReview.tenant_id == request.user["tenant_id"], BAReview.status == "pending",
        ).count()
        user_count = db.query(User).filter(
            User.tenant_id == request.user["tenant_id"],
            User.is_active == True,
            User.role != "superadmin",
        ).count()
        env_count = db.query(Environment).filter(Environment.tenant_id == request.user["tenant_id"]).count()
        conn_count = db.query(Connection).filter(Connection.tenant_id == request.user["tenant_id"]).count()
        group_count = db.query(Group).filter(Group.tenant_id == request.user["tenant_id"]).count()
        setup_complete = conn_count > 0 and env_count > 0 and group_count > 0

        recent_runs = db.query(PipelineRun).filter(
            PipelineRun.tenant_id == request.user["tenant_id"],
        ).order_by(PipelineRun.queued_at.desc()).limit(10).all()

        runs_data = [{
            "id": r.id, "status": r.status, "run_type": r.run_type,
            "priority": r.priority, "queued_at": r.queued_at.isoformat() if r.queued_at else "",
        } for r in recent_runs]

        # Analytics
        from primeqa.execution.analytics import AnalyticsService
        analytics = AnalyticsService(db)
        tid = request.user["tenant_id"]
        overall = analytics.overall_stats(tid)
        env_pass_rates = analytics.pass_rate_by_environment(tid)
        flaky = analytics.flaky_tests(tid, limit=5)
        releases_health = analytics.release_health(tid)

        stats = {
            "total_test_cases": tc_count, "runs_today": runs_today,
            "pass_rate": overall["pass_rate_30d"], "pending_reviews": pending,
            "user_count": user_count, "env_count": env_count,
        }
        return render_template("dashboard.html", **ctx(
            active_page="dashboard", stats=stats, recent_runs=runs_data,
            setup_complete=setup_complete,
            env_pass_rates=env_pass_rates, flaky_tests=flaky, releases_health=releases_health,
        ))
    finally:
        db.close()


# --- Runs ---

@views_bp.route("/runs")
@login_required
def runs_list():
    db = next(get_db())
    try:
        from primeqa.execution.repository import PipelineRunRepository
        from primeqa.execution.models import PipelineRun
        from sqlalchemy import or_
        repo = PipelineRunRepository(db)
        status_filter = request.args.get("status")
        page = max(1, request.args.get("page", 1, type=int))
        per_page = min(50, max(5, request.args.get("per_page", 20, type=int)))
        # PipelineRunRepository.list_runs supports limit/offset but returns a
        # plain list; we compute the count ourselves so we can render proper
        # pagination. Status filter reused as-is.
        base = db.query(PipelineRun).filter(PipelineRun.tenant_id == request.user["tenant_id"])
        if status_filter:
            base = base.filter(PipelineRun.status == status_filter)
        total = base.order_by(None).count()
        runs = base.order_by(PipelineRun.queued_at.desc()) \
                   .offset((page - 1) * per_page).limit(per_page).all()

        runs_data = [{
            "id": r.id, "status": r.status, "run_type": r.run_type,
            "source_type": r.source_type, "priority": r.priority,
            "passed": r.passed, "failed": r.failed, "total_tests": r.total_tests,
            "queued_at": r.queued_at.isoformat() if r.queued_at else "",
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        } for r in runs]
        from math import ceil
        meta = {
            "total": total, "page": page, "per_page": per_page,
            "total_pages": max(1, ceil(total / per_page)) if total else 0,
        }
        return render_template("runs/list.html", **ctx(
            active_page="runs", runs=runs_data,
            status_filter=status_filter, meta=meta,
        ))
    finally:
        db.close()


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
        results_data = []
        for r in results:
            steps = step_repo.list_step_results(r.id)
            results_data.append({
                "test_case_id": r.test_case_id, "status": r.status,
                "failure_summary": r.failure_summary,
                "steps": [{"step_order": s.step_order, "step_action": s.step_action,
                           "target_object": s.target_object, "status": s.status,
                           "error_message": s.error_message} for s in steps],
            })
        return render_template("runs/detail.html", **ctx(
            active_page="runs", run=run_data, stages=stages_data, results=results_data,
            agent_fixes=agent_fixes_data,
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
    db = next(get_db())
    try:
        from primeqa.test_management.repository import SectionRepository, TestCaseRepository
        section_repo = SectionRepository(db)
        tc_repo = TestCaseRepository(db)

        sections = section_repo.get_section_tree(request.user["tenant_id"])

        # Parse list params — page, per_page (capped 50), q, sort, order, filters
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 20, type=int)
        q = (request.args.get("q") or "").strip()
        sort = request.args.get("sort", "updated_at")
        order = request.args.get("order", "desc")
        section_id = request.args.get("section_id", type=int)
        status = request.args.get("status") or None
        show_deleted = request.args.get("deleted", "").lower() in ("1", "true", "yes")

        filters = {}
        if section_id:
            filters["section_id"] = section_id
        if status:
            filters["status"] = status

        try:
            result = tc_repo.list_page(
                request.user["tenant_id"], user_id=request.user["id"],
                page=page, per_page=per_page, q=q, sort=sort, order=order,
                filters=filters, include_deleted=show_deleted,
            )
            query_error = None
            tc_rows = result.items
            meta = {
                "total": result.total, "page": result.page,
                "per_page": result.per_page, "total_pages": result.total_pages,
            }
        except Exception as e:
            # Bad sort field etc. — show empty list with a toast and keep the page usable
            query_error = str(e)
            tc_rows = []
            meta = {"total": 0, "page": 1, "per_page": per_page, "total_pages": 0}

        tc_data = [{
            "id": tc.id, "title": tc.title, "status": tc.status,
            "visibility": tc.visibility, "owner_id": tc.owner_id,
            "updated_at": tc.updated_at.isoformat() if tc.updated_at else "",
            "deleted_at": tc.deleted_at.isoformat() if getattr(tc, "deleted_at", None) else None,
            "coverage_type": getattr(tc, "coverage_type", None),
            "generation_batch_id": getattr(tc, "generation_batch_id", None),
            "requirement_id": tc.requirement_id,
        } for tc in tc_rows]

        return render_template("test_cases/library.html", **ctx(
            active_page="test_cases", sections=sections, test_cases=tc_data,
            section_id=section_id, meta=meta,
            search=q, sort=sort, order=order, status_filter=status,
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
        }
        cv_data = None
        if current_version:
            cv_data = {
                "version_number": current_version.version_number,
                "generation_method": current_version.generation_method,
                "steps": current_version.steps or [],
                "referenced_entities": current_version.referenced_entities or [],
                "confidence_score": current_version.confidence_score,
            }
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
    return redirect("/users")


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
        return render_template("connections/detail.html", **ctx(
            active_page="settings_connections", settings_page="connections", conn=conn,
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

@views_bp.route("/settings/users")
@role_required("admin")
def settings_users(): return redirect("/users")


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

        reqs_data = [{
            "id": r.id, "jira_key": r.jira_key, "jira_summary": r.jira_summary,
            "acceptance_criteria": r.acceptance_criteria, "is_stale": r.is_stale,
            "source": r.source,
            "updated_at": r.updated_at.isoformat() if r.updated_at else "",
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
        flash(f"Generation failed: {e}", "error")
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

        suites_data = [{"id": s.id, "name": s.name, "suite_type": s.suite_type,
                        "description": s.description,
                        "updated_at": s.updated_at.isoformat() if s.updated_at else "",
                        "created_at": s.created_at.isoformat() if s.created_at else ""}
                       for s in suites]
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
    db = next(get_db())
    try:
        from primeqa.test_management.repository import TestSuiteRepository, TestCaseRepository
        suite_repo = TestSuiteRepository(db)
        tc_repo = TestCaseRepository(db)
        suite = suite_repo.get_suite(suite_id, request.user["tenant_id"])
        if not suite:
            return redirect("/suites")
        stcs = suite_repo.get_suite_test_cases(suite_id)
        test_cases = []
        for stc in stcs:
            tc = tc_repo.get_test_case(stc.test_case_id, request.user["tenant_id"])
            if tc:
                test_cases.append({"id": tc.id, "title": tc.title, "status": tc.status})
        envs = EnvironmentRepository(db).list_environments(
            request.user["tenant_id"], request.user["id"], request.user["role"],
        )
        envs_data = [{"id": e.id, "name": e.name} for e in envs]
        suite_data = {"id": suite.id, "name": suite.name, "suite_type": suite.suite_type,
                      "description": suite.description}
        return render_template("suites/detail.html", **ctx(
            active_page="suites", suite=suite_data, test_cases=test_cases, environments=envs_data,
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
        return render_template("releases/detail.html", **ctx(
            active_page="releases", release=release, tab=tab,
        ))
    finally:
        db.close()
