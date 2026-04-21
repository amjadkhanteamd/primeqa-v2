"""Developer experience: /tickets page backend.

Responsibilities:
  - Resolve the caller's "active" environment (preference → personal env
    → team env), inheriting Jira / AI / Knowledge config from parent.
  - Fetch Jira tickets assigned to the caller.
  - Match tickets to the most recent pipeline run via requirements.jira_key.
  - Sort tickets for the Developer's triage view
    (running → failed → untested → passed).

Intentionally small — the /tickets page is the Developer's entire UI,
so this module stays focused and easy to reason about.
"""

from __future__ import annotations

from base64 import b64encode
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from primeqa.core.models import Connection, Environment, User
from primeqa.core.crypto import decrypt
from primeqa.execution.models import PipelineRun, RunTestResult
from primeqa.test_management.models import Requirement, TestCase


# --------------------------------------------------------------------------
# Active environment resolution.
# --------------------------------------------------------------------------

STALE_PERSONAL_ENV_DAYS = 14


def resolve_active_environment(user: User, db: Session) -> Optional[Environment]:
    """Return the `Environment` the Developer should see tickets for.

    Priority:
      1. users.preferred_environment_id, if the env is still reachable.
      2. Most recently-created personal env owned by this user.
      3. First team env in the tenant.
      4. None.
    """
    if user.preferred_environment_id:
        env = db.query(Environment).filter_by(id=user.preferred_environment_id).first()
        if env and env.tenant_id == user.tenant_id and env.is_active:
            return env

    personal = (db.query(Environment)
                .filter(Environment.tenant_id == user.tenant_id,
                        Environment.owner_user_id == user.id,
                        Environment.environment_type == "personal",
                        Environment.is_active == True)
                .order_by(Environment.created_at.desc())
                .first())
    if personal:
        return personal

    team = (db.query(Environment)
            .filter(Environment.tenant_id == user.tenant_id,
                    Environment.environment_type == "team",
                    Environment.is_active == True)
            .order_by(Environment.created_at.desc())
            .first())
    if team:
        return team

    # Fall back to ANY active env in tenant (covers tenants that haven't
    # tagged their envs with environment_type yet — pre-migration-039 data).
    fallback = (db.query(Environment)
                .filter(Environment.tenant_id == user.tenant_id,
                        Environment.is_active == True)
                .order_by(Environment.created_at.desc())
                .first())
    return fallback


def list_switchable_environments(user: User, db: Session) -> list[dict]:
    """Return the list that populates the Active Org switcher.

    Personal envs first, then team envs. Each dict carries a `stale`
    flag for UI rendering (personal envs untouched for 14+ days).
    """
    personal = (db.query(Environment)
                .filter(Environment.tenant_id == user.tenant_id,
                        Environment.owner_user_id == user.id,
                        Environment.environment_type == "personal",
                        Environment.is_active == True)
                .order_by(Environment.created_at.desc())
                .all())
    team = (db.query(Environment)
            .filter(Environment.tenant_id == user.tenant_id,
                    or_(Environment.environment_type == "team",
                        Environment.environment_type.is_(None)),
                    Environment.is_active == True)
            .order_by(Environment.created_at.desc())
            .all())
    now = datetime.now(timezone.utc)
    stale_threshold = now - timedelta(days=STALE_PERSONAL_ENV_DAYS)
    out: list[dict] = []
    for env in personal:
        updated = env.updated_at or env.created_at
        stale = (updated < stale_threshold) if updated else False
        out.append({
            "id": env.id,
            "name": env.name,
            "kind": "personal",
            "stale": bool(stale),
        })
    for env in team:
        out.append({
            "id": env.id,
            "name": env.name,
            "kind": "team",
            "stale": False,
        })
    return out


# --------------------------------------------------------------------------
# Jira helpers.
# --------------------------------------------------------------------------

def _resolve_jira_connection(env: Environment, db: Session) -> Optional[Connection]:
    """Find the Jira connection to use for this env.

    Personal envs inherit from their parent team env — the developer
    never has to configure Jira themselves.
    """
    if env.jira_connection_id:
        conn = db.query(Connection).filter_by(id=env.jira_connection_id).first()
        if conn and conn.connection_type == "jira":
            return conn
    if env.parent_team_env_id:
        parent = db.query(Environment).filter_by(id=env.parent_team_env_id).first()
        if parent and parent.jira_connection_id:
            conn = db.query(Connection).filter_by(id=parent.jira_connection_id).first()
            if conn and conn.connection_type == "jira":
                return conn
    # Last resort: any Jira connection in this tenant.
    return (db.query(Connection)
            .filter_by(tenant_id=env.tenant_id, connection_type="jira", status="active")
            .order_by(Connection.created_at.desc())
            .first())


def _build_jira_client(conn: Connection):
    """Build a JiraClient from a Connection row. Handles the stored auth
    shape and decrypts when needed (existing pattern)."""
    # Import here to avoid a module-load cycle.
    from primeqa.runs.wizard import JiraClient

    cfg = dict(conn.config or {})
    base_url = (cfg.get("base_url") or "").rstrip("/")
    email = cfg.get("email") or cfg.get("username")
    token = cfg.get("api_token") or cfg.get("token")
    if token and token.startswith("enc:"):
        try:
            token = decrypt(token[4:])
        except Exception:
            token = None
    basic_b64 = None
    if email and token:
        basic_b64 = b64encode(f"{email}:{token}".encode("utf-8")).decode("ascii")
    return JiraClient(base_url, basic_b64)


def fetch_my_tickets(user: User, env: Environment, db: Session,
                     *, limit: int = 50) -> list[dict]:
    """Query Jira for tickets assigned to `user` in `env`'s Jira.

    Uses the user's email for assignee matching. Returns a list of
    ticket dicts (key, summary, status, priority, sprint_name) — empty
    list on any Jira error (surfaced to UI as the "could not reach
    Jira" empty state).
    """
    conn = _resolve_jira_connection(env, db)
    if conn is None:
        return []
    client = _build_jira_client(conn)
    # JQL: assignee matches user's email. ORDER BY priority DESC,
    # updated DESC so the most-recently-touched high-priority tickets
    # float to the top before our client-side triage sort reorders
    # anything with a recent run.
    email = (user.email or "").replace('"', '\\"')
    jql = (f'assignee = "{email}" AND resolution = Unresolved '
           f'ORDER BY priority DESC, updated DESC')
    try:
        # Reuse the tokenised search_issues path where possible —
        # faster-path for the wizard cache. For assignee queries we go
        # direct because we need the full fields set.
        import requests as _r
        r = _r.get(f"{client.base_url}/rest/api/3/search",
                   headers=client.headers,
                   params={"jql": jql, "maxResults": limit,
                           "fields": "summary,status,priority,customfield_10020,issuetype"},
                   timeout=15)
        r.raise_for_status()
        body = r.json()
    except Exception:
        return []

    out: list[dict] = []
    for issue in body.get("issues", []):
        fields = issue.get("fields") or {}
        status = (fields.get("status") or {}).get("name")
        priority = (fields.get("priority") or {}).get("name")
        # customfield_10020 is Sprint on Jira Cloud (standard Agile field).
        sprints = fields.get("customfield_10020") or []
        sprint_name = None
        if isinstance(sprints, list):
            for sp in sprints:
                if isinstance(sp, dict):
                    sprint_name = sp.get("name")
                    break
                # Legacy string format: "com.atlassian...[id=1,name=SP-24,…]"
                if isinstance(sp, str) and "name=" in sp:
                    try:
                        sprint_name = sp.split("name=", 1)[1].split(",", 1)[0]
                        break
                    except Exception:
                        pass
        out.append({
            "key": issue.get("key"),
            "summary": fields.get("summary") or "",
            "status": status,
            "priority": priority or "Medium",
            "sprint": sprint_name,
        })
    return out


# --------------------------------------------------------------------------
# Last-run lookup + triage sort.
# --------------------------------------------------------------------------

def _priority_rank(name: Optional[str]) -> int:
    return {
        "Highest": 0, "High": 1, "Medium": 2, "Low": 3, "Lowest": 4,
    }.get(name or "", 2)


# Sort bucket for a ticket based on its latest run status.
_RUN_BUCKET: dict[str, int] = {
    "running": 0,
    "queued": 0,
    "failed": 1,
    "error": 1,
    "cancelled": 1,
    "untested": 2,
    "passed": 3,
    "completed": 3,  # treat as "passed" (result-level failures move to bucket 1)
}


def attach_latest_runs(tickets: list[dict], env: Environment, db: Session) -> list[dict]:
    """For each ticket, attach `.last_run` (dict or None).

    Strategy: find the requirement row for the ticket's jira_key in the
    tenant, then find the most recent pipeline_run whose source_refs /
    source_ids reference that requirement, filtered to the active env.
    """
    if not tickets:
        return tickets
    keys = [t["key"] for t in tickets if t.get("key")]
    if not keys:
        return tickets

    reqs = (db.query(Requirement)
            .filter(Requirement.tenant_id == env.tenant_id,
                    Requirement.jira_key.in_(keys),
                    Requirement.deleted_at.is_(None))
            .all())
    req_by_key = {r.jira_key: r for r in reqs}

    # For each requirement with TCs, the latest pipeline_run that produced
    # results for any of those TCs is the "last run".
    # Use a window query: latest run per (environment, test_case) then
    # roll up to requirement.
    for t in tickets:
        req = req_by_key.get(t["key"])
        t["requirement_id"] = req.id if req else None
        if req is None:
            t["last_run"] = None
            continue
        # Find a pipeline_run that produced results for a TC belonging to
        # this requirement, in the active env. Take the most recent.
        run_row = (db.query(PipelineRun)
                   .join(RunTestResult, RunTestResult.run_id == PipelineRun.id)
                   .join(TestCase, TestCase.id == RunTestResult.test_case_id)
                   .filter(PipelineRun.tenant_id == env.tenant_id,
                           PipelineRun.environment_id == env.id,
                           TestCase.requirement_id == req.id,
                           TestCase.deleted_at.is_(None))
                   .order_by(PipelineRun.queued_at.desc())
                   .first())
        if run_row is None:
            t["last_run"] = None
        else:
            # Classify for bucket: running / failed / passed / etc.
            if run_row.status in ("queued", "running"):
                bucket_status = "running"
            elif run_row.failed and run_row.failed > 0:
                bucket_status = "failed"
            elif run_row.status == "completed" and (run_row.failed or 0) == 0:
                bucket_status = "passed"
            elif run_row.status in ("failed", "cancelled"):
                bucket_status = "failed"
            else:
                bucket_status = run_row.status or "untested"
            t["last_run"] = {
                "id": run_row.id,
                "status": run_row.status,
                "bucket": bucket_status,
                "passed": run_row.passed or 0,
                "failed": run_row.failed or 0,
                "total": run_row.total_tests or 0,
                "queued_at": run_row.queued_at.isoformat() if run_row.queued_at else None,
                "completed_at": run_row.completed_at.isoformat() if run_row.completed_at else None,
            }
    return tickets


def sort_for_triage(tickets: list[dict]) -> list[dict]:
    """Sort tickets for the Developer's triage view.

    running → failed → untested → passed; within each bucket, Jira
    priority then ticket key.
    """
    def bucket(t: dict) -> int:
        lr = t.get("last_run")
        if not lr:
            return _RUN_BUCKET["untested"]
        return _RUN_BUCKET.get(lr["bucket"], _RUN_BUCKET["untested"])

    def key(t: dict):
        return (bucket(t), _priority_rank(t.get("priority")), t.get("key") or "")

    return sorted(tickets, key=key)


__all__ = [
    "STALE_PERSONAL_ENV_DAYS",
    "resolve_active_environment",
    "list_switchable_environments",
    "fetch_my_tickets",
    "attach_latest_runs",
    "sort_for_triage",
]
