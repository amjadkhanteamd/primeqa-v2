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
            if request.user["role"] not in roles:
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
        user_count = db.query(User).filter(User.tenant_id == request.user["tenant_id"], User.is_active == True).count()
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

        stats = {
            "total_test_cases": tc_count, "runs_today": runs_today,
            "pass_rate": 0, "pending_reviews": pending,
            "user_count": user_count, "env_count": env_count,
        }
        return render_template("dashboard.html", **ctx(
            active_page="dashboard", stats=stats, recent_runs=runs_data,
            setup_complete=setup_complete,
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
        repo = PipelineRunRepository(db)
        status_filter = request.args.get("status")
        runs = repo.list_runs(request.user["tenant_id"], status=status_filter)
        runs_data = [{
            "id": r.id, "status": r.status, "run_type": r.run_type,
            "source_type": r.source_type, "priority": r.priority,
            "passed": r.passed, "total_tests": r.total_tests,
            "queued_at": r.queued_at.isoformat() if r.queued_at else "",
        } for r in runs]
        return render_template("runs/list.html", **ctx(
            active_page="runs", runs=runs_data, status_filter=status_filter,
        ))
    finally:
        db.close()


@views_bp.route("/runs/new")
@role_required("admin", "tester")
def runs_new():
    db = next(get_db())
    try:
        envs = EnvironmentRepository(db).list_environments(request.user["tenant_id"])
        envs_data = [{"id": e.id, "name": e.name, "env_type": e.env_type} for e in envs]
        return render_template("runs/new.html", **ctx(active_page="runs", environments=envs_data))
    finally:
        db.close()


@views_bp.route("/runs", methods=["POST"])
@role_required("admin", "tester")
def runs_create():
    db = next(get_db())
    try:
        from primeqa.execution.repository import PipelineRunRepository, PipelineStageRepository, ExecutionSlotRepository, WorkerHeartbeatRepository
        from primeqa.execution.service import PipelineService
        svc = PipelineService(
            PipelineRunRepository(db), PipelineStageRepository(db),
            ExecutionSlotRepository(db), WorkerHeartbeatRepository(db),
        )
        source_ids = [int(x.strip()) for x in request.form.get("source_ids", "").split(",") if x.strip()]
        result = svc.create_run(
            tenant_id=request.user["tenant_id"],
            environment_id=int(request.form["environment_id"]),
            triggered_by=request.user["id"],
            run_type=request.form.get("run_type", "full"),
            source_type=request.form.get("source_type", "requirements"),
            source_ids=source_ids,
            priority=request.form.get("priority", "normal"),
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
        }
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
        section_id = request.args.get("section_id", type=int)
        tcs = tc_repo.list_test_cases(
            request.user["tenant_id"],
            include_private_for=request.user["id"],
            section_id=section_id,
        )
        tc_data = [{
            "id": tc.id, "title": tc.title, "status": tc.status,
            "visibility": tc.visibility, "owner_id": tc.owner_id,
        } for tc in tcs]
        return render_template("test_cases/library.html", **ctx(
            active_page="test_cases", sections=sections, test_cases=tc_data,
            section_id=section_id,
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

        return render_template("test_cases/detail.html", **ctx(
            active_page="test_cases", tc=tc_data, current_version=cv_data,
            versions=versions_data,
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


# --- Reviews ---

@views_bp.route("/reviews")
@role_required("admin", "ba")
def reviews_queue():
    db = next(get_db())
    try:
        from primeqa.test_management.repository import BAReviewRepository
        repo = BAReviewRepository(db)
        reviews = repo.list_reviews(request.user["tenant_id"], assigned_to=request.user["id"])
        reviews_data = [{
            "id": r.id, "test_case_version_id": r.test_case_version_id,
            "status": r.status, "created_at": r.created_at.isoformat() if r.created_at else "",
        } for r in reviews]
        return render_template("reviews/queue.html", **ctx(
            active_page="reviews", reviews=reviews_data,
        ))
    finally:
        db.close()


@views_bp.route("/reviews/<int:review_id>")
@role_required("admin", "ba")
def reviews_detail(review_id):
    db = next(get_db())
    try:
        from primeqa.test_management.repository import BAReviewRepository
        review = BAReviewRepository(db).get_review(review_id)
        if not review:
            return redirect("/reviews")
        review_data = {
            "id": review.id, "test_case_version_id": review.test_case_version_id,
            "status": review.status, "feedback": review.feedback,
            "created_at": review.created_at.isoformat() if review.created_at else "",
        }
        return render_template("reviews/detail.html", **ctx(
            active_page="reviews", review=review_data,
        ))
    finally:
        db.close()


@views_bp.route("/reviews/<int:review_id>", methods=["POST"])
@role_required("admin", "ba")
def reviews_submit(review_id):
    db = next(get_db())
    try:
        from primeqa.test_management.repository import BAReviewRepository, TestCaseRepository
        from primeqa.test_management.models import TestCaseVersion
        review_repo = BAReviewRepository(db)
        status = request.form.get("status")
        feedback = request.form.get("feedback")
        review = review_repo.update_review(review_id, status, feedback, request.user["id"])
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
        }
        return render_template("environments/detail.html", **ctx(
            active_page="settings_environments", settings_page="environments", env=env_data, message=request.args.get("message"),
        ))
    finally:
        db.close()


# --- Users ---

@views_bp.route("/users")
@role_required("admin")
def users_list():
    db = next(get_db())
    try:
        svc = AuthService(UserRepository(db), RefreshTokenRepository(db))
        users = svc.list_users(request.user["tenant_id"])
        return render_template("users/list.html", **ctx(active_page="settings_users", settings_page="users", users=users))
    finally:
        db.close()


@views_bp.route("/users/new", methods=["GET"])
@role_required("admin")
def users_new():
    return render_template("users/form.html", **ctx(active_page="settings_users", settings_page="users", edit_user=None, error=None))


@views_bp.route("/users/new", methods=["POST"])
@role_required("admin")
def users_create():
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
        return redirect("/users")
    except ValueError as e:
        return render_template("users/form.html", **ctx(active_page="settings_users", settings_page="users", edit_user=None, error=str(e)))
    finally:
        db.close()


# --- Impacts ---

@views_bp.route("/impacts")
@role_required("admin", "tester")
def impacts_list():
    db = next(get_db())
    try:
        from primeqa.test_management.repository import MetadataImpactRepository
        repo = MetadataImpactRepository(db)
        impacts = repo.list_pending_impacts(request.user["tenant_id"])
        impacts_data = [{
            "id": i.id, "test_case_id": i.test_case_id,
            "impact_type": i.impact_type, "entity_ref": i.entity_ref,
        } for i in impacts]
        return render_template("impacts/list.html", **ctx(active_page="impacts", impacts=impacts_data))
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
        return redirect("/groups")
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
