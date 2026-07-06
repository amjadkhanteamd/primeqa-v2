"""SEC-1 regression: GET /api/connections/<id> must be admin-gated AND must
never return decrypted secrets.

Before the fix the route was ``@require_auth`` (any authenticated role) and
returned ``get_connection_decrypted()`` verbatim, so a viewer could read the
tenant's Salesforce/Jira/LLM ``client_secret`` / ``password`` / ``api_token`` in
cleartext. The fix admin-gates the route and returns the redacted ``_conn_dict``
display shape (no ``config``).

Integration test against the real Railway DB; self-cleaning. Tokens are minted
directly (require_auth trusts the JWT role claim), so this creates no users.

Run: python tests/integration/test_connection_authz.py  (or via pytest)
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
from primeqa.core.models import Connection, User
from primeqa.core.secrets import get_jwt_secret

client = app.test_client()
TENANT_ID = 1


def _mint(role, uid):
    # require_auth requires sub + tenant_id and reads role from the claim
    # (core/auth.py), verifying with get_jwt_secret() — sign with the same.
    return jwt.encode(
        {"sub": str(uid), "tenant_id": TENANT_ID, "role": role},
        get_jwt_secret(), algorithm="HS256")


def _a_user_id(db):
    u = db.query(User).filter_by(tenant_id=TENANT_ID).first()
    assert u is not None, "seed data: expected at least one user in tenant 1"
    return u.id


def test_get_connection_is_admin_gated_and_secret_masked():
    marker = "SEC1_SECRET_" + uuid.uuid4().hex
    db = SessionLocal()
    uid = _a_user_id(db)
    # ORM insert stores config as-is (encryption lives in the service create
    # path, not the ORM) — the display path never decrypts, so the marker must
    # simply never surface.
    conn = Connection(tenant_id=TENANT_ID, connection_type="jira",
                      name=f"SEC1-{uuid.uuid4().hex[:8]}",
                      config={"client_secret": marker, "api_token": marker,
                              "password": marker},
                      status="inactive", created_by=uid)
    db.add(conn); db.commit(); db.refresh(conn)
    cid = conn.id
    db.close()
    try:
        # (a) Non-admin roles are rejected (was 200 + plaintext secrets).
        for role in ("viewer", "ba", "tester"):
            r = client.get(f"/api/connections/{cid}",
                           headers={"Authorization": f"Bearer {_mint(role, uid)}"})
            assert r.status_code == 403, \
                f"{role} expected 403 on GET /api/connections/{cid}, got {r.status_code}"

        # (b) Admin gets 200, but the response carries NO decrypted secret and
        #     no `config` block at all (redacted _conn_dict shape).
        r = client.get(f"/api/connections/{cid}",
                       headers={"Authorization": f"Bearer {_mint('admin', uid)}"})
        assert r.status_code == 200, \
            f"admin expected 200, got {r.status_code}: {r.data[:200]}"
        body = r.get_data(as_text=True)
        assert marker not in body, "SEC-1 REGRESSION: a decrypted secret leaked in the admin response"
        payload = r.get_json() or {}
        assert "config" not in payload, \
            f"response must carry no config block, got keys {sorted(payload.keys())}"
        # still returns the non-secret display metadata
        assert payload.get("id") == cid and payload.get("name")
    finally:
        db = SessionLocal()
        c = db.query(Connection).filter_by(id=cid).first()
        if c:
            db.delete(c)
            db.commit()
        db.close()


if __name__ == "__main__":
    test_get_connection_is_admin_gated_and_secret_masked()
    print("PASS  SEC-1 connection authz + masking")
