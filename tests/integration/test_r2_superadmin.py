"""R2 — Super Admin cap exclusion, agent settings, cost forecast.

Round 4 (AUD-051): converted from an in-file custom runner (``run_tests()``
returned False and named no assertion) to plain pytest — one test per check,
so a red names its assertion. The checks themselves are unchanged.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from primeqa.app import app  # noqa: E402

TENANT_ID = 1


@pytest.fixture
def client():
    return app.test_client()


@pytest.fixture
def admin_token():
    """A SUPERADMIN of the tenant, planted for the test and removed after it
    (with the audit rows it wrote — activity_log's foreign key would keep it
    alive). The seed admin@primeqa.io is a superadmin on production but an
    admin on scratch, so the suite carries its own; the token is minted the
    way the app mints one."""
    from datetime import datetime, timedelta, timezone

    import jwt

    from primeqa.core.models import ActivityLog, User
    from primeqa.db import SessionLocal
    db = SessionLocal()
    u = User(tenant_id=TENANT_ID, email="r2-superadmin@test.local", password_hash="x",
             full_name="R2 superadmin", role="superadmin", is_active=True)
    db.add(u); db.commit(); db.refresh(u)
    uid = u.id
    db.close()
    now = datetime.now(timezone.utc)
    token = jwt.encode({"sub": str(uid), "tenant_id": TENANT_ID, "role": "superadmin",
                        "email": "r2-superadmin@test.local", "full_name": "R2 superadmin",
                        "iat": now, "exp": now + timedelta(minutes=10)},
                       os.environ["JWT_SECRET"], algorithm="HS256")
    yield token
    db = SessionLocal()
    try:
        db.query(ActivityLog).filter(ActivityLog.user_id == uid).delete(synchronize_session=False)
        db.query(User).filter(User.id == uid).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def test_r2_1_count_active_users_excludes_superadmin():
    from primeqa.core.models import User
    from primeqa.core.repository import UserRepository
    from primeqa.db import SessionLocal
    db = SessionLocal()
    try:
        count = UserRepository(db).count_active_users(TENANT_ID)
        total_active = db.query(User).filter(
            User.tenant_id == TENANT_ID, User.is_active == True).count()  # noqa: E712
        superadmins = db.query(User).filter(
            User.tenant_id == TENANT_ID, User.is_active == True,  # noqa: E712
            User.role == "superadmin").count()
        assert count == total_active - superadmins, \
            f"cap count {count} should exclude {superadmins} superadmin(s) from total {total_active}"
    finally:
        db.close()


def test_r2_2_settings_agent_renders_for_superadmin(client, admin_token):
    client.set_cookie("access_token", admin_token)
    r = client.get("/settings/agent")
    assert r.status_code == 200
    body = r.data.decode()
    assert "Agent autonomy" in body
    # Step A: the ONE home for the repair agent's safety controls
    assert "repair_gate_apply_enabled" in body
    assert "repair_auto_apply" in body
    assert "trust_threshold" not in body


def test_r2_3_superadmin_updates_agent_flags_and_attempts(client, admin_token):
    client.set_cookie("access_token", admin_token)
    payload = {
        "agent_enabled": "1",
        # repair_auto_apply absent => false; the gate switch on
        "repair_gate_apply_enabled": "1",
        "max_fix_attempts_per_run": "2",
    }
    # CSRF enforcement is correct: a state-changing POST with NO token is
    # rejected 403 (a web form route, not /api/* Bearer).
    assert client.post("/settings/agent", data=payload).status_code == 403, \
        "CSRF should reject the agent-settings POST when no token is supplied"
    # The legit path supplies the double-submit token (cookie == form field).
    csrf = "sec-wave1-csrf-token"
    client.set_cookie("csrf_token", csrf)
    r = client.post("/settings/agent", data={**payload, "csrf_token": csrf})
    assert r.status_code in (200, 302), \
        f"expected 200/302 with a valid CSRF token, got {r.status_code}"
    from primeqa.core.agent_settings import AgentSettingsRepository
    from primeqa.db import SessionLocal
    db = SessionLocal()
    try:
        s = AgentSettingsRepository(db).get(TENANT_ID)
        assert s.agent_enabled is True
        assert s.repair_gate_apply_enabled is True
        assert s.repair_auto_apply is False
        assert s.max_fix_attempts_per_run == 2
    finally:
        # restore the dormant default so the tenant is left as found
        AgentSettingsRepository(db).update(
            TENANT_ID, updated_by=1, repair_gate_apply_enabled=False)
        db.close()


def test_r2_4_out_of_range_attempt_cap_rejected():
    from primeqa.core.agent_settings import AgentSettingsRepository
    from primeqa.db import SessionLocal
    db = SessionLocal()
    try:
        repo = AgentSettingsRepository(db)
        with pytest.raises(ValueError):
            repo.update(TENANT_ID, updated_by=1, max_fix_attempts_per_run=11)
        db.rollback()
    finally:
        db.close()


# R2-5 / R2-6 (the cost forecast, ``primeqa.runs.cost``) retired with the v1
# product layer (D-191..D-221); the two checks have no subject and are gone.


def test_r2_7_release_decisions_agent_verdict_counts_exists():
    from sqlalchemy import text

    from primeqa.db import SessionLocal
    db = SessionLocal()
    try:
        rows = list(db.execute(text(
            "SELECT column_name, column_default FROM information_schema.columns "
            "WHERE table_name='release_decisions' AND column_name='agent_verdict_counts'")))
        assert rows, "agent_verdict_counts column missing"
    finally:
        db.close()
