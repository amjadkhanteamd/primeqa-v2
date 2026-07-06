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


def test_ci_webhook_hmac_and_tenant_guard():
    """TEST-1: the four security branches of the previously-untested ingress —
    fail-closed 503 (unset secret), 401 (bad/missing HMAC), the A5 cross-tenant
    guard (env from a different tenant -> 404), and a valid same-tenant request
    reaching the enqueue path."""
    from primeqa.core.models import Tenant
    prev = os.environ.get("WEBHOOK_SECRET")
    db = SessionLocal()
    uid = db.query(User.id).filter(User.tenant_id == TENANT_ID).limit(1).scalar()
    assert uid is not None, "seed data: expected a user in tenant 1"
    rel = ReleaseRepository(db).create_release(TENANT_ID, f"CIW2-{uuid.uuid4().hex[:8]}", uid)
    sbx = _mk_env(db, uid, is_production=False)
    t2 = Tenant(name=f"CIW-t2-{uuid.uuid4().hex[:6]}", slug=f"ciw-{uuid.uuid4().hex[:6]}")
    db.add(t2); db.commit(); db.refresh(t2)
    foreign_env = Environment(tenant_id=t2.id, name=f"CIW-foreign-{uuid.uuid4().hex[:6]}",
                              env_type="sandbox", is_production=False,
                              sf_instance_url="https://acme.my.salesforce.com",
                              sf_api_version="60.0", created_by=uid)
    db.add(foreign_env); db.commit(); db.refresh(foreign_env)
    rid, sbx_eid, foreign_eid, t2_id = rel.id, sbx.id, foreign_env.id, t2.id
    db.close()
    try:
        # (1) WEBHOOK_SECRET unset -> fail closed 503 (before any signature check).
        os.environ.pop("WEBHOOK_SECRET", None)
        r = _post_webhook({"release_id": rid, "environment_id": sbx_eid})
        assert r.status_code == 503, f"unset secret expected 503, got {r.status_code}"

        os.environ["WEBHOOK_SECRET"] = _SECRET
        body = json.dumps({"release_id": rid, "environment_id": sbx_eid}).encode()
        # (2) bad signature -> 401 (constant-time compare_digest).
        r = client.post("/api/webhooks/ci-trigger", data=body, content_type="application/json",
                        headers={"X-PrimeQA-Signature": "deadbeef"})
        assert r.status_code == 401, f"bad signature expected 401, got {r.status_code}"
        # (2b) missing signature -> 401.
        r = client.post("/api/webhooks/ci-trigger", data=body, content_type="application/json")
        assert r.status_code == 401, f"missing signature expected 401, got {r.status_code}"

        # (3) A5 cross-tenant guard: valid HMAC but env belongs to a DIFFERENT
        #     tenant than the release -> 404 (env not found for this tenant).
        r = _post_webhook({"release_id": rid, "environment_id": foreign_eid})
        assert r.status_code == 404, \
            f"cross-tenant env expected 404, got {r.status_code}: {r.data[:200]}"

        # (4) valid HMAC + same-tenant non-prod env -> passes HMAC + A5 + the SEC-7
        #     prod gate and reaches the enqueue path (this release has no
        #     requirements, so it lands on the 'no substrate claims' 400, proving
        #     every security gate was cleared).
        r = _post_webhook({"release_id": rid, "environment_id": sbx_eid})
        assert r.status_code == 400 and b"No substrate claims" in r.data, \
            f"a valid same-tenant request did not reach the enqueue path: {r.status_code} {r.data[:200]}"
    finally:
        if prev is None:
            os.environ.pop("WEBHOOK_SECRET", None)
        else:
            os.environ["WEBHOOK_SECRET"] = prev
        db = SessionLocal()
        from primeqa.release.models import Release
        rel_row = db.query(Release).filter_by(id=rid).first()
        if rel_row:
            db.delete(rel_row)
        for _id in (sbx_eid, foreign_eid):
            e = db.query(Environment).filter_by(id=_id).first()
            if e:
                db.delete(e)
        t2row = db.query(Tenant).filter_by(id=t2_id).first()
        if t2row:
            db.delete(t2row)
        db.commit()
        db.close()


if __name__ == "__main__":
    test_ci_webhook_production_gate()
    test_ci_webhook_hmac_and_tenant_guard()
    print("PASS  CI webhook: production gate + HMAC/tenant guard")
