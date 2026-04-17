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
        tc_data = {"id": tc.id, "title": tc.title}
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
            tc_repo.update_test_case(tc_id, request.user["tenant_id"], {"title": title})

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
            "cleanup_mandatory": env.cleanup_mandatory,
        }
        return render_template("environments/detail.html", **ctx(
            active_page="settings_environments", settings_page="environments",
            breadcrumb_section="Environments", breadcrumb_item=env.name,
            env=env_data, message=request.args.get("message"),
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
@role_required("admin")
def environments_refresh_metadata(env_id):
    from flask import flash
    db = next(get_db())
    try:
        from primeqa.metadata.repository import MetadataRepository
        from primeqa.metadata.service import MetadataService
        env_repo = EnvironmentRepository(db)
        conn_repo = ConnectionRepository(db)
        env = env_repo.get_environment(env_id, request.user["tenant_id"])
        if not env:
            flash("Environment not found", "error")
            return redirect("/environments")
        if not env.connection_id:
            flash("No Salesforce connection linked — cannot refresh metadata", "error")
            return redirect(f"/environments/{env_id}")
        conn_data = conn_repo.get_connection_decrypted(env.connection_id, request.user["tenant_id"])
        if not conn_data:
            flash("Connection not found", "error")
            return redirect(f"/environments/{env_id}")
        # Do OAuth flow to get a fresh access token
        import requests as http_requests
        cfg = conn_data["config"]
        login_url = cfg.get("instance_url", "").rstrip("/")
        if not login_url:
            org_type = cfg.get("org_type", "sandbox")
            login_url = "https://test.salesforce.com" if org_type == "sandbox" else "https://login.salesforce.com"
        token_body = {"client_id": cfg.get("client_id", ""), "client_secret": cfg.get("client_secret", "")}
        if cfg.get("auth_flow") == "password":
            token_body["grant_type"] = "password"
            token_body["username"] = cfg.get("username", "")
            token_body["password"] = cfg.get("password", "")
        else:
            token_body["grant_type"] = "client_credentials"
        token_resp = http_requests.post(f"{login_url}/services/oauth2/token", data=token_body, timeout=15)
        if token_resp.status_code != 200:
            flash(f"OAuth failed: {token_resp.text[:300]}", "error")
            return redirect(f"/environments/{env_id}")
        token_data = token_resp.json()
        access_token = token_data.get("access_token", "")
        # Store fresh token on environment credentials
        env_repo.store_credentials(
            env_id,
            client_id=cfg.get("client_id", ""),
            client_secret=cfg.get("client_secret", ""),
            access_token=access_token,
        )
        meta_repo = MetadataRepository(db)
        meta_svc = MetadataService(meta_repo, env_repo)
        result = meta_svc.refresh_metadata(env_id, request.user["tenant_id"])
        flash(f"Metadata refreshed: {result['objects_count']} objects, {result['fields_count']} fields", "success")
    except Exception as e:
        flash(f"Metadata refresh failed: {e}", "error")
    finally:
        db.close()
    return redirect(f"/environments/{env_id}")


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
        reqs = req_repo.list_requirements(tid)
        sections = sec_repo.list_sections(tid)
        envs = EnvironmentRepository(db).list_environments(tid, request.user["id"], request.user["role"])
        conns = ConnectionRepository(db).list_connections(tid, "jira")
        reqs_data = [{"id": r.id, "jira_key": r.jira_key, "jira_summary": r.jira_summary,
                      "acceptance_criteria": r.acceptance_criteria, "is_stale": r.is_stale}
                     for r in reqs]
        envs_data = [{"id": e.id, "name": e.name} for e in envs if e.llm_connection_id]
        sections_data = [{"id": s.id, "name": s.name} for s in sections]
        conns_data = [{"id": c.id, "name": c.name} for c in conns]
        return render_template("requirements/list.html", **ctx(
            active_page="requirements",
            requirements=reqs_data, sections=sections_data,
            environments=envs_data, jira_connections=conns_data,
        ))
    finally:
        db.close()


@views_bp.route("/requirements/import-jira", methods=["POST"])
@role_required("admin", "tester")
def requirements_import_jira():
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
        svc.import_jira_requirement(
            tenant_id=request.user["tenant_id"],
            section_id=int(request.form["section_id"]),
            jira_base_url=cfg.get("base_url", ""),
            jira_key=request.form["jira_key"],
            created_by=request.user["id"],
            jira_auth=jira_auth,
        )
        flash(f"Imported {request.form['jira_key']}", "success")
    except ValueError as e:
        flash(str(e), "error")
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
        env_id = int(request.form["environment_id"])
        result = svc.generate_test_case(
            tenant_id=request.user["tenant_id"],
            requirement_id=req_id,
            environment_id=env_id,
            created_by=request.user["id"],
            env_repo=EnvironmentRepository(db),
            conn_repo=ConnectionRepository(db),
            metadata_repo=MetadataRepository(db),
        )
        flash(f"Generated test case with {result['steps_count']} steps ({int(result['confidence_score']*100)}% confidence)", "success")
        return redirect(f"/test-cases/{result['test_case_id']}")
    except ValueError as e:
        flash(str(e), "error")
    except Exception as e:
        flash(f"Generation failed: {e}", "error")
    finally:
        db.close()
    return redirect("/requirements")


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
