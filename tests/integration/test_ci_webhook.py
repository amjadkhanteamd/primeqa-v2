"""CI webhook (POST /api/webhooks/ci-trigger) security tests.

SEC-7: a production run must be explicitly confirmed in the webhook payload; the
machine caller (global WEBHOOK_SECRET, no user tier) + the system-run queued job
mean the production decision must be made at this enqueue boundary.

(TEST-1 extends this file with the HMAC / fail-closed-503 / A5 cross-tenant
coverage — see test_ci_webhook_hmac_and_tenant_guard.)

Integration test against the real Railway DB; self-cleaning. Sets WEBHOOK_SECRET
in-process (restored afterwards) and signs the raw body with HMAC-SHA256.
"""
import hashlib
import hmac
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv
load_dotenv()

from primeqa.app import app
from primeqa.db import SessionLocal
from primeqa.core.models import User, Environment
from primeqa.release.repository import ReleaseRepository

client = app.test_client()
TENANT_ID = 1
_SECRET = "sec7-webhook-secret-" + uuid.uuid4().hex


def _post_webhook(payload: dict):
    body = json.dumps(payload).encode()
    sig = hmac.new(_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/api/webhooks/ci-trigger", data=body,
                       content_type="application/json",
                       headers={"X-PrimeQA-Signature": sig})


def _mk_env(db, uid, *, is_production):
    env = Environment(
        tenant_id=TENANT_ID,
        name=f"CIW-{'prod' if is_production else 'sbx'}-{uuid.uuid4().hex[:6]}",
        env_type="production" if is_production else "sandbox",
        is_production=is_production,
        sf_instance_url="https://acme.my.salesforce.com",
        sf_api_version="60.0", created_by=uid)
    db.add(env); db.commit(); db.refresh(env)
    return env


def test_ci_webhook_production_gate():
    prev = os.environ.get("WEBHOOK_SECRET")
    os.environ["WEBHOOK_SECRET"] = _SECRET
    db = SessionLocal()
    uid = db.query(User.id).filter(User.tenant_id == TENANT_ID).limit(1).scalar()
    assert uid is not None, "seed data: expected a user in tenant 1"
    prod_env = _mk_env(db, uid, is_production=True)
    sbx_env = _mk_env(db, uid, is_production=False)
    rel = ReleaseRepository(db).create_release(TENANT_ID, f"CIW-{uuid.uuid4().hex[:8]}", uid)
    rid, prod_eid, sbx_eid = rel.id, prod_env.id, sbx_env.id
    db.close()
    try:
        # (A) production env, NO confirm_production -> blocked (403), no run.
        r = _post_webhook({"release_id": rid, "environment_id": prod_eid})
        assert r.status_code == 403, \
            f"CI webhook against prod without confirm expected 403, got {r.status_code}: {r.data[:200]}"

        # (B) production env WITH confirm_production -> passes the gate (not 403).
        r = _post_webhook({"release_id": rid, "environment_id": prod_eid,
                           "confirm_production": True})
        assert r.status_code != 403, \
            f"CI webhook against prod WITH confirm was wrongly blocked: {r.data[:200]}"

        # (C) non-production env, NO confirm -> gate not triggered (not 403).
        r = _post_webhook({"release_id": rid, "environment_id": sbx_eid})
        assert r.status_code != 403, \
            f"CI webhook against a sandbox env was wrongly blocked: {r.data[:200]}"
    finally:
        if prev is None:
            os.environ.pop("WEBHOOK_SECRET", None)
        else:
            os.environ["WEBHOOK_SECRET"] = prev
        db = SessionLocal()
        from primeqa.release.models import Release
        for model, _id in ((Release, rid),):
            row = db.query(model).filter_by(id=_id).first()
            if row:
                db.delete(row)
        for _id in (prod_eid, sbx_eid):
            e = db.query(Environment).filter_by(id=_id).first()
            if e:
                db.delete(e)
        db.commit()
        db.close()


if __name__ == "__main__":
    test_ci_webhook_production_gate()
    print("PASS  SEC-7 CI webhook production gate")
