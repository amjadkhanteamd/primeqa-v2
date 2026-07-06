"""SEC-4: POST /releases/<id>/run enforces the production gate at the enqueue
boundary. This path enqueues to s4_execution_jobs, which the worker later runs
as system (caller_tier=None), so the execution chokepoint's production-role rule
is structurally bypassed — the gate MUST live in the route.

Proves: a non-Admin (with env access) cannot run a production org; even an Admin
must confirm_production; an Admin WITH confirm passes the gate. Integration test
against the real Railway DB; self-cleaning. Web POST → double-submit CSRF is
satisfied (cookie + matching form field).

Run: python tests/integration/test_release_run_prod_gate.py  (or via pytest)
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv
load_dotenv()

import jwt

from primeqa.app import app
from primeqa.db import SessionLocal
from primeqa.core.models import User, Environment
from primeqa.core.secrets import get_jwt_secret
from primeqa.release.repository import ReleaseRepository

client = app.test_client()
TENANT_ID = 1


def _mint(role, uid):
    return jwt.encode({"sub": str(uid), "tenant_id": TENANT_ID, "role": role},
                      get_jwt_secret(), algorithm="HS256")


def _post_run(release_id, token, env_id, confirm=False):
    # Double-submit CSRF: cookie value must equal the submitted form field.
    csrf = "csrf-" + uuid.uuid4().hex
    client.set_cookie("csrf_token", csrf)
    client.set_cookie("access_token", token)
    data = {"environment_id": str(env_id), "csrf_token": csrf}
    if confirm:
        data["confirm_production"] = "on"
    # follow the redirect so the flash renders on the release page.
    return client.post(f"/releases/{release_id}/run", data=data, follow_redirects=True)


def test_release_run_production_gate():
    db = SessionLocal()
    admin = db.query(User).filter(
        User.tenant_id == TENANT_ID,
        User.role.in_(("admin", "superadmin")),
        User.is_active == True).first()
    assert admin is not None, "seed data: expected an admin/superadmin in tenant 1"
    # A tester user who OWNS the env, so it is in their accessible set (created-by)
    # without needing a group — isolates the production gate from the env-access gate.
    tester = User(tenant_id=TENANT_ID, email=f"sec4-{uuid.uuid4().hex[:8]}@example.invalid",
                  password_hash="x", full_name="SEC4 Tester", role="tester", is_active=True)
    db.add(tester); db.commit(); db.refresh(tester)
    prod_env = Environment(tenant_id=TENANT_ID, name=f"SEC4-prod-{uuid.uuid4().hex[:6]}",
                           env_type="production", is_production=True,
                           sf_instance_url="https://acme.my.salesforce.com",
                           sf_api_version="60.0", created_by=tester.id)
    db.add(prod_env); db.commit(); db.refresh(prod_env)
    admin_id = admin.id
    rel = ReleaseRepository(db).create_release(TENANT_ID, f"SEC4-{uuid.uuid4().hex[:8]}", admin_id)
    rid, eid, tester_id = rel.id, prod_env.id, tester.id
    db.close()
    try:
        # (1) non-Admin (tester, WITH access as the env's creator) + prod + confirm
        #     -> BLOCKED by the Admin-only production rule (the core SEC-4 fix).
        r = _post_run(rid, _mint("tester", tester_id), eid, confirm=True)
        assert b"requires an Admin" in r.data, \
            "SEC-4: a non-Admin was NOT blocked from a production run"

        # (2) Admin + prod + NO confirm -> BLOCKED (explicit confirmation required).
        r = _post_run(rid, _mint("admin", admin_id), eid, confirm=False)
        assert b"Production org confirmation required" in r.data, \
            "SEC-4: a production run without confirm_production was NOT blocked"

        # (3) Admin + prod + confirm -> PASSES the gate (reaches the release/keys
        #     logic; the release has no requirements, so it lands on that branch
        #     rather than any gate-block message).
        r = _post_run(rid, _mint("admin", admin_id), eid, confirm=True)
        assert b"requires an Admin" not in r.data \
            and b"Production org confirmation required" not in r.data, \
            "SEC-4: an Admin WITH confirm_production was wrongly blocked by the gate"
        assert b"no requirements to run" in r.data, \
            "expected the Admin+confirm run to pass the gate to the no-requirements branch"
    finally:
        db = SessionLocal()
        from primeqa.release.models import Release
        rel_row = db.query(Release).filter_by(id=rid).first()
        if rel_row:
            db.delete(rel_row)
        env_row = db.query(Environment).filter_by(id=eid).first()
        if env_row:
            db.delete(env_row)
        u = db.query(User).filter_by(id=tester_id).first()
        if u:
            db.delete(u)
        db.commit()
        db.close()


if __name__ == "__main__":
    test_release_run_production_gate()
    print("PASS  SEC-4 release-run production gate")
