"""Release-domain audit-logging tests — integration against Railway.

CLAUDE.md: "Every destructive/admin action writes to activity_log via the
service layer." An adversarial review (2026-06-15) found the release domain
wrote ZERO activity_log entries. ReleaseService now has a best-effort `_log`
helper and every release route calls it after a successful action.

These tests drive the real Flask routes (admin token) and assert the
activity_log rows land — with special attention to the status-token mint, which
must record that a token was minted WITHOUT ever logging the raw token.

Run: python tests/test_release_audit.py
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from primeqa.app import app
from primeqa.db import SessionLocal

client = app.test_client()

ADMIN_EMAIL = "admin@primeqa.io"
ADMIN_PASSWORD = "changeme123"
TENANT_ID = 1
results = []


def test(name, fn):
    try:
        fn()
        print(f"  PASS  {name}")
        return True
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        return False
    except Exception as e:
        import traceback
        print(f"  ERROR {name}: {type(e).__name__}: {e}")
        traceback.print_exc()
        return False


def login(email, password):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    return (r.get_json() or {}).get("access_token")


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def _audit_rows(rid, action=None):
    from primeqa.core.models import ActivityLog
    db = SessionLocal()
    try:
        q = db.query(ActivityLog).filter(
            ActivityLog.tenant_id == TENANT_ID,
            ActivityLog.entity_type == "release",
            ActivityLog.entity_id == rid,
        )
        if action:
            q = q.filter(ActivityLog.action == action)
        return q.all()
    finally:
        db.close()


def _cleanup(rid):
    from primeqa.core.models import ActivityLog
    from primeqa.release.models import Release
    db = SessionLocal()
    try:
        db.query(ActivityLog).filter(
            ActivityLog.entity_type == "release",
            ActivityLog.entity_id == rid).delete()
        rel = db.query(Release).filter(Release.id == rid).first()
        if rel:
            db.delete(rel)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def run_tests():
    print("\n=== Release audit-logging tests ===\n")
    admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    assert admin, "admin login failed — check seed creds"

    sfx = uuid.uuid4().hex[:8]
    r = client.post("/api/releases", headers=auth(admin), json={
        "name": f"AUDIT-{sfx}",
    })
    assert r.status_code == 201, f"create release failed: {r.status_code} {r.data[:200]}"
    rid = r.get_json()["id"]

    try:
        # 1. create writes a release.created row with the acting user + name.
        def t_created():
            rows = _audit_rows(rid, "release.created")
            assert len(rows) == 1, f"expected 1 release.created row, got {len(rows)}"
            row = rows[0]
            assert row.user_id is not None, "release.created row has no user_id"
            assert (row.details or {}).get("name") == f"AUDIT-{sfx}", \
                f"release.created details missing name: {row.details}"
        results.append(test("1. create_release writes release.created", t_created))

        # 2. minting a status token logs the grant but NEVER the raw token.
        raw_token = {"v": None}

        def t_mint():
            mr = client.post(f"/api/releases/{rid}/status-token", headers=auth(admin))
            assert mr.status_code == 201, f"mint failed: {mr.status_code} {mr.data[:200]}"
            raw_token["v"] = mr.get_json()["token"]
            rows = _audit_rows(rid, "release.status_token.minted")
            assert len(rows) >= 1, "no release.status_token.minted audit row"
            # The raw token must not appear anywhere in the audit row.
            blob = str(rows[-1].details) + str(rows[-1].action)
            assert raw_token["v"] not in blob, "raw token leaked into activity_log!"
        results.append(test("2. status-token mint audited, raw token NOT logged", t_mint))

        # 3. revoking the token logs the revoke.
        def t_revoke():
            dr = client.delete(f"/api/releases/{rid}/status-token", headers=auth(admin))
            assert dr.status_code == 200, f"revoke failed: {dr.status_code}"
            rows = _audit_rows(rid, "release.status_token.revoked")
            assert len(rows) >= 1, "no release.status_token.revoked audit row"
        results.append(test("3. status-token revoke audited", t_revoke))
    finally:
        _cleanup(rid)

    passed = sum(1 for x in results if x)
    total = len(results)
    print("\n" + "=" * 50)
    print(f"  {passed}/{total} passed")
    print("=" * 50 + "\n")
    return passed == total


if __name__ == "__main__":
    sys.exit(0 if run_tests() else 1)
