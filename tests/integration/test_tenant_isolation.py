"""Multi-tenant isolation fixes — integration tests against Railway.

Covers the cross-tenant gaps closed in the multi-tenant-readiness arc:

  1. finalize_decision must match the URL release_id  (cross-tenant WRITE fix)
  2. public /status requires a per-release token        (UNAUTH cross-tenant read)
  3. update_status honours tenant_id                    (defense-in-depth)
  4. add_requirement rejects a foreign-tenant requirement (adversarial-review fix)
  5. is_environment_accessible is tenant-scoped         (D-245 env-scope axis)

Tests 1-3 prove cross-tenant behaviour by passing a *mismatched* tenant_id
(fixtures live in tenant 1, cleaned up in a finally). Tests 4-5 need a REAL second
tenant, so they stand up a throwaway tenant + fixture and tear it down. A full
2-tenant E2E across all paths lands with the provisioning plan.

D-245: cross-tenant *environment* access scoping moved off the deleted
``check_environment_policy`` onto the role-ladder + Groups model
(``EnvironmentRepository.is_environment_accessible`` → ``list_environments``,
whose ``Environment.tenant_id == tenant_id`` filter is the load-bearing guarantee).
The unit test ``tests/unit/test_authz_env_scope.py`` covers the membership /
coercion logic with ``list_environments`` *stubbed*, so it does NOT exercise that
tenant filter; test 5 below does, against the real DB with a foreign-tenant env.

Run: python tests/test_tenant_isolation.py
"""

import os
import sys
import uuid

# wave-0 TEST-2: moved under tests/integration/ so pytest collects it (testpaths).
# One extra dirname keeps the repo root on sys.path for `python tests/integration/…` too.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv
load_dotenv()

from primeqa.app import app
from primeqa.db import SessionLocal
from primeqa.core.models import Connection, User
from primeqa.core.repository import ConnectionRepository
from primeqa.release.repository import ReleaseRepository
from primeqa.release.routes import _hash_poll_token

TENANT_ID = 1
client = app.test_client()
results = []


def _run(name, fn):
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
def test_finalize_matches_release():
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
# 2. public /status requires a per-release token (the unauth leak fix)
# --------------------------------------------------------------------------
def test_status_requires_token():
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
# 3. update_status honours tenant_id (defense-in-depth)
# --------------------------------------------------------------------------
def test_update_status_tenant_scoped():
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


# --------------------------------------------------------------------------
# 4. add_requirement rejects a foreign-tenant requirement (adversarial-review
#    finding). This is the one isolation test that needs a REAL 2nd tenant —
#    the others use a mismatched tenant_id, but add_requirement uses one
#    tenant_id for both the release and the requirement, so a genuinely foreign
#    requirement (release in tenant 1, requirement in tenant 2) is required to
#    exercise the new guard.
# --------------------------------------------------------------------------
def test_add_requirement_tenant_scoped():
    from primeqa.release.service import ReleaseService
    from primeqa.release.models import ReleaseRequirement
    from primeqa.test_management.models import Requirement, Section
    from primeqa.core.models import Tenant
    sfx = uuid.uuid4().hex[:8]
    db = SessionLocal()
    uid = _a_user_id(db)

    # Foreign-tenant fixtures (a throwaway tenant 2 + section + requirement).
    t2 = Tenant(name=f"ISO Tenant {sfx}", slug=f"iso-{sfx}")
    db.add(t2); db.commit(); db.refresh(t2)
    sec2 = Section(tenant_id=t2.id, name=f"ISO Sec {sfx}", position=0, created_by=uid)
    db.add(sec2); db.commit(); db.refresh(sec2)
    foreign_req = Requirement(tenant_id=t2.id, section_id=sec2.id, source="manual",
                              created_by=uid, jira_key=f"FOR-{sfx}",
                              jira_summary="foreign tenant secret")
    db.add(foreign_req); db.commit(); db.refresh(foreign_req)

    repo = ReleaseRepository(db)
    rel = repo.create_release(TENANT_ID, f"ISO-REQ-{sfx}", uid)
    svc = ReleaseService(repo)
    own_req = db.query(Requirement).filter(
        Requirement.tenant_id == TENANT_ID,
        Requirement.deleted_at.is_(None)).first()
    try:
        # NEGATIVE: a foreign-tenant requirement must be rejected, no row created.
        rejected = False
        try:
            svc.add_requirement(rel.id, TENANT_ID, foreign_req.id, uid)
        except ValueError as e:
            rejected = "not found" in str(e).lower()
        assert rejected, "cross-tenant requirement was NOT rejected by add_requirement"
        leaked = db.query(ReleaseRequirement).filter(
            ReleaseRequirement.release_id == rel.id,
            ReleaseRequirement.requirement_id == foreign_req.id).first()
        assert leaked is None, "a cross-tenant release_requirements row was created"

        # POSITIVE: a same-tenant requirement still links (no happy-path regression).
        if own_req is not None:
            svc.add_requirement(rel.id, TENANT_ID, own_req.id, uid)
            linked = db.query(ReleaseRequirement).filter(
                ReleaseRequirement.release_id == rel.id,
                ReleaseRequirement.requirement_id == own_req.id).first()
            assert linked is not None, "same-tenant requirement failed to link"
    finally:
        try:
            db.query(ReleaseRequirement).filter(
                ReleaseRequirement.release_id == rel.id).delete()
            db.delete(rel)
            db.delete(foreign_req)
            db.delete(sec2)
            db.delete(t2)
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


# --------------------------------------------------------------------------
# 5. is_environment_accessible is tenant-scoped (the D-245 env-scope axis that
#    replaced check_environment_policy). Re-homes the cross-tenant env probe the
#    old test #2 covered — exercising the REAL list_environments tenant filter
#    (the unit test stubs list_environments, so it cannot catch a tenant-filter
#    regression). Stands up a throwaway tenant + env and asserts a tenant-1 caller
#    cannot reach it.
# --------------------------------------------------------------------------
def test_env_access_tenant_scoped():
    from primeqa.core.models import Tenant, Environment
    from primeqa.core.repository import EnvironmentRepository
    sfx = uuid.uuid4().hex[:8]
    db = SessionLocal()
    uid = _a_user_id(db)
    # An admin caller in tenant 1 — admins take the "see all" branch, so the
    # ONLY thing scoping them is the tenant filter (the load-bearing guarantee).
    admin = db.query(User).filter(User.tenant_id == TENANT_ID,
                                  User.role.in_(("admin", "superadmin")),
                                  User.is_active == True).first()
    t2 = Tenant(name=f"ISO Env Tenant {sfx}", slug=f"isoenv-{sfx}")
    db.add(t2); db.commit(); db.refresh(t2)
    foreign_env = Environment(tenant_id=t2.id, name=f"ISO Env {sfx}",
                              env_type="sandbox", sf_instance_url="https://example.invalid",
                              sf_api_version="60.0", created_by=uid)
    db.add(foreign_env); db.commit(); db.refresh(foreign_env)
    repo = EnvironmentRepository(db)
    own_env = (db.query(Environment)
               .filter(Environment.tenant_id == TENANT_ID,
                       Environment.is_active == True).first())
    try:
        # NEGATIVE: a tenant-1 caller (even admin = "see all") must NOT reach a
        # tenant-2 env — the tenant filter, not group membership, is the guard.
        assert admin is not None, "seed data: expected an admin/superadmin in tenant 1"
        reachable = repo.is_environment_accessible(
            TENANT_ID, admin.id, admin.role, foreign_env.id)
        assert reachable is False, \
            "cross-tenant env was accessible — tenant filter on list_environments broke"
        # POSITIVE: a same-tenant env IS reachable (no happy-path regression).
        if own_env is not None:
            assert repo.is_environment_accessible(
                TENANT_ID, admin.id, admin.role, own_env.id) is True, \
                "own-tenant env not accessible to an admin"
    finally:
        try:
            db.delete(foreign_env)
            db.delete(t2)
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


# --------------------------------------------------------------------------
# 6. add_member / add_environment tenant-scope the client-supplied id (SEC-2).
#    The group is tenant-checked, but the linked user_id / environment_id was
#    written straight through; group_members / group_environments carry no tenant
#    column, so a foreign id would link in and leak the user's PII / the env's
#    sf_instance_url via get_group_detail. Mirrors test #4 (add_requirement).
# --------------------------------------------------------------------------
def test_add_member_environment_tenant_scoped():
    from primeqa.core.models import (
        Tenant, User, Environment, GroupMember, GroupEnvironment,
    )
    from primeqa.core.repository import GroupRepository
    from primeqa.core.service import GroupService
    sfx = uuid.uuid4().hex[:8]
    db = SessionLocal()
    uid = _a_user_id(db)
    grepo = GroupRepository(db)
    svc = GroupService(grepo)
    group = grepo.create_group(TENANT_ID, f"ISO Group {sfx}", uid)

    # Foreign-tenant fixtures (throwaway tenant 2 + its user + env).
    t2 = Tenant(name=f"ISO Grp Tenant {sfx}", slug=f"isogrp-{sfx}")
    db.add(t2); db.commit(); db.refresh(t2)
    foreign_user = User(tenant_id=t2.id, email=f"iso-{sfx}@example.invalid",
                        password_hash="x", full_name="Foreign User", role="viewer")
    db.add(foreign_user); db.commit(); db.refresh(foreign_user)
    foreign_env = Environment(tenant_id=t2.id, name=f"ISO GrpEnv {sfx}",
                              env_type="sandbox", sf_instance_url="https://example.invalid",
                              sf_api_version="60.0", created_by=uid)
    db.add(foreign_env); db.commit(); db.refresh(foreign_env)
    own_user = db.query(User).filter(User.tenant_id == TENANT_ID).first()
    own_env = db.query(Environment).filter(Environment.tenant_id == TENANT_ID).first()
    try:
        # NEGATIVE: a foreign-tenant user_id must be rejected, no member row.
        rejected = False
        try:
            svc.add_member(group.id, TENANT_ID, foreign_user.id, uid)
        except ValueError as e:
            rejected = "not found" in str(e).lower()
        assert rejected, "cross-tenant user_id was NOT rejected by add_member"
        assert db.query(GroupMember).filter(
            GroupMember.group_id == group.id,
            GroupMember.user_id == foreign_user.id).first() is None, \
            "a cross-tenant group_members row was created"

        # NEGATIVE: a foreign-tenant environment_id must be rejected.
        rejected_env = False
        try:
            svc.add_environment(group.id, TENANT_ID, foreign_env.id, uid)
        except ValueError as e:
            rejected_env = "not found" in str(e).lower()
        assert rejected_env, "cross-tenant environment_id was NOT rejected by add_environment"
        assert db.query(GroupEnvironment).filter(
            GroupEnvironment.group_id == group.id,
            GroupEnvironment.environment_id == foreign_env.id).first() is None, \
            "a cross-tenant group_environments row was created"

        # POSITIVE: same-tenant links still work (no happy-path regression).
        if own_user is not None:
            svc.add_member(group.id, TENANT_ID, own_user.id, uid)
            assert db.query(GroupMember).filter(
                GroupMember.group_id == group.id,
                GroupMember.user_id == own_user.id).first() is not None, \
                "same-tenant add_member failed to link"
        if own_env is not None:
            svc.add_environment(group.id, TENANT_ID, own_env.id, uid)
            assert db.query(GroupEnvironment).filter(
                GroupEnvironment.group_id == group.id,
                GroupEnvironment.environment_id == own_env.id).first() is not None, \
                "same-tenant add_environment failed to link"
    finally:
        try:
            db.query(GroupMember).filter(GroupMember.group_id == group.id).delete()
            db.query(GroupEnvironment).filter(GroupEnvironment.group_id == group.id).delete()
            db.delete(group)
            db.delete(foreign_env)
            db.delete(foreign_user)
            db.delete(t2)
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


if __name__ == "__main__":
    print("\n=== Multi-tenant isolation tests ===\n")
    results.append(_run("1. finalize_decision must match the URL release_id",
                        test_finalize_matches_release))
    results.append(_run("2. public /status requires a per-release token",
                        test_status_requires_token))
    results.append(_run("3. update_status honours tenant_id",
                        test_update_status_tenant_scoped))
    results.append(_run("4. add_requirement rejects a foreign-tenant requirement",
                        test_add_requirement_tenant_scoped))
    results.append(_run("5. is_environment_accessible is tenant-scoped",
                        test_env_access_tenant_scoped))
    results.append(_run("6. add_member/add_environment tenant-scope the linked id",
                        test_add_member_environment_tenant_scoped))

    passed = sum(1 for r in results if r)
    total = len(results)
    print("\n" + "=" * 60)
    print(f"  {passed}/{total} passed")
    print("=" * 60 + "\n")
    sys.exit(0 if passed == total else 1)
