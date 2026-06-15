"""Multi-tenant isolation fixes — integration tests against Railway.

Covers the cross-tenant gaps closed in the multi-tenant-readiness arc:

  1. finalize_decision must match the URL release_id  (cross-tenant WRITE fix)
  2. check_environment_policy is tenant-scoped         (cross-tenant env probe)
  3. public /status requires a per-release token        (UNAUTH cross-tenant read)
  4. update_status honours tenant_id                    (defense-in-depth)

Fixtures are created in tenant 1 and cleaned up in a finally. Cross-tenant
behaviour is proven by passing a *mismatched* tenant_id rather than standing up
a second tenant — a full 2-tenant E2E lands with the provisioning plan. The
list_requirements tenant filter (A5) shares the same mismatched-tenant-id
mechanism verified here and in test 2/4.

Run: python tests/test_tenant_isolation.py
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from primeqa.app import app
from primeqa.db import SessionLocal
from primeqa.core.models import Connection, Environment, User
from primeqa.core.permissions import check_environment_policy
from primeqa.core.repository import ConnectionRepository
from primeqa.release.repository import ReleaseRepository
from primeqa.release.routes import _hash_poll_token

TENANT_ID = 1
client = app.test_client()
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


def _a_user_id(db):
    u = db.query(User).filter_by(tenant_id=TENANT_ID).first()
    assert u is not None, "seed data: expected at least one user in tenant 1"
    return u.id


# --------------------------------------------------------------------------
# 1. finalize_decision must match the URL release_id
# --------------------------------------------------------------------------
def t_finalize_matches_release():
    db = SessionLocal()
    repo = ReleaseRepository(db)
    uid = _a_user_id(db)
    sfx = uuid.uuid4().hex[:8]
    rel_a = repo.create_release(TENANT_ID, f"ISO-A-{sfx}", uid)
    rel_b = repo.create_release(TENANT_ID, f"ISO-B-{sfx}", uid)
    dec = repo.create_decision(rel_a.id, "go", recommended_by="ai")
    try:
        # A decision belonging to release A cannot be finalized through a
        # different release's URL — the repo filters on (id, release_id).
        assert repo.finalize_decision(dec.id, rel_b.id, "go", uid) is None, \
            "decision finalized through the WRONG release_id — isolation gap"
        # Same decision through its own release finalizes normally.
        d = repo.finalize_decision(dec.id, rel_a.id, "go", uid)
        assert d is not None and d.final_decision == "go", "own-release finalize failed"
    finally:
        db.delete(rel_a)   # cascade removes the decision
        db.delete(rel_b)
        db.commit()
        db.close()


# --------------------------------------------------------------------------
# 2. check_environment_policy is tenant-scoped
# --------------------------------------------------------------------------
def t_env_policy_tenant_scoped():
    db = SessionLocal()
    try:
        env = db.query(Environment).filter_by(tenant_id=TENANT_ID).first()
        assert env is not None, "seed data: expected a tenant-1 environment"
        # Correct tenant: evaluates the policy (allowed, or a policy reason —
        # never the 'not found' branch).
        _, reason = check_environment_policy(env.id, "single_run", db, TENANT_ID)
        assert "not found" not in reason.lower(), \
            f"own-tenant env reported not-found: {reason!r}"
        # Mismatched tenant: the env is invisible (no name/policy leak).
        ok2, reason2 = check_environment_policy(env.id, "single_run", db, 999_999)
        assert ok2 is False and "not found" in reason2.lower(), \
            f"cross-tenant env probe was NOT blocked: ok={ok2} reason={reason2!r}"
    finally:
        db.close()


# --------------------------------------------------------------------------
# 3. public /status requires a per-release token (the unauth leak fix)
# --------------------------------------------------------------------------
def t_status_requires_token():
    # NOTE: each DB mutation uses its own short-lived session that is closed
    # before the next client.get(). The route's `finally: db.close()` closes the
    # thread-scoped session, so holding one open across an in-process client call
    # would detach our release object — a test-harness artifact, not a product
    # bug (in production mint and poll are separate requests).
    sfx = uuid.uuid4().hex[:8]
    raw = "tok-" + sfx

    def _set_hash(value):
        db = SessionLocal()
        try:
            from primeqa.release.models import Release
            rel = db.query(Release).filter_by(id=rid).first()
            rel.status_poll_token_hash = value
            db.commit()
        finally:
            db.close()

    # Create the release in its own session.
    db = SessionLocal()
    uid = _a_user_id(db)
    rel = ReleaseRepository(db).create_release(TENANT_ID, f"ISO-STATUS-{sfx}", uid)
    rid = rel.id
    db.close()
    try:
        # No token at all -> 401 (was 200 + full cross-tenant data before).
        r = client.get(f"/api/releases/{rid}/status")
        assert r.status_code == 401, f"no-token expected 401, got {r.status_code}"

        # Mint a token (set the hash, as the mint route does).
        _set_hash(_hash_poll_token(raw))

        # Wrong token -> 404 (indistinguishable from nonexistent).
        r = client.get(f"/api/releases/{rid}/status?token=wrong-{sfx}")
        assert r.status_code == 404, f"wrong-token expected 404, got {r.status_code}"

        # Correct token -> 200, scoped to this release.
        r = client.get(f"/api/releases/{rid}/status?token={raw}")
        assert r.status_code == 200, f"correct-token expected 200, got {r.status_code}: {r.data[:200]}"
        assert (r.get_json() or {}).get("release_id") == rid

        # Revoke (clear the hash) -> the old token 404s.
        _set_hash(None)
        r = client.get(f"/api/releases/{rid}/status?token={raw}")
        assert r.status_code == 404, f"post-revoke expected 404, got {r.status_code}"
    finally:
        db = SessionLocal()
        from primeqa.release.models import Release
        rel = db.query(Release).filter_by(id=rid).first()
        if rel:
            db.delete(rel)
            db.commit()
        db.close()


# --------------------------------------------------------------------------
# 4. update_status honours tenant_id (defense-in-depth)
# --------------------------------------------------------------------------
def t_update_status_tenant_scoped():
    db = SessionLocal()
    uid = _a_user_id(db)
    sfx = uuid.uuid4().hex[:8]
    # Insert a Connection row directly (config={} bypasses encryption, which
    # this test doesn't need).
    conn = Connection(tenant_id=TENANT_ID, connection_type="jira",
                      name=f"ISO-CONN-{sfx}", config={}, status="inactive",
                      created_by=uid)
    db.add(conn)
    db.commit()
    db.refresh(conn)
    cid = conn.id
    try:
        repo = ConnectionRepository(db)
        # Mismatched tenant -> no row matched -> status unchanged.
        repo.update_status(cid, "active", tenant_id=999_999)
        db.refresh(conn)
        assert conn.status == "inactive", \
            f"cross-tenant update_status mutated the row: {conn.status!r}"
        # Correct tenant -> updates.
        repo.update_status(cid, "active", tenant_id=TENANT_ID)
        db.refresh(conn)
        assert conn.status == "active", f"own-tenant update_status failed: {conn.status!r}"
    finally:
        db.delete(conn)
        db.commit()
        db.close()


if __name__ == "__main__":
    print("\n=== Multi-tenant isolation tests ===\n")
    results.append(test("1. finalize_decision must match the URL release_id",
                        t_finalize_matches_release))
    results.append(test("2. check_environment_policy is tenant-scoped",
                        t_env_policy_tenant_scoped))
    results.append(test("3. public /status requires a per-release token",
                        t_status_requires_token))
    results.append(test("4. update_status honours tenant_id",
                        t_update_status_tenant_scoped))

    passed = sum(1 for r in results if r)
    total = len(results)
    print("\n" + "=" * 60)
    print(f"  {passed}/{total} passed")
    print("=" * 60 + "\n")
    sys.exit(0 if passed == total else 1)
