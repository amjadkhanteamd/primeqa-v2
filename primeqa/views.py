"""Web UI views — server-rendered templates with Jinja2 + HTMX.

All pages require authentication via JWT cookie except /login.
"""

import os
from functools import wraps

import jwt
from flask import Blueprint, render_template, request, redirect, url_for, make_response, jsonify

from primeqa.db import get_db
from primeqa.core.repository import (
    UserRepository, RefreshTokenRepository, EnvironmentRepository,
    ConnectionRepository, GroupRepository,
)
from primeqa.core.service import AuthService, EnvironmentService, ConnectionService, GroupService
from primeqa.core.authz import Tier, authorize, floor_tier, rank
from primeqa.core.auth import require_tier_api
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
    """D-245 Phase 6: the legacy role list is now a thin wrapper over the role
    ladder. It gates at ``floor_tier(roles)`` (the lowest listed role's tier)
    via the same ``authorize()`` path as ``require_tier`` — so superadmin still
    passes (ladder top), and a list naming ``tester`` now also admits ``ba``
    (both ``MEMBER``), the one intended widening. The explicit role names stay
    at the call sites as living documentation of audience."""
    return require_tier(floor_tier(roles))


def require_tier(min_tier):
    """D-245 Phase-2 transitional role gate — the new ``authorize()`` path,
    applied as the **OUTER** decorator (fail-closed: a below-tier caller is
    rejected before reaching the inner permission gate). Web semantics: it
    enforces auth (``login_required``) then the role tier, redirecting to ``/``
    on deny (mirrors ``role_required``). Left in place after Phase 5 deletes the
    permission layer — it is the route's permanent replacement gate."""
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated(*args, **kwargs):
            allow, _reason = authorize(getattr(request, "user", None), min_tier)
            if not allow:
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

        # Route the user to the landing page their ROLE unlocks (or their saved
        # preference, if still reachable). D-245: capabilities are role-derived
        # now — there is no permission-set union.
        from primeqa.core.permissions import _role_capabilities
        from primeqa.core.navigation import get_landing_page
        from primeqa.core.models import User

        user_row = db.query(User).filter_by(id=result["user"]["id"]).first()
        is_superadmin = (user_row.role == "superadmin") if user_row else False
        perms = _role_capabilities(user_row.role if user_row else None)
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
        tid = request.user["tenant_id"]

        # One CTE-free roll-up: every count lives in its own scalar
        # subquery. Postgres parallelises these on a single
        # round-trip; the total is ~1 RTT instead of 7.
        row = db.execute(sql("""
            SELECT
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

        # D-219: the landing metrics read substrate evidence — claims,
        # s4 runs, latest-per-claim pass rate, flake-flagged claims. The
        # v1 product-table counts froze when the engine moved.
        from primeqa.intelligence.substrate_dashboard import (
            get_landing_substrate_stats,
        )
        sub = get_landing_substrate_stats(tid)

        stats = {
            "total_test_cases": sub["approved_claims"],
            "runs_today": sub["runs_today"],
            "pass_rate": sub["pass_rate"],
            "pending_reviews": sub["draft_claims"],
            "user_count": row["user_count"],
            "env_count": row["env_count"],
        }
        return render_template("dashboard.html", **ctx(
            active_page="dashboard", stats=stats,
            recent_runs=sub["recent_runs"],
            setup_complete=setup_complete,
            env_pass_rates=[], flaky_tests=[],
            flaky_claims=sub["flaky_claims"], releases_health=[],
        ))
    finally:
        db.close()


# --- Release Owner Dashboard (Prompt 10) ----------------------------------
# Import here (not at module top) so the symbol is defined before the
# decorators below try to use it. The admin-UI block defines the same
# alias later; Python is fine with the re-import.
from primeqa.core.auth import require_auth as _require_auth_api  # noqa: E402


@views_bp.route("/dashboard")
@require_tier(Tier.VIEWER)
@login_required
def release_dashboard():
    """Release Owner's executive view. Answers 'is it safe to release?'
    in 5 seconds: hero Go/No-Go, ticket grid, quality gates, sprint
    trend, intelligence summary."""
    from primeqa.core.models import User
    # D-219: the dashboard reads substrate evidence (v1-shaped drop-in).
    from primeqa.intelligence.substrate_dashboard import (
        get_substrate_dashboard_data as get_dashboard_data,
    )
    from primeqa.runs.my_tickets import (
        resolve_active_environment, list_switchable_environments,
    )

    def _render():
        db = next(get_db())
        try:
            user_row = db.query(User).filter_by(id=request.user["id"]).first()
            env = resolve_active_environment(user_row, db)
            if env is None:
                return render_template("dashboard_release.html", **ctx(
                    active_page="dashboard",
                    data={"environment": None, "empty": True},
                    envs=[],
                    empty_reason="no_environment",
                ))
            data = get_dashboard_data(env.id, request.user["tenant_id"], db)
            envs = list_switchable_environments(user_row, db)
            return render_template("dashboard_release.html", **ctx(
                active_page="dashboard",
                data=data, envs=envs, env=env,
                empty_reason=None,
            ))
        finally:
            db.close()

    return _render()


@views_bp.route("/substrate-insights")
@require_tier(Tier.VIEWER)
@login_required
def substrate_insights():
    """Cutover Step 2 (D-155): the additive substrate-insights read surface.

    S6 interpretations + cross-run clustering + S8 grounding-validity verdicts,
    tenant-scoped + best-effort (the substrate read can never break this page).
    Renders an empty-state until the live-SF sync populates S1 + the first
    runs/recompute ticks land. Gated on ``view_intelligence_report`` (ba + admin
    + superadmin)."""
    from primeqa.intelligence.substrate_insights import get_substrate_insights

    def _render():
        insights = get_substrate_insights(request.user["tenant_id"])
        return render_template("substrate_insights.html", **ctx(
            active_page="substrate_insights", insights=insights))

    return _render()


@views_bp.route("/knowledge")
@require_tier(Tier.MEMBER)
@login_required
def knowledge():
    """UI Area 7 (D-176): read-only knowledge admin over the S5 substrate — System
    Rules (file-backed) + Domain Packs (trusted git-controlled ``.md``) + Learned
    Rules (per-tenant, signal-derived). Gated on ``manage_knowledge``. **Read-only
    by contract**: rule/pack content is authored via git PR, never a UI write (the
    trusted-content boundary — prompt-injection defence). Best-effort: the S5 read
    can never break this page."""
    from primeqa.intelligence.knowledge_console import get_knowledge_overview

    def _render():
        knowledge = get_knowledge_overview(request.user["tenant_id"])
        return render_template("knowledge.html", **ctx(
            active_page="knowledge", knowledge=knowledge))

    return _render()


def _resolve_env_llm(db, tenant_id, environment_id):
    """Best-effort resolve an env's LLM api_key + model — (None, None) on any miss.
    When unresolved, the S7 bridge phrases nothing and degrades to a
    refused-with-citations (S7 still answers refusals without an LLM)."""
    if not environment_id:
        return None, None
    try:
        env = EnvironmentRepository(db).get_environment(environment_id, tenant_id)
        if env and getattr(env, "llm_connection_id", None):
            conn = ConnectionRepository(db).get_connection_decrypted(
                env.llm_connection_id, tenant_id)
            if conn:
                cfg = conn.get("config") or {}
                return cfg.get("api_key"), cfg.get("model")
    except Exception:
        pass
    return None, None


@views_bp.route("/ask", methods=["GET", "POST"])
@require_tier(Tier.VIEWER)
@login_required
def ask():
    """S7 grounded answering (D-163.4): ask a question about the test system; get an
    answer grounded in substrate evidence, or a refusal. Gated on
    ``view_intelligence_report`` (ba + admin + superadmin). The substrate answer
    stores are empty until the live-SF sync + first runs land, so the default
    result is a grounded refusal — correct behaviour."""
    from primeqa.intelligence.conversation_bridge import answer_question

    def _render():
        tid = request.user["tenant_id"]
        db = next(get_db())
        # Phase 3 (D-245): scope the env dropdown to the caller's accessible set.
        environments = EnvironmentRepository(db).list_environments(
            tid, request.user["id"], request.user["role"])
        form = {"question": "", "environment_id": "", "requirement_key": "",
                "object_api_name": ""}
        answer = None
        if request.method == "POST":
            form["question"] = (request.form.get("question") or "").strip()
            form["environment_id"] = request.form.get("environment_id") or ""
            form["requirement_key"] = (request.form.get("requirement_key") or "").strip()
            form["object_api_name"] = (request.form.get("object_api_name") or "").strip()
            env_id = (int(form["environment_id"])
                      if form["environment_id"].isdigit() else None)
            # Phase 3 (D-245): drop an out-of-scope env filter so a caller can't
            # ground an answer over an environment outside their groups.
            if env_id is not None and not EnvironmentRepository(db).is_environment_accessible(
                    tid, request.user["id"], request.user["role"], env_id):
                env_id = None
                form["environment_id"] = ""
            if form["question"]:
                api_key, model = _resolve_env_llm(db, tid, env_id)
                answer = answer_question(
                    tid, form["question"], environment_id=env_id,
                    requirement_key=form["requirement_key"] or None,
                    object_api_name=form["object_api_name"] or None,
                    api_key=api_key, model=model)
        else:
            # GET prefill for contextual deep-links ("Ask about this requirement").
            # Prefill ONLY — a deep-link must never auto-run an LLM call (a GET stays
            # safe/idempotent); the user reviews the seeded question and clicks Ask.
            # Values are length-capped (URL params are link-supplied) and HTML-escaped
            # at render; environment_id only sticks if it matches a tenant env option.
            form["question"] = (request.args.get("q") or "").strip()[:500]
            form["requirement_key"] = (request.args.get("requirement_key") or "").strip()[:100]
            form["object_api_name"] = (request.args.get("object_api_name") or "").strip()[:100]
            eid = request.args.get("environment_id") or ""
            form["environment_id"] = eid if eid.isdigit() else ""
        return render_template("conversation.html", **ctx(
            active_page="ask", environments=environments, answer=answer, form=form))

    return _render()


@views_bp.route("/api/dashboard/share", methods=["POST"])
@require_tier_api(Tier.MEMBER)
@_require_auth_api
def api_dashboard_share():
    """Generate a shareable dashboard URL. Returns 201 with the
    public URL + link_id + expires_at. The raw token is included
    exactly once in the response — the server only stores the hash.

    Body: {"environment_id": int, "expires_days": int (default 30, max 180)}
    """
    from datetime import datetime, timedelta, timezone
    import secrets
    from primeqa.core.models import Environment
    from primeqa.core.permissions import SharedDashboardLink

    def _do():
        body = request.get_json(silent=True) or {}
        try:
            env_id = int(body.get("environment_id"))
        except (TypeError, ValueError):
            return ({"error": {"code": "VALIDATION_ERROR",
                               "message": "environment_id required"}}, 400)
        try:
            days = int(body.get("expires_days", 30))
        except (TypeError, ValueError):
            days = 30
        days = max(1, min(days, 180))  # clamp 1..180

        db = next(get_db())
        try:
            # Phase 3 (D-245): only share a dashboard for an env the caller can
            # access (groups), not any env in the tenant. 404 hides existence.
            if not EnvironmentRepository(db).is_environment_accessible(
                    request.user["tenant_id"], request.user["id"],
                    request.user["role"], env_id):
                return ({"error": {"code": "NOT_FOUND",
                                   "message": "Environment not found"}}, 404)
            env = (db.query(Environment)
                   .filter_by(id=env_id,
                              tenant_id=request.user["tenant_id"])
                   .first())
            if env is None:
                return ({"error": {"code": "NOT_FOUND",
                                   "message": "Environment not found"}}, 404)

            # 32 random bytes → 43-char URL-safe token. Well within the
            # VARCHAR(64) column; hashed to 64 hex chars for storage.
            raw = secrets.token_urlsafe(32)
            token_hash = _hash_share_token(raw)
            expires_at = datetime.now(timezone.utc) + timedelta(days=days)

            link = SharedDashboardLink(
                tenant_id=request.user["tenant_id"],
                environment_id=env_id,
                token=token_hash,
                created_by=request.user["id"],
                expires_at=expires_at,
            )
            db.add(link); db.commit(); db.refresh(link)

            # Build the public URL. Railway (and most PaaS) terminate
            # TLS at the proxy and forward plain HTTP to gunicorn;
            # request.url_root reflects the internal scheme (http),
            # which produces an ugly URL. Honour X-Forwarded-Proto
            # when present so PMs can paste the link into Slack
            # without a cert warning hop.
            proto = request.headers.get("X-Forwarded-Proto",
                                        request.scheme or "https")
            if proto not in ("http", "https"):
                proto = "https"
            host = request.host
            base = f"{proto}://{host}"
            return ({
                "link_id": link.id,
                "environment_id": env_id,
                "url": f"{base}/shared/{raw}",
                "expires_at": expires_at.isoformat(),
                "expires_days": days,
            }, 201)
        finally:
            db.close()

    return _do()


@views_bp.route("/api/dashboard/share/<int:link_id>/revoke", methods=["POST"])
@require_tier_api(Tier.MEMBER)
@_require_auth_api
def api_dashboard_share_revoke(link_id):
    """Revoke a shared dashboard link so subsequent visits return a
    'link revoked' page. Soft-only — row stays for audit."""
    from datetime import datetime, timezone
    from primeqa.core.permissions import SharedDashboardLink

    def _do():
        db = next(get_db())
        try:
            link = db.query(SharedDashboardLink).filter_by(id=link_id).first()
            if link is None or link.tenant_id != request.user["tenant_id"]:
                return ({"error": {"code": "NOT_FOUND",
                                   "message": "Link not found"}}, 404)
            if link.revoked_at is not None:
                return ({"link_id": link.id, "status": "already_revoked",
                         "revoked_at": link.revoked_at.isoformat()}, 200)
            link.revoked_at = datetime.now(timezone.utc)
            db.commit()
            return ({"link_id": link.id, "status": "revoked",
                     "revoked_at": link.revoked_at.isoformat()}, 200)
        finally:
            db.close()

    return _do()


@views_bp.route("/api/dashboard/share", methods=["GET"])
@require_tier_api(Tier.MEMBER)
@_require_auth_api
def api_dashboard_share_list():
    """List active + recent shared links in the caller's tenant.
    Used by the 'manage share links' UI on the dashboard."""
    from primeqa.core.permissions import SharedDashboardLink

    def _do():
        db = next(get_db())
        try:
            rows = (db.query(SharedDashboardLink)
                    .filter_by(tenant_id=request.user["tenant_id"])
                    .order_by(SharedDashboardLink.created_at.desc())
                    .limit(50).all())
            return ({"links": [{
                "id": l.id, "environment_id": l.environment_id,
                "created_at": l.created_at.isoformat() if l.created_at else None,
                "expires_at": l.expires_at.isoformat() if l.expires_at else None,
                "revoked_at": l.revoked_at.isoformat() if l.revoked_at else None,
                "created_by": l.created_by,
            } for l in rows]}, 200)
        finally:
            db.close()

    return _do()


@views_bp.route("/shared/<string:token>")
def shared_dashboard_public(token):
    """Public read-only dashboard view. No auth required — the token
    itself is the capability. Shows the hero Go/No-Go, ticket grid,
    quality gates, run trend. Action buttons (approve, override,
    share, cancel) are stripped in the read-only template."""
    from datetime import datetime, timezone
    from primeqa.core.permissions import SharedDashboardLink
    # D-219: shared links render the same substrate-sourced data.
    from primeqa.intelligence.substrate_dashboard import (
        get_substrate_dashboard_data as get_dashboard_data,
    )

    db = next(get_db())
    try:
        link = (db.query(SharedDashboardLink)
                .filter_by(token=_hash_share_token(token))
                .first())
        if link is None:
            return render_template("dashboard_shared.html",
                                   state="not_found", data=None,
                                   link=None, now=datetime.now(timezone.utc)), 404
        now = datetime.now(timezone.utc)
        if link.revoked_at is not None:
            return render_template("dashboard_shared.html",
                                   state="revoked", data=None,
                                   link=link, now=now), 410
        # `expires_at` is timezone-aware in Postgres; normalise for
        # safety.
        if link.expires_at and link.expires_at < now:
            return render_template("dashboard_shared.html",
                                   state="expired", data=None,
                                   link=link, now=now), 410
        data = get_dashboard_data(link.environment_id, link.tenant_id, db)
        return render_template("dashboard_shared.html",
                               state="ok", data=data, link=link, now=now)
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
    """Tester-facing Results URL. D-218: results live on the substrate runs
    index now — the v1 /runs list froze when execution moved to
    s4_execution_runs. The v1 archive stays reachable at /runs directly."""
    # Preserve any filter query-string the caller passed.
    qs = request.query_string.decode() if request.query_string else ""
    return redirect("/runs/substrate" + (f"?{qs}" if qs else ""))


@views_bp.route("/run")
@require_tier(Tier.MEMBER)
@login_required
def run_page():
    """Run approved substrate tests in bulk: pick an environment + the
    requirements to cover (or run everything approved). Replaces the v1
    4-mode page (Prompt 16) whose pickers and POST /api/bulk-runs fed the
    retired pipeline engine. Production environments are excluded — the
    D-214 sandbox-only execution posture."""
    from primeqa.core.models import Environment
    from primeqa.intelligence.s4_execution_console import (
        list_runnable_requirements,
    )
    from primeqa.test_management.models import Requirement

    def _render():
        db = next(get_db())
        try:
            tid = request.user["tenant_id"]
            # Phase 3 (D-245): scope the env list to the caller's accessible
            # set (groups), not just the tenant. Non-prod only (D-214).
            envs = [e for e in EnvironmentRepository(db).list_environments(
                        tid, request.user["id"], request.user["role"])
                    if not e.is_production]
            envs.sort(key=lambda e: (e.name or "").lower())
            runnable = list_runnable_requirements(tid)
            keys = [r["key"] for r in runnable["rows"]]
            summaries = {}
            jira_keys = [k for k in keys if not k.startswith("req-")]
            req_ids = [int(k[4:]) for k in keys if k.startswith("req-")
                       and k[4:].isdigit()]
            if jira_keys:
                for r in (db.query(Requirement)
                          .filter(Requirement.tenant_id == tid,
                                  Requirement.jira_key.in_(jira_keys)).all()):
                    summaries[r.jira_key] = r.jira_summary
            if req_ids:
                for r in (db.query(Requirement)
                          .filter(Requirement.tenant_id == tid,
                                  Requirement.id.in_(req_ids)).all()):
                    summaries[f"req-{r.id}"] = r.jira_summary
            rows = [{**r, "summary": summaries.get(r["key"])}
                    for r in runnable["rows"]]
            return render_template("run/index.html", **ctx(
                active_page="run_tests", environments=[
                    {"id": e.id, "name": e.name} for e in envs],
                requirements=rows, available=runnable["available"],
            ))
        finally:
            db.close()

    return _render()


@views_bp.route("/run", methods=["POST"])
@require_tier(Tier.MEMBER)
@login_required
def run_page_submit():
    """Enqueue one s4 execution job per approved claim of the selected
    requirements (all of them when run_all is set)."""
    from flask import flash
    from primeqa.core.models import Environment

    def _submit():
        tid = request.user["tenant_id"]
        env_id = request.form.get("environment_id", type=int)
        run_all = request.form.get("run_all") == "1"
        keys = request.form.getlist("requirement_keys")
        db = next(get_db())
        try:
            # Phase 3 (D-245): validate the client-supplied env_id is within the
            # caller's accessible set (groups) BEFORE running — not just tenant.
            repo = EnvironmentRepository(db)
            if not (env_id and repo.is_environment_accessible(
                    tid, request.user["id"], request.user["role"], env_id)):
                flash("Pick an environment you have access to.", "error")
                return redirect("/run")
            env = repo.get_environment(env_id, tid)
            if env.is_production:
                flash("Substrate runs are sandbox-only — production "
                      "environments cannot be targeted here.", "error")
                return redirect("/run")
        finally:
            db.close()

        if run_all:
            from primeqa.intelligence.s4_execution_console import (
                enqueue_all_approved_claims,
            )
            result = enqueue_all_approved_claims(
                tid, env_id, created_by=request.user["id"])
            count = len(result["enqueued"])
        else:
            if not keys:
                flash("Select at least one requirement.", "error")
                return redirect("/run")
            from primeqa.execution_engine.intake import (
                enqueue_claims_for_requirements,
            )
            result = enqueue_claims_for_requirements(
                tenant_id=tid, external_keys=keys, environment_id=env_id,
                created_by=request.user["id"])
            count = result["enqueued"]
        if count == 0:
            flash("No approved claims matched the selection.", "error")
            return redirect("/run")
        skipped = result.get("skipped_unexecutable") or 0
        flash(f"{count} substrate run{'s' if count != 1 else ''} queued"
              + (f" — {skipped} claim{'s' if skipped != 1 else ''} skipped "
                 f"(not yet executable)" if skipped else ""),
              "success")
        return redirect("/runs/substrate")

    return _submit()


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

        # D-164 (UI Area 1): Substrate-1 sync status — best-effort, never breaks the page.
        from primeqa.metadata_bridge.s1_sync_console import read_s1_sync_status
        s1_status = read_s1_sync_status(request.user["tenant_id"], env_id)

        return render_template("environments/detail.html", **ctx(
            active_page="settings_environments", settings_page="environments",
            breadcrumb_section="Environments", breadcrumb_item=env.name,
            env=env_data, message=request.args.get("message"),
            sync_statuses=sync_statuses, meta_version_id=meta_version_id,
            s1_status=s1_status,
        ))
    finally:
        db.close()


@views_bp.route("/environments/<int:env_id>/sync-substrate", methods=["POST"])
@role_required("admin", "superadmin")
def environments_sync_substrate(env_id):
    """Provision + enqueue an S1 (substrate) metadata sync for the env (D-164).
    Async via the worker queue; reuses the ``trigger_metadata_sync`` permission."""
    from urllib.parse import quote

    from primeqa.metadata_bridge.s1_sync_console import trigger_s1_sync
    # Gated by @role_required("admin", "superadmin") above (D-245).
    db = next(get_db())
    try:
        env = EnvironmentRepository(db).get_environment(env_id, request.user["tenant_id"])
        if not env:
            return redirect("/environments")
        res = trigger_s1_sync(
            request.user["tenant_id"], env_id, env.sf_instance_url,
            created_by=request.user.get("id"))
    finally:
        db.close()
    msg = ("Substrate (S1) sync queued — it runs in the background; "
           "refresh to watch progress." if res.get("ok")
           else f"Substrate sync error: {res.get('error', 'could not queue')}")
    return redirect(f"/environments/{env_id}?message={quote(msg)}")


@views_bp.route("/environments/<int:env_id>/sync-substrate/status")
@role_required("admin", "superadmin")
def environments_sync_substrate_status(env_id):
    """JSON S1-sync status for the env (D-164, 1b) — polled by the panel while a
    sync runs. Best-effort; always 200 with the status dict."""
    from primeqa.metadata_bridge.s1_sync_console import read_s1_sync_status
    return jsonify(read_s1_sync_status(request.user["tenant_id"], env_id))


@views_bp.route("/environments/<int:env_id>/sync-substrate/requeue-enrichment",
                methods=["POST"])
@role_required("admin", "superadmin")
def environments_requeue_enrichment(env_id):
    """Reset the env's connected-org ``failed_permanent`` enrichment rows to
    ``pending`` (D-180) so the worker re-embeds them under the per-env keys (D-179).
    Async via the worker queue; reuses the ``trigger_metadata_sync`` permission."""
    from urllib.parse import quote

    from primeqa.metadata_bridge.s1_sync_console import requeue_s1_enrichment
    # Gated by @role_required("admin", "superadmin") above (D-245).
    res = requeue_s1_enrichment(request.user["tenant_id"], env_id)
    if res.get("ok"):
        n = res.get("requeued", 0)
        msg = (f"Requeued {n} enrichment row{'s' if n != 1 else ''} — the worker "
               "will re-embed them in the background." if n
               else "No failed enrichment rows to requeue.")
    else:
        msg = f"Requeue error: {res.get('error', 'could not requeue')}"
    return redirect(f"/environments/{env_id}?message={quote(msg)}")


@views_bp.route("/org-model")
@role_required("admin", "superadmin")
def org_model():
    """Read-only browser of the synced S1 org model (D-164, 1c). Tenant-level —
    S1 is one versioned org model per tenant. ``?object=ApiName`` drills in."""
    from primeqa.metadata_bridge.s1_sync_console import read_org_model
    obj = request.args.get("object") or None
    data = read_org_model(request.user["tenant_id"], obj)
    return render_template("org_model.html", **ctx(
        active_page="org_model", model=data, selected_object=obj))


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


# v1 metadata-sync routes RETIRED (D-193): refresh-metadata / sync/<id> progress /
# quick-refresh / sync cancel + retry — the v1 meta_* writer is gone (reads on S1);
# users sync via the Substrate (S1) panel on the env detail page (D-164).


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


# --- User admin UI (role ladder, D-245) -----------------------------------


def _settings_users_payload(db, tenant_id, search: str = ""):
    """Build the list-page payload: users + their role-ladder tier (D-245).

    The DB ``role`` value is kept; ``role_label`` is the user-facing tier name
    (Viewer / Member / Admin / Superadmin) the role maps to."""
    from primeqa.core.models import User
    from primeqa.core.authz import tier_label

    users = (db.query(User)
             .filter_by(tenant_id=tenant_id)
             .order_by(User.is_active.desc(), User.full_name.asc())
             .all())
    if search:
        s = search.lower()
        users = [u for u in users
                 if s in (u.full_name or "").lower() or s in (u.email or "").lower()]

    return [{
        "id": u.id,
        "full_name": u.full_name,
        "email": u.email,
        "is_active": u.is_active,
        "role": u.role,
        "role_label": tier_label(u.role),
    } for u in users]


@views_bp.route("/settings/users")
@require_tier(Tier.ADMIN)
@login_required
def settings_users():
    """List all users in the tenant with their role-ladder tier (D-245)."""

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
@require_tier(Tier.ADMIN)
@login_required
def settings_user_detail(user_id):
    """User detail: info + role-ladder tier + activation control (D-245).

    The permission-set sections (assigned sets / effective-permissions) were
    removed in D-245 — access is now the role ladder. Role is edited via the
    existing ``/users/<id>/edit`` form."""
    from flask import flash
    from primeqa.core.models import User
    from primeqa.core.authz import tier_label

    def _render():
        db = next(get_db())
        try:
            u = db.query(User).filter_by(id=user_id).first()
            if u is None or u.tenant_id != request.user["tenant_id"]:
                flash("User not found.", "error")
                return redirect("/settings/users")

            is_self = (u.id == request.user["id"])

            return render_template("settings/user_detail.html", **ctx(
                active_page="settings_users", settings_page="users",
                breadcrumb_section="Users",
                breadcrumb_section_url="settings/users",
                breadcrumb_item=u.full_name,
                edit_user=u, role_label=tier_label(u.role), is_self=is_self,
            ))
        finally:
            db.close()

    return _render()


@views_bp.route("/api/users/<int:user_id>/deactivate", methods=["POST"])
@require_tier_api(Tier.ADMIN)
@_require_auth_api
def api_deactivate_user(user_id):
    """Deactivate a user. Blocks self-deactivation + last-superadmin lockout."""
    from primeqa.core.models import User

    def _do():
        if user_id == request.user["id"] and request.user.get("role") != "superadmin":
            return ({"error": {"code": "SELF_DEACTIVATE",
                               "message": "Cannot deactivate your own account."}}, 400)
        db = next(get_db())
        try:
            u = db.query(User).filter_by(id=user_id).first()
            if u is None or u.tenant_id != request.user["tenant_id"]:
                return ({"error": {"code": "NOT_FOUND", "message": "User not found"}}, 404)
            # Task 7: last-superadmin guard. Even superadmins (who
            # bypass the SELF_DEACTIVATE check) can't deactivate the
            # last active superadmin in a tenant — that would lock
            # admin-only routes behind a user who can no longer log in.
            if u.role == "superadmin" and u.is_active:
                other_supers = (db.query(User)
                                .filter(User.tenant_id == u.tenant_id,
                                        User.role == "superadmin",
                                        User.is_active == True,
                                        User.id != u.id)
                                .count())
                if other_supers == 0:
                    return ({"error": {
                        "code": "LAST_SUPERADMIN",
                        "message": ("Cannot deactivate the last active "
                                    "superadmin in this tenant."),
                    }}, 400)
            u.is_active = False
            db.commit()
            return ("", 204)
        finally:
            db.close()

    return _do()


@views_bp.route("/api/users/<int:user_id>/activate", methods=["POST"])
@require_tier_api(Tier.ADMIN)
@_require_auth_api
def api_activate_user(user_id):
    """Re-activate a user."""
    from primeqa.core.models import User

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
                # D-179: optional Voyage embedding key for S1 enrichment.
                "voyage_api_key": request.form.get("llm_voyage_api_key", ""),
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
            # D-179: Voyage embedding key — same keep-current idiom (blank = keep).
            if request.form.get("llm_voyage_api_key"):
                new_config["voyage_api_key"] = request.form["llm_voyage_api_key"]
            elif old_config.get("voyage_api_key"):
                new_config["voyage_api_key"] = old_config["voyage_api_key"]
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
@require_tier(Tier.ADMIN)
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
            tids = [row["key"] for row in cost["by_tenant"]]
            name_rows = db.query(Tenant.id, Tenant.name).filter(
                Tenant.id.in_(tids),
            ).all()
            name_by_id = {r[0]: r[1] for r in name_rows}
            tier_rows = db.query(
                TenantAgentSettings.tenant_id,
                TenantAgentSettings.llm_tier,
                TenantAgentSettings.llm_enable_story_enrichment,
                TenantAgentSettings.llm_enable_domain_packs,
            ).filter(TenantAgentSettings.tenant_id.in_(tids)).all()
            tier_by_id = {r[0]: r[1] for r in tier_rows}
            story_by_id = {r[0]: bool(r[2]) for r in tier_rows}
            packs_by_id = {r[0]: bool(r[3]) for r in tier_rows}
            # D-236: repair_auto_apply is NOT ORM-mapped (deploy-safety) — read it
            # best-effort via raw SQL; a missing column (pre-054) → all False.
            repair_by_id = {}
            try:
                rr = db.execute(_sql(
                    "SELECT tenant_id, repair_auto_apply FROM tenant_agent_settings "
                    "WHERE tenant_id = ANY(:t)"), {"t": list(tids)}).all()
                repair_by_id = {r[0]: bool(r[1]) for r in rr}
            except Exception:
                db.rollback()

            # D-238 (drop-readiness): the per-tenant correction rate counted v1
            # ``test_cases`` (AI-generated TCs) — that table retires with migration
            # 053. The denom is already 0, so every tenant reads 0.0; skip the query
            # (the loop below defaults to (0, 0)). Re-sourcing the rate from the
            # substrate is a logged residual.
            rate_by_id = {}

            for row in cost["by_tenant"]:
                row["tenant_name"] = name_by_id.get(row["key"], f"Tenant #{row['key']}")
                row["tier"] = tier_by_id.get(row["key"], _tiers.TIER_STARTER)
                row["llm_enable_story_enrichment"] = story_by_id.get(row["key"], False)
                row["llm_enable_domain_packs"] = packs_by_id.get(row["key"], False)
                row["repair_auto_apply"] = repair_by_id.get(row["key"], False)
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
    """Superadmin-only: change a tenant's LLM tier and feature flags.

    Accepts form fields:
      - ``llm_tier`` ∈ {starter, pro, enterprise, custom}
      - ``llm_enable_story_enrichment`` (checkbox; absent = false)
      - ``llm_enable_domain_packs`` (checkbox; absent = false)

    Logs each change to activity_log so the audit trail distinguishes
    tier changes from feature-flag toggles. Redirects back to
    /settings/llm-usage.
    """
    from flask import flash
    from primeqa.intelligence.llm import tiers
    from primeqa.core.models import TenantAgentSettings, ActivityLog

    new_tier = (request.form.get("llm_tier") or "").strip().lower()
    if new_tier not in tiers.ALL_TIERS:
        flash(f"Unknown tier: {new_tier!r}", "error")
        return redirect("/settings/llm-usage")

    # Checkbox semantics: HTML only submits the field when checked.
    new_story_flag = bool(request.form.get("llm_enable_story_enrichment"))
    new_packs_flag = bool(request.form.get("llm_enable_domain_packs"))
    new_repair_flag = bool(request.form.get("repair_auto_apply"))  # D-236

    db = next(get_db())
    try:
        row = db.query(TenantAgentSettings).filter(
            TenantAgentSettings.tenant_id == tenant_id,
        ).first()
        if not row:
            row = TenantAgentSettings(
                tenant_id=tenant_id,
                llm_tier=new_tier,
                llm_enable_story_enrichment=new_story_flag,
                llm_enable_domain_packs=new_packs_flag,
            )
            db.add(row)
            db.flush()
            db.add(ActivityLog(
                tenant_id=tenant_id,
                user_id=request.user["id"],
                action="create",
                entity_type="tenant_agent_settings",
                entity_id=tenant_id,
                details={
                    "llm_tier": new_tier,
                    "llm_enable_story_enrichment": new_story_flag,
                    "llm_enable_domain_packs": new_packs_flag,
                },
            ))
        else:
            old_tier = row.llm_tier
            old_story = bool(row.llm_enable_story_enrichment)
            old_packs = bool(row.llm_enable_domain_packs)
            row.llm_tier = new_tier
            row.llm_enable_story_enrichment = new_story_flag
            row.llm_enable_domain_packs = new_packs_flag
            db.flush()
            if old_tier != new_tier:
                db.add(ActivityLog(
                    tenant_id=tenant_id,
                    user_id=request.user["id"],
                    action="update",
                    entity_type="tenant_llm_tier",
                    entity_id=tenant_id,
                    details={"old": old_tier, "new": new_tier},
                ))
            if old_story != new_story_flag:
                db.add(ActivityLog(
                    tenant_id=tenant_id,
                    user_id=request.user["id"],
                    action="update",
                    entity_type="tenant_story_enrichment",
                    entity_id=tenant_id,
                    details={"old": old_story, "new": new_story_flag},
                ))
            if old_packs != new_packs_flag:
                db.add(ActivityLog(
                    tenant_id=tenant_id,
                    user_id=request.user["id"],
                    action="update",
                    entity_type="tenant_domain_packs",
                    entity_id=tenant_id,
                    details={"old": old_packs, "new": new_packs_flag},
                ))
        db.commit()
        # D-236: persist repair_auto_apply SEPARATELY + best-effort — the column
        # (migration 054) is not ORM-mapped and may not be applied yet, so a
        # missing column must never break the tier/story/packs save (D-230
        # ordering). Own try/except + rollback so it can't poison the request.
        try:
            from sqlalchemy import text as _t
            cur = db.execute(_t(
                "SELECT repair_auto_apply FROM tenant_agent_settings "
                "WHERE tenant_id = :t"), {"t": tenant_id}).scalar()
            if bool(cur) != new_repair_flag:
                db.execute(_t(
                    "UPDATE tenant_agent_settings SET repair_auto_apply = :v "
                    "WHERE tenant_id = :t"), {"v": new_repair_flag, "t": tenant_id})
                db.add(ActivityLog(
                    tenant_id=tenant_id, user_id=request.user["id"],
                    action="update", entity_type="tenant_repair_auto_apply",
                    entity_id=tenant_id,
                    details={"old": bool(cur), "new": new_repair_flag}))
                db.commit()
        except Exception:
            db.rollback()
        flash(
            f"Tenant #{tenant_id}: tier={new_tier}, "
            f"story={'on' if new_story_flag else 'off'}, "
            f"packs={'on' if new_packs_flag else 'off'}",
            "success",
        )
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

        # D-165 (UI Area 2): the per-requirement count is now S2 claims (generated
        # by S3), not v1 test_cases. One bulk read keyed by requirement external_key
        # (_requirement_to_ref — the same key generation persisted). Generation +
        # execution now happen on the detail page, so the list is browse + counts.
        from primeqa.intelligence.s3_enqueue import _requirement_to_ref
        from primeqa.intelligence.s3_generation_console import count_claims_by_requirement
        req_keys = {r.id: _requirement_to_ref(r)["key"] for r in reqs}
        claim_counts = count_claims_by_requirement(
            tid, set(req_keys.values())).get("counts", {})

        reqs_data = [{
            "id": r.id, "jira_key": r.jira_key, "jira_summary": r.jira_summary,
            "acceptance_criteria": r.acceptance_criteria, "is_stale": r.is_stale,
            "source": r.source,
            "updated_at": r.updated_at.isoformat() if r.updated_at else "",
            "claim_count": claim_counts.get(req_keys[r.id], 0),
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
            RequirementRepository, SectionRepository,
        )
        tid = request.user["tenant_id"]
        req_repo = RequirementRepository(db)
        req = req_repo.get_requirement(req_id, tid, include_deleted=True)
        if not req:
            return redirect("/requirements")

        # Prompt 16: track this view for the /run Tickets picker's
        # "Recent tickets" list. Best-effort — failures never break
        # the requirement detail page. Scoped to the user's active
        # env so switching envs gives a clean slate.
        try:
            from primeqa.core.models import User
            from primeqa.runs.my_tickets import resolve_active_environment
            from primeqa.runs.recent_tickets import record_view
            if req.jira_key:
                active_user = db.query(User).filter_by(
                    id=request.user["id"]).first()
                active_env = resolve_active_environment(active_user, db) if active_user else None
                if active_env is not None:
                    record_view(db, request.user["id"], active_env.id,
                                req.jira_key, req.jira_summary)
        except Exception:
            pass  # tracking is best-effort

        section = None
        if req.section_id:
            section = SectionRepository(db).get_section(req.section_id, tid)

        # D-165 (UI Area 2): the requirement's test plan is now S2 claims/recipes
        # generated by S3 — not v1 test_cases. req_key is the substrate external_key
        # (s3_enqueue._requirement_to_ref is the single source of truth, so the read
        # never drifts from what generation wrote per D-166).
        from primeqa.intelligence.s3_enqueue import _requirement_to_ref
        from primeqa.intelligence.s3_generation_console import (
            read_requirement_claims, read_latest_s3_job,
            read_latest_generation_note)
        req_key = _requirement_to_ref(req)["key"]
        s2 = read_requirement_claims(tid, req_key)
        s3_job = read_latest_s3_job(tid, req_key)
        # D-206: surface a dedup honestly — "Generate matched an existing test"
        # instead of looking like it silently did nothing.
        gen_note = read_latest_generation_note(tid, req_key)

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
            environments=envs_data, req_key=req_key,
            s2_claims=s2, s3_job=s3_job, gen_note=gen_note,
        ))
    finally:
        db.close()


@views_bp.route("/requirements/<int:req_id>/generate-substrate", methods=["POST"])
@role_required("admin", "tester", "superadmin")
def requirements_generate_substrate(req_id):
    """D-165 (UI Area 2): enqueue an S3 generation job for this requirement — the
    substrate replacement for the v1 Generate path. Resolves the requirement +
    validates the env via the s3_generation_console bridge (best-effort), then
    redirects back; the worker's s3_generation_tick runs it async and the detail
    page polls the job. Same role gate as the v1 generate + the S3 API enqueue."""
    from flask import flash
    environment_id = request.form.get("environment_id", type=int)
    if not environment_id:
        flash("Pick an environment to generate against.", "error")
        return redirect(f"/requirements/{req_id}")
    db = next(get_db())
    try:
        # Phase 3 (D-245): validate the client-supplied env against the caller's
        # accessible set (groups) before generating.
        if not EnvironmentRepository(db).is_environment_accessible(
                request.user["tenant_id"], request.user["id"],
                request.user["role"], environment_id):
            flash("Pick an environment you have access to.", "error")
            return redirect(f"/requirements/{req_id}")
        from primeqa.intelligence.s3_generation_console import trigger_s3_generation
        res = trigger_s3_generation(
            db, tenant_id=request.user["tenant_id"], requirement_id=req_id,
            environment_id=environment_id, created_by=request.user["id"])
    finally:
        db.close()
    if res.get("ok"):
        flash("Generating the test plan — this runs in the background.", "success")
    else:
        flash(f"Could not start generation: {res.get('error', 'unknown error')}", "error")
    return redirect(f"/requirements/{req_id}")


@views_bp.route("/claims")
@login_required
def claims_list():
    """D-165 (UI Area 2 slice 2c): the claims library — paginated + searchable
    list of the tenant's current S2 claims (the substrate replacement for the v1
    Test Library at /test-cases). Best-effort read via the bridge."""
    from primeqa.intelligence.s3_generation_console import list_claims
    page = request.args.get("page", 1, type=int) or 1
    per_page = request.args.get("per_page", 20, type=int) or 20
    q = (request.args.get("q") or "").strip() or None
    data = list_claims(request.user["tenant_id"], page=page, per_page=per_page, q=q)
    # D-232: mark quarantined rows for the list badge — one best-effort batch read
    # over the active ledger rows (a missing table degrades to no badges).
    from primeqa.intelligence import quarantine as _quar
    _q_ids = {r["test_id"] for r in _quar.list_quarantined(request.user["tenant_id"])}
    for _c in data.get("claims") or []:
        _c["quarantined"] = str(_c.get("test_id")) in _q_ids
    # The inbox chip: how many drafts are waiting for approval (D-206).
    pending = list_claims(request.user["tenant_id"], page=1, per_page=1, status="draft")
    return render_template("claims/list.html", **ctx(
        active_page="test_library", data=data, q=q or "",
        pending_total=pending.get("total", 0)))


@views_bp.route("/claims/inbox")
@role_required("admin", "ba", "tester", "superadmin")
def claims_inbox():
    """D-206: the approval inbox — every draft claim awaiting a human decision,
    with the plain-English title + the behavioral/configuration-check depth
    badge + the source requirement, and a per-row Approve. Approval is the
    human gate that makes a claim runnable (and auto-queues its first runs),
    so this page is the manual-approval workflow's home."""
    from primeqa.intelligence.s3_generation_console import list_claims
    page = request.args.get("page", 1, type=int) or 1
    data = list_claims(request.user["tenant_id"], page=page, per_page=50,
                       status="draft")
    return render_template("claims/inbox.html", **ctx(
        active_page="test_library", data=data))


@views_bp.route("/claims/<uuid:test_id>")
@login_required
def claims_detail(test_id):
    """D-165 (UI Area 2 slice 2b): the semantic detail of a single S2 claim —
    archetype / claim_kind / asserted_truth / semantic_conditions + its recipes.
    The substrate replacement for the v1 test-case detail page. Best-effort read
    via the s3_generation_console bridge; renders an empty state when the claim is
    gone or the substrate is unavailable."""
    from primeqa.intelligence.claim_presentation import verdict_plain
    from primeqa.intelligence.s3_generation_console import (
        read_claim_detail, read_claim_requirement, read_claim_siblings)
    from primeqa.intelligence.s4_execution_console import read_claim_runs
    tid = request.user["tenant_id"]
    detail = read_claim_detail(tid, test_id)
    siblings = read_claim_siblings(tid, test_id)      # D-228 (F3): supersession context
    runs = read_claim_runs(tid, test_id)              # D-168 (3a): recent runs (S6)
    for r in runs.get("runs") or []:                  # D-206: plain-words line
        r["plain"] = verdict_plain(r.get("verdict"), r.get("outcome"))
    # D-233: the source requirement (the back-link). The substrate stores only the
    # external_key; the v1 db resolves it to a viewable requirement id.
    req_key = read_claim_requirement(tid, test_id).get("requirement_key")
    # Environments for the Run picker (tester+; gated in the template). is_production
    # drives the dynamic prod-confirm gate; has_connection flags runnable envs.
    requirement = None
    db = next(get_db())
    try:
        envs = EnvironmentRepository(db).list_environments(
            tid, request.user["id"], request.user["role"])
        envs_data = [{"id": e.id, "name": e.name,
                      "is_production": bool(getattr(e, "is_production", False)),
                      "has_connection": bool(getattr(e, "connection_id", None))}
                     for e in envs]
        if req_key:
            from primeqa.intelligence.substrate_dashboard import _requirement_rows
            req_id = _requirement_rows(db, tid, [req_key]).get(req_key)
            requirement = {"key": req_key, "id": req_id,
                           "url": f"/requirements/{req_id}" if req_id else None}
    finally:
        db.close()
    # D-232: the persisted quarantine state for the badge + pin/lift control.
    # `active` (any source) drives the badge + toggle; `manual` exposes a durable
    # lift override so the page can say "auto-quarantine suppressed". Source is
    # derived: an active row that is not a manual pin must be auto (a manual lift
    # is inactive, so it can't be active). Best-effort — empties on any read error.
    from primeqa.intelligence import quarantine as _quar
    _q_manual = _quar.manual_states(tid).get(str(test_id))   # 'pinned'|'lifted'|None
    _q_active = _quar.is_quarantined(tid, test_id)
    quarantine_state = {
        "active": _q_active,
        "manual": _q_manual,
        "source": ("manual" if _q_manual == "pinned" else "auto") if _q_active
        else None,
    }
    return render_template("claims/detail.html", **ctx(
        active_page="test_library", detail=detail, siblings=siblings,
        runs=runs, environments=envs_data, quarantine=quarantine_state,
        requirement=requirement))


_MAX_FIELD_OVERRIDES = 50


def _parse_field_overrides(raw):
    """D-235: parse the optional run-time test-data injection textarea — one
    ``Field=Value`` per line — into a ``{bare_field_name: value}`` dict. Tolerant:
    blank lines + lines without '=' are skipped; whitespace trimmed; capped at
    ``_MAX_FIELD_OVERRIDES`` entries; empty input → {}. A bad entry is ignored,
    never an error (a misspelled field surfaces later as the org's create
    rejection — D-235 defers S1 pre-flight validation)."""
    out = {}
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()[:100]
        if key:
            out[key] = value.strip()[:255]
        if len(out) >= _MAX_FIELD_OVERRIDES:
            break
    return out


@views_bp.route("/claims/<uuid:test_id>/run", methods=["POST"])
@role_required("admin", "tester", "superadmin")
def claims_run(test_id):
    """D-168 (UI Area 3 slice 3a): run the eligible recipe for this claim on the
    chosen environment (synchronous — blocks for the live Salesforce I/O, returns
    the outcome + verdict). The production-confirm gate lives HERE (the substrate
    has none and a data-recipe run mutates the org) — reuses v1's
    environment_can_bulk_run. Best-effort run via the s4_execution_console bridge.
    D-235: an optional ``field_overrides`` textarea injects per-run test data into
    the positive vertical's subject create."""
    from flask import flash
    environment_id = request.form.get("environment_id", type=int)
    confirm_production = request.form.get("confirm_production") in ("on", "1", "true")
    field_overrides = _parse_field_overrides(request.form.get("field_overrides"))
    if not environment_id:
        flash("Pick an environment to run against.", "error")
        return redirect(f"/claims/{test_id}")
    tid = request.user["tenant_id"]
    db = next(get_db())
    try:
        from primeqa.core.repository import EnvironmentRepository
        from primeqa.runs.bulk import environment_can_bulk_run
        repo = EnvironmentRepository(db)
        # Phase 3 (D-245): the env must be within the caller's accessible set
        # (groups) before a live run targets it.
        if not repo.is_environment_accessible(
                tid, request.user["id"], request.user["role"], environment_id):
            flash("Pick an environment you have access to.", "error")
            return redirect(f"/claims/{test_id}")
        env = repo.get_environment(environment_id, tid)
        if env is None:
            flash("Environment not found.", "error")
            return redirect(f"/claims/{test_id}")
        ok, msg = environment_can_bulk_run(env, confirm_production)
    finally:
        db.close()
    if not ok:
        flash(msg, "error")
        return redirect(f"/claims/{test_id}")

    from primeqa.intelligence.s4_execution_console import trigger_claim_run
    # Phase 4 (D-245): pass the caller's role tier so the dispatch gate can apply
    # the production role rule (a non-Admin may run only the read-only inspection
    # vertical against a production env).
    res = trigger_claim_run(tid, str(test_id), environment_id,
                            field_overrides=field_overrides,
                            caller_tier=rank(request.user["role"]))
    if not res.get("ok"):
        flash(f"Run failed: {res.get('error', 'unknown error')}", "error")
    elif not res.get("ran"):
        flash("Nothing ran — the claim needs an approved recipe that matches the "
              f"environment ({res.get('reason', 'no_eligible_recipe')}).", "error")
    else:
        v = res.get("verdict")
        # D-235: name the overrides so the run is reproducible from the flash.
        # "requested" not "applied": the executor consumes them on the POSITIVE
        # vertical only (a behavioral-negative recipe correctly drops them), so the
        # flash must not assert they took effect.
        n = len(field_overrides)
        ov = f" · {n} data override{'' if n == 1 else 's'} requested" if n else ""
        flash(f"Run complete — outcome: {res.get('outcome')}"
              + (f" · verdict: {v}" if v else "") + ov, "success")
    return redirect(f"/claims/{test_id}")


@views_bp.route("/claims/<uuid:test_id>/approve", methods=["POST"])
@role_required("admin", "ba", "tester", "superadmin")
def claims_approve(test_id):
    """D-168 (UI Area 3 slice 3a): approve a draft claim + its recipes so it
    becomes runnable (the generate→approve→run loop). Approval is humans-only at
    the substrate (D-ε-1); best-effort via the s4_execution_console bridge."""
    from flask import flash
    from primeqa.intelligence.s4_execution_console import approve_claim
    res = approve_claim(request.user["tenant_id"], str(test_id))
    if res.get("ok"):
        # D-226 (the 0.4-audit F2 fix): say what actually got queued — the old
        # flash claimed runnability even when zero runs were enqueued.
        queued = res.get("auto_enqueued") or 0
        if res.get("unexecutable"):
            flash(f"Claim approved, but no run was queued: {res['unexecutable']}",
                  "warning")
        elif queued:
            flash(f"Claim approved — {queued} verification run"
                  f"{'s' if queued != 1 else ''} queued.", "success")
        else:
            flash("Claim approved. No run was queued (no auto-verify sandbox "
                  "environment is connected).", "warning")
    else:
        flash(f"Could not approve: {res.get('error', 'unknown error')}", "error")
    # D-206: the inbox approves in place — return there when asked. Same-page
    # paths only (no open redirect).
    nxt = request.form.get("next") or ""
    if nxt.startswith("/claims"):
        return redirect(nxt)
    return redirect(f"/claims/{test_id}")


@views_bp.route("/claims/<uuid:test_id>/deprecate", methods=["POST"])
@role_required("admin", "tester", "superadmin")
def claims_deprecate(test_id):
    """D-228 (F3): deprecate a claim with a REQUIRED reason — the supersession
    affordance. Deprecation is a human judgment (the D-226 fork closed against
    auto-deprecate); the reason lands in provenance (D-ε-5). A deprecated claim
    stops grading releases (D-219) and stops being selectable for runs."""
    from flask import flash
    from primeqa.intelligence.s4_execution_console import deprecate_claim
    reason = (request.form.get("reason") or "").strip()
    if not reason:
        flash("A reason is required to deprecate a claim.", "error")
        return redirect(f"/claims/{test_id}")
    res = deprecate_claim(request.user["tenant_id"], str(test_id), reason)
    if res.get("ok"):
        if res.get("already"):
            flash("Claim was already deprecated.", "warning")
        else:
            flash("Claim deprecated — it no longer grades releases or runs. "
                  "The reason is recorded in its provenance.", "success")
    else:
        flash(f"Could not deprecate: {res.get('error', 'unknown error')}", "error")
    return redirect(f"/claims/{test_id}")


@views_bp.route("/claims/<uuid:test_id>/quarantine", methods=["POST"])
@role_required("admin", "tester", "superadmin")
def claims_quarantine(test_id):
    """D-232: the operator's manual flaky-test control — pin (quarantine) or lift a
    claim. A MANUAL pin wins over the live flake signal (the claim is excluded from
    the release pass-rate); a lift is a durable override that suppresses the live
    auto-quarantine so the claim counts again (harmonization lives in the release
    decision, D-232.2). Each action writes activity_log. Best-effort via the
    quarantine ledger — a missing table (pre-migration) degrades to a no-op."""
    from flask import flash

    from primeqa.intelligence import quarantine
    tid = request.user["tenant_id"]
    action = (request.form.get("action") or "").strip()
    if action == "pin":
        reason = (request.form.get("reason") or "").strip() or None
        ok = quarantine.pin(tid, str(test_id), reason=reason,
                            actor=request.user["id"], source="manual")
        log_action = "quarantine_pin"
        ok_msg = ("Claim quarantined — it no longer counts toward the release "
                  "pass-rate.")
    elif action == "unpin":
        reason = None
        ok = quarantine.unpin(tid, str(test_id), actor=request.user["id"])
        log_action = "quarantine_lift"
        ok_msg = ("Quarantine lifted — the claim counts toward the release "
                  "pass-rate again.")
    else:
        flash("Unknown quarantine action.", "error")
        return redirect(f"/claims/{test_id}")

    if not ok:
        flash("Could not update the quarantine — the substrate is unavailable.",
              "error")
        return redirect(f"/claims/{test_id}")

    # Audit: activity_log.entity_id is an int column (can't hold a UUID), so the
    # test_id rides the details JSON; entity_type names the ledger.
    db = next(get_db())
    try:
        from primeqa.core.models import ActivityLog
        details = {"test_id": str(test_id)}
        if reason:
            details["reason"] = reason
        db.add(ActivityLog(
            tenant_id=tid, user_id=request.user["id"], action=log_action,
            entity_type="claim_quarantine", entity_id=None, details=details))
        db.commit()
    finally:
        db.close()
    flash(ok_msg, "success")
    return redirect(f"/claims/{test_id}")


def _parse_runs_since(raw):
    """D-231: parse the Results `since` filter — 'today' → start of today (UTC), or
    an ISO date (YYYY-MM-DD) → that day's start. Returns a tz-aware datetime, or
    None for empty/unrecognized input (a bad filter is ignored, never an error)."""
    if not raw:
        return None
    from datetime import date, datetime, timezone
    if raw == "today":
        return datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0)
    try:
        d = date.fromisoformat(raw)
    except ValueError:
        return None
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


@views_bp.route("/runs/substrate")
@login_required
def s4_runs_list():
    """D-168 (UI Area 3 slice 3b): the global S4 runs index — all execution runs,
    newest-first. A focused new surface (the dense v1 /runs page is re-pointed at
    cutover Step 5). Best-effort read via the s4_execution_console bridge.
    D-214 adds the schedules panel (admin+): one cadence per env, sandbox-only."""
    from primeqa.intelligence.s4_execution_console import _RUN_OUTCOMES, list_runs
    page = request.args.get("page", 1, type=int) or 1
    per_page = request.args.get("per_page", 20, type=int) or 20
    # D-231 triage filters (the failures front door). `status` is the v1-alias arg
    # the /results redirect forwards (e.g. ?status=failed); `outcome` is the
    # canonical name — accept either, validated against the run_outcome enum.
    raw_outcome = (request.args.get("outcome")
                   or request.args.get("status") or "").strip().lower()
    outcome = raw_outcome if raw_outcome in _RUN_OUTCOMES else None
    verdict = (request.args.get("verdict") or "").strip() or None
    env = request.args.get("env", type=int)
    since_raw = (request.args.get("since") or "").strip().lower() or None
    since = _parse_runs_since(since_raw)
    tid = request.user["tenant_id"]
    data = list_runs(tid, page=page, per_page=per_page, outcome=outcome,
                     verdict=verdict, environment_id=env, since=since)
    # Echo the active facets (raw arg forms) so the template renders active chips
    # + carries the filter across pagination/chip links.
    active_filters = {"outcome": outcome, "verdict": verdict, "env": env,
                      "since": since_raw}

    schedules, sched_envs = None, []
    if request.user["role"] in ("admin", "superadmin"):
        try:
            from primeqa.execution_engine.schedules import RunScheduleStore
            db = next(get_db())
            try:
                envs = EnvironmentRepository(db).list_environments(
                    tid, request.user["id"], request.user["role"])
                env_names = {e.id: e.name for e in envs}
                sched_envs = [{"id": e.id, "name": e.name}
                              for e in envs if not e.is_production]
            finally:
                db.close()
            schedules = [{
                "id": s.id, "environment_id": s.environment_id,
                "environment": env_names.get(s.environment_id,
                                             f"env {s.environment_id}"),
                "cron_expr": s.cron_expr, "enabled": s.enabled,
                "last_fired_at": (s.last_fired_at.isoformat()
                                  if s.last_fired_at else None),
            } for s in RunScheduleStore(tid).list()]
        except Exception:
            schedules = None                    # panel degrades, page renders

    # D-215.1: open repair proposals (admin+) — proposal-only spine; the
    # decision (approve = apply immediately / reject) happens here.
    repairs = None
    if request.user["role"] in ("admin", "superadmin"):
        from primeqa.intelligence.repair_agent import list_proposals
        repairs = list_proposals(tid)
    return render_template("runs/s4_list.html", **ctx(
        active_page="test_library", data=data, active_filters=active_filters,
        schedules=schedules, sched_envs=sched_envs, repairs=repairs))


@views_bp.route("/runs/substrate/repairs/<int:proposal_id>", methods=["POST"])
@role_required("admin", "superadmin")
def s4_repair_decide(proposal_id):
    """D-215.1: approve (apply immediately) or reject one repair proposal.
    D-236: surface the outcome — a recipe_edit apply can genuinely fail (the
    proposal stays open), so the human must see success vs failure, not a silent
    redirect."""
    from flask import flash

    from primeqa.intelligence.repair_agent import decide_proposal
    approve = request.form.get("action") == "approve"
    res = decide_proposal(
        request.user["tenant_id"], proposal_id, approve=approve,
        decided_by=request.user["id"])
    if not approve:
        flash("Proposal rejected." if res.get("ok") else
              f"Could not reject: {res.get('error', 'unknown error')}",
              "success" if res.get("ok") else "error")
    elif res.get("ok"):
        flash("Fix applied — a re-verify run was queued.", "success")
    else:
        # the apply failed; the proposal stays open for retry.
        flash(f"Could not apply the fix: {res.get('error', 'unknown error')}. "
              "The proposal is still open.", "error")
    return redirect("/runs/substrate")


@views_bp.route("/runs/substrate/schedules", methods=["POST"])
@role_required("admin", "superadmin")
def s4_schedule_create():
    """D-214: create (or re-enable) one scheduled substrate regression. The
    cron comes from a preset <select> — no free-form cron in v1."""
    from primeqa.execution_engine.schedules import RunScheduleStore
    presets = {"hourly": "0 * * * *", "daily": "0 6 * * *",
               "weekly": "0 6 * * 1"}
    cron = presets.get(request.form.get("cadence") or "")
    env_id = request.form.get("environment_id", type=int)
    if cron and env_id:
        # sandbox-only (D-214 §4): reject production envs server-side too
        db = next(get_db())
        try:
            envs = EnvironmentRepository(db).list_environments(
                request.user["tenant_id"], request.user["id"],
                request.user["role"])
            ok = any(e.id == env_id and not e.is_production for e in envs)
        finally:
            db.close()
        if ok:
            RunScheduleStore(request.user["tenant_id"]).create(
                environment_id=env_id, cron_expr=cron,
                created_by=request.user["id"])
    return redirect("/runs/substrate")


@views_bp.route("/runs/substrate/schedules/<int:schedule_id>", methods=["POST"])
@role_required("admin", "superadmin")
def s4_schedule_update(schedule_id):
    """D-214: toggle or delete one schedule (form `action` field)."""
    from primeqa.execution_engine.schedules import RunScheduleStore
    store = RunScheduleStore(request.user["tenant_id"])
    action = request.form.get("action")
    if action == "delete":
        store.delete(schedule_id)
    elif action in ("enable", "disable"):
        store.set_enabled(schedule_id, action == "enable")
    return redirect("/runs/substrate")


@views_bp.route("/runs/<uuid:run_id>")
@login_required
def s4_run_detail(run_id):
    """D-168 (UI Area 3 slice 3c): the detail of one S4 execution run — the
    evidence trace (per-step) + the S6 verdict/cause. Reached from the claim
    detail's Recent-runs panel. Keyed on the run's UUID, so it coexists with the
    v1 /runs/<int:id> detail. Best-effort read via the s4_execution_console bridge."""
    from primeqa.intelligence.s4_execution_console import read_run_detail
    detail = read_run_detail(request.user["tenant_id"], run_id)
    # D-233: a plain-English headline per evidence step (the raw step tree stays
    # collapsed beneath it in the template). Attached here, mirroring how the
    # claim-detail view attaches verdict_plain onto each run row.
    from primeqa.intelligence.claim_presentation import step_plain
    for _step in ((detail or {}).get("run") or {}).get("steps") or []:
        if isinstance(_step, dict):
            _step["plain"] = step_plain(_step)
    # D-231: close the failure→repair drill — surface the actionable repair
    # proposal for THIS run inline (admin+), reusing the queue's decide POST, so the
    # drill ends at an action instead of read-only suggestion text.
    repair_proposal = None
    if request.user["role"] in ("admin", "superadmin"):
        from primeqa.intelligence.repair_agent import open_proposal_for_run
        repair_proposal = open_proposal_for_run(request.user["tenant_id"], run_id)
    return render_template("runs/s4_detail.html", **ctx(
        active_page="test_library", detail=detail, repair_proposal=repair_proposal))


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
            SectionRepository, RequirementRepository,
        )
        from primeqa.test_management.service import TestManagementService
        svc = TestManagementService(
            SectionRepository(db), RequirementRepository(db),
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
            SectionRepository, RequirementRepository,
        )
        from primeqa.test_management.service import TestManagementService
        svc = TestManagementService(
            SectionRepository(db), RequirementRepository(db),
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


@views_bp.route("/api/s3-generation-jobs", methods=["POST"])
@require_tier_api(Tier.MEMBER)
def api_s3_generation_enqueue():
    """Enqueue an S3 generation job. Gated at the Member tier (D-245 Phase 6 —
    the floor of the old inline ``("admin","tester","superadmin")`` list, so
    ``ba`` is admitted too; superadmin passes via the ladder top). Resolves the
    requirement (v1-side) + validates the environment, then pins a queued job and
    returns immediately; the worker's s3_generation_tick runs it."""
    body = request.get_json(silent=True) or {}
    try:
        requirement_id = int(body["requirement_id"])
        environment_id = int(body["environment_id"])
    except (KeyError, TypeError, ValueError):
        return ({"error": {"code": "BAD_REQUEST",
                           "message": "requirement_id and environment_id (ints) are required."}}, 400)

    tenant_id = request.user["tenant_id"]
    db = next(get_db())
    try:
        from primeqa.core.repository import EnvironmentRepository
        from primeqa.intelligence.s3_enqueue import resolve_requirement
        ref = resolve_requirement(db, requirement_id, tenant_id)
        if ref is None:
            return ({"error": {"code": "NOT_FOUND", "message": "Requirement not found."}}, 404)
        if EnvironmentRepository(db).get_environment(environment_id, tenant_id) is None:
            return ({"error": {"code": "NOT_FOUND", "message": "Environment not found."}}, 404)
    finally:
        db.close()

    from primeqa.generation.intake import enqueue_s3_generation
    try:
        job = enqueue_s3_generation(
            tenant_id=tenant_id, requirement_ref=ref,
            environment_id=environment_id, created_by=request.user["id"])
    except Exception as e:
        # e.g. no S1 version pinned yet (VersionNotFoundError) — nothing to
        # generate against. Fail-loud, not a 500.
        return ({"error": {"code": "NO_S1_VERSION",
                           "message": f"Cannot enqueue: {e}"}}, 409)
    return ({"job_id": job.id, "status": job.status}, 202)


@views_bp.route("/api/s3-generation-jobs/<int:job_id>", methods=["GET"])
@_require_auth_api
def api_s3_generation_status(job_id):
    """Poll an S3 generation job (per-tenant; schema-isolated by tenant)."""
    from primeqa.generation.jobs import GenerationJobStore
    job = GenerationJobStore(request.user["tenant_id"]).get_job(job_id)
    if job is None:
        return ({"error": {"code": "NOT_FOUND", "message": "Job not found"}}, 404)
    body = {
        "job_id": job.id, "status": job.status,
        "progress_pct": job.progress_pct or 0, "progress_msg": job.progress_msg,
        "requirement_key": job.requirement_key, "environment_id": job.environment_id,
        "s1_version_seq": job.s1_version_seq, "attempt_count": job.attempt_count,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }
    if job.status in ("failed", "cancelled"):
        body["error_code"] = job.error_code
        body["error_message"] = job.error_message
    return (body, 200)


@views_bp.route("/api/s4-execution-jobs", methods=["POST"])
@require_tier_api(Tier.MEMBER)
def api_s4_execution_enqueue():
    """Enqueue an S4 recipe-execution job (async; the worker's s4_execution_tick
    runs it). Authed; gated on the run role (admin / tester, superadmin bypass —
    mirrors /claims/<id>/run). Validates the environment + applies the
    production-confirm gate at **enqueue** time (the run is deferred to the worker,
    so the human confirm must happen here — not at run time as the sync button
    does), then pins a queued job and returns 202; poll GET /api/s4-execution-jobs/<id>.

    D-245 Phase 6: gated at the Member tier (the floor of the old inline
    ``("admin","tester","superadmin")`` list — ``ba`` admitted too, superadmin
    via the ladder top)."""
    from uuid import UUID

    body = request.get_json(silent=True) or {}
    try:
        test_id = str(UUID(str(body["test_id"])))
        environment_id = int(body["environment_id"])
    except (KeyError, TypeError, ValueError):
        return ({"error": {"code": "BAD_REQUEST",
                           "message": "test_id (uuid) and environment_id (int) are required."}}, 400)
    confirm_production = bool(body.get("confirm_production"))

    tenant_id = request.user["tenant_id"]
    db = next(get_db())
    try:
        from primeqa.core.repository import EnvironmentRepository
        from primeqa.runs.bulk import environment_can_bulk_run
        env = EnvironmentRepository(db).get_environment(environment_id, tenant_id)
        if env is None:
            return ({"error": {"code": "NOT_FOUND", "message": "Environment not found."}}, 404)
        ok, msg = environment_can_bulk_run(env, confirm_production)
        if not ok:
            return ({"error": {"code": "PRODUCTION_CONFIRM_REQUIRED", "message": msg}}, 400)
    finally:
        db.close()

    from primeqa.execution_engine.intake import enqueue_s4_execution
    try:
        job = enqueue_s4_execution(
            tenant_id=tenant_id, test_id=test_id,
            environment_id=environment_id, created_by=request.user["id"])
    except Exception as e:
        return ({"error": {"code": "ENQUEUE_FAILED",
                           "message": f"Cannot enqueue: {e}"}}, 409)
    return ({"job_id": job.id, "status": job.status}, 202)


@views_bp.route("/api/s4-execution-jobs/<int:job_id>", methods=["GET"])
@_require_auth_api
def api_s4_execution_status(job_id):
    """Poll an S4 execution job (per-tenant; schema-isolated by tenant)."""
    from primeqa.execution_engine.jobs import ExecutionJobStore
    job = ExecutionJobStore(request.user["tenant_id"]).get_job(job_id)
    if job is None:
        return ({"error": {"code": "NOT_FOUND", "message": "Job not found"}}, 404)
    out = {
        "job_id": job.id, "status": job.status,
        "test_id": job.test_id, "environment_id": job.environment_id,
        "attempt_count": job.attempt_count,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }
    if job.status in ("failed", "cancelled"):
        out["error_code"] = job.error_code
        out["error_message"] = job.error_message
    return (out, 200)


@views_bp.route("/api/s3-generation-jobs/<int:job_id>/cancel", methods=["POST"])
@_require_auth_api
def api_s3_generation_cancel(job_id):
    """Cancel a non-terminal S3 generation job (creator or admin/superadmin)."""
    from primeqa.generation.jobs import GenerationJobStore
    store = GenerationJobStore(request.user["tenant_id"])
    job = store.get_job(job_id)
    if job is None:
        return ({"error": {"code": "NOT_FOUND", "message": "Job not found"}}, 404)
    # D-245: ownership OR Admin tier (the old inline ("admin","superadmin")
    # list — superadmin passes via the ladder top).
    if (job.created_by != request.user["id"]
            and not authorize(request.user, Tier.ADMIN)[0]):
        return ({"error": {"code": "FORBIDDEN",
                           "message": "Only the creator or an admin can cancel."}}, 403)
    if job.status in ("completed", "failed", "cancelled"):
        return ({"error": {"code": "JOB_TERMINAL",
                           "message": f"Job already {job.status}."},
                 "status": job.status}, 400)
    store.cancel(job_id)
    return ({"job_id": job_id, "status": "cancelled"}, 200)


# --- Suites ---

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
    from primeqa.test_management.models import Section

    db = next(get_db())
    try:
        tenant_id = request.user["tenant_id"]
        rows = db.query(Section).filter(
            Section.tenant_id == tenant_id,
            Section.deleted_at.is_(None),
        ).order_by(Section.parent_id.nullsfirst(), Section.position).all()

        # D-221 R4: per-section v1 TC counts retired with test_cases.
        tc_counts = {}

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
    """D-219 slice 2: run the release's tests on the SUBSTRATE — one s4
    execution job per approved claim of the release's requirements. The
    button's promise is "run this release's tests", and those are claims
    now; the v1 pipeline path retired with this re-target."""
    from flask import flash
    db = next(get_db())
    try:
        from primeqa.core.repository import EnvironmentRepository
        from primeqa.release.repository import ReleaseRepository
        from primeqa.release.service import ReleaseService
        from primeqa.release.decision_composer import (
            external_keys_for_requirements,
        )
        tid = request.user["tenant_id"]
        env_id = request.form.get("environment_id", type=int)
        if not env_id:
            flash("Pick an environment to run against.", "error")
            return redirect(f"/releases/{release_id}")
        # Phase 3 (D-245): env must be within the caller's accessible set (groups).
        if not EnvironmentRepository(db).is_environment_accessible(
                tid, request.user["id"], request.user["role"], env_id):
            flash("Pick an environment you have access to.", "error")
            return redirect(f"/releases/{release_id}")
        env = EnvironmentRepository(db).get_environment(env_id, tid)
        if env is None:
            flash("Environment not found.", "error")
            return redirect(f"/releases/{release_id}")
        rel_svc = ReleaseService(ReleaseRepository(db))
        release = rel_svc.get_release_detail(release_id, tid)
        if not release:
            flash("Release not found.", "error")
            return redirect("/releases")
        keys = external_keys_for_requirements(release.get("requirements", []))
        if not keys:
            flash("Release has no requirements to run.", "error")
            return redirect(f"/releases/{release_id}?tab=requirements")
    finally:
        db.close()

    from primeqa.execution_engine.intake import enqueue_claims_for_requirements
    try:
        result = enqueue_claims_for_requirements(
            tenant_id=tid, external_keys=keys, environment_id=env_id,
            created_by=request.user["id"])
    except Exception as e:
        flash(f"Could not queue substrate runs: {e}", "error")
        return redirect(f"/releases/{release_id}?tab=decision")
    if result["enqueued"] == 0:
        flash("No approved claims found for this release's requirements — "
              "approve drafts in the claims inbox first.", "error")
        return redirect(f"/releases/{release_id}?tab=decision")
    skipped = result.get("skipped_unexecutable") or 0
    flash(f"{result['enqueued']} substrate run"
          f"{'s' if result['enqueued'] != 1 else ''} queued across "
          f"{result['requirements']} requirement(s)"
          + (f" — {skipped} claim{'s' if skipped != 1 else ''} skipped "
             f"(not yet executable)" if skipped else ""), "success")
    return redirect("/runs/substrate")


@views_bp.route("/releases/<int:release_id>/evaluate-decision", methods=["POST"])
@role_required("admin", "tester")
def releases_evaluate_decision(release_id):
    from flask import flash
    # D-198: route the web button through the composer too (v1 + substrate,
    # one decision row) — same path as the API endpoint.
    from primeqa.release.decision_composer import evaluate_and_record
    db = next(get_db())
    try:
        repo = ReleaseRepository(db)
        release = repo.get_release(release_id, request.user["tenant_id"])
        if not release:
            return redirect("/releases")
        result = evaluate_and_record(
            db, release, request.user["tenant_id"], release_repo=repo)
        rec = result["recommendation"].upper().replace("_", " ")
        suffix = ""
        if result.get("recommendation_source") == "substrate_gate":
            suffix = " — degraded by substrate evidence"
        flash(f"Decision: {rec} ({int(result['confidence']*100)}% confidence)"
              + suffix, "success")
    except Exception as e:
        flash(f"Evaluation failed: {e}", "error")
    finally:
        db.close()
    return redirect(f"/releases/{release_id}?tab=decision")


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

        # D-172 (5a): substrate evidence for the Decision tab — the release's
        # requirements' claims -> S8 grounding-drift + S6 latest verdict
        # (best-effort; the v1 DecisionEngine stays the verdict authority). The
        # substrate path is via requirements only (jira_key or req-<id>).
        substrate = None
        substrate_decision = None
        if tab == "decision":
            from primeqa.intelligence.release_substrate_console import get_release_substrate
            from primeqa.intelligence.substrate_decision import (
                get_release_substrate_decision,
            )
            from primeqa.release.decision_composer import external_keys_for_requirements
            external_keys = external_keys_for_requirements(
                release.get("requirements", []))
            substrate = get_release_substrate(tid, external_keys)
            # D-198 (slice 4): the live substrate RECOMMENDATION card — recomputed
            # at render like the evidence panel; the persisted snapshot lives in
            # latest_decision.reasoning.substrate.
            substrate_decision = get_release_substrate_decision(
                tid, external_keys, release.get("decision_criteria") or {})

        # D-221 R4: the D-212 parity view retired with D-220 (zero v1 corpus —
        # nothing to compare). The tab renders nothing.
        parity = None

        return render_template("releases/detail.html", **ctx(
            active_page="releases", release=release, tab=tab,
            all_requirements=all_requirements,
            environments=envs_data, substrate=substrate,
            substrate_decision=substrate_decision, parity=parity,
        ))
    finally:
        db.close()


# --- D-221 (5b-R1): v1 page URLs redirect to their substrate successors ----
# The v1 pages served tables that were verified EMPTY (D-220); bookmarks and
# stale links land on the live surface instead of a 404.

@views_bp.route("/runs")
@login_required
def runs_list_redirect():
    return redirect("/runs/substrate")


@views_bp.route("/runs/<int:run_id>")
@login_required
def runs_detail_redirect(run_id):
    return redirect("/runs/substrate")


@views_bp.route("/results/<int:run_id>")
@login_required
def result_detail_redirect(run_id):
    return redirect("/runs/substrate")


@views_bp.route("/test-cases")
@views_bp.route("/test-cases/<int:tc_id>")
@login_required
def test_cases_redirect(tc_id=None):
    return redirect("/claims")


@views_bp.route("/reviews")
@login_required
def reviews_redirect():
    return redirect("/claims/inbox")


@views_bp.route("/tickets")
@login_required
def tickets_redirect():
    return redirect("/requirements")


@views_bp.route("/suites")
@views_bp.route("/suites/<int:suite_id>")
@login_required
def suites_redirect(suite_id=None):
    return redirect("/claims")
