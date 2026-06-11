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
