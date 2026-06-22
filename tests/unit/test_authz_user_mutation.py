"""F-1 / F-2 chokepoint — user-mutation authorization, BOTH directions.

The fix closes two flaws in the user-edit path:
  * F-1 — an admin could set any user's role to ``superadmin`` (the role *value*
    was never checked) — including self-promotion.
  * F-2 — an admin could mutate a user in *another* tenant (the lookup was by
    bare id, no tenant scope).
plus the decapitation guard (#3): an admin must not edit/demote/deactivate a
user whose tier is above its own (e.g. a superadmin).

Both the API routes (`/api/auth/users` POST + `/api/auth/users/<id>` PATCH) and
the web routes (`/users/new`, `/users/<id>/edit`, `/users/<id>/toggle-active`)
funnel through exactly two service methods — ``AuthService.create_user`` and
``AuthService.update_user`` — so the guards live at that single chokepoint, not
per-route. Proving the chokepoint proves BOTH doors at once.

Two layers, both directions:
  1. the pure predicate ``can_assign_user_role`` (full caller × role × target matrix);
  2. the service chokepoint with a fake repo that RECORDS every write — every
     DENY asserts the repo write NEVER happened (the offline equivalent of "read
     back the row, confirm UNCHANGED": a guard that raises but still wrote would
     fail here).
"""
from types import SimpleNamespace

import pytest

from primeqa.core.authz import (
    AuthorizationError,
    Tier,
    can_assign_user_role,
    rank,
)
from primeqa.core.service import AuthService

pytestmark = pytest.mark.unit

VIEWER, MEMBER, ADMIN, SUPER = Tier.VIEWER, Tier.MEMBER, Tier.ADMIN, Tier.SUPERADMIN
ALL_TIERS = [VIEWER, MEMBER, ADMIN, SUPER]
ALL_ROLES = ["viewer", "ba", "tester", "admin", "superadmin"]


# ====================================================================
# Layer 1 — the pure predicate
# ====================================================================
class TestCanAssignUserRole:
    # --- create-side (no pre-existing target): only the role ceiling applies ---
    @pytest.mark.parametrize("new_role", ["viewer", "ba", "tester", "admin"])
    def test_admin_may_assign_at_or_below_own_tier(self, new_role):
        assert can_assign_user_role(ADMIN, new_role)[0] is True

    def test_admin_may_not_assign_superadmin(self):  # F-1
        ok, reason = can_assign_user_role(ADMIN, "superadmin")
        assert ok is False and "above your own tier" in reason

    def test_superadmin_may_assign_superadmin(self):
        assert can_assign_user_role(SUPER, "superadmin")[0] is True

    @pytest.mark.parametrize("bad", ["root", "owner", "", "  ", "SUPER"])
    def test_invalid_role_string_rejected(self, bad):
        ok, reason = can_assign_user_role(ADMIN, bad)
        assert ok is False and "invalid role" in reason

    @pytest.mark.parametrize("caller_tier", ALL_TIERS)
    @pytest.mark.parametrize("new_role", ALL_ROLES)
    def test_create_matrix_is_tier_ceiling(self, caller_tier, new_role):
        # The whole create-side rule: assignable iff the new role's tier is at or
        # below the caller's own tier.
        ok, _ = can_assign_user_role(caller_tier, new_role)
        assert ok is (rank(new_role) <= caller_tier)

    # --- update-side: decapitation guard on the target's current tier ---
    def test_admin_may_not_modify_superadmin_target(self):  # #3 decapitation
        ok, reason = can_assign_user_role(ADMIN, None, SUPER)
        assert ok is False and "above your own tier" in reason

    def test_admin_may_modify_admin_target(self):
        assert can_assign_user_role(ADMIN, None, ADMIN)[0] is True

    def test_superadmin_may_modify_superadmin_target(self):
        assert can_assign_user_role(SUPER, None, SUPER)[0] is True

    def test_both_checks_must_pass(self):
        # valid (low) new role but target above caller -> deny (decapitation)
        assert can_assign_user_role(ADMIN, "viewer", SUPER)[0] is False
        # in-tier target but new role above caller -> deny (F-1)
        assert can_assign_user_role(ADMIN, "superadmin", MEMBER)[0] is False

    def test_noop_allows(self):
        assert can_assign_user_role(ADMIN, None, None)[0] is True

    @pytest.mark.parametrize("caller_tier", ALL_TIERS)
    @pytest.mark.parametrize("target_tier", ALL_TIERS)
    def test_decapitation_matrix(self, caller_tier, target_tier):
        ok, _ = can_assign_user_role(caller_tier, None, target_tier)
        assert ok is (target_tier <= caller_tier)


# ====================================================================
# Layer 2 — the service chokepoint (fake repo records every write)
# ====================================================================
def _user(uid, tenant, role, *, is_active=True, full_name="X", email="x@e.co"):
    return SimpleNamespace(
        id=uid, tenant_id=tenant, role=role, is_active=is_active,
        full_name=full_name, email=email, last_login_at=None, created_at=None,
        updated_at=None,
    )


class FakeUserRepo:
    def __init__(self, users):
        self._users = {u.id: u for u in users}
        self.update_calls = []   # records EVERY repo write (the "did the row change?" oracle)
        self.create_calls = []

    def get_user_by_id(self, user_id, tenant_id=None):
        u = self._users.get(user_id)
        if u is None:
            return None
        if tenant_id is not None and u.tenant_id != tenant_id:
            return None          # F-2: cross-tenant id is invisible
        return u

    def update_user(self, user_id, updates, tenant_id=None):
        self.update_calls.append((user_id, dict(updates), tenant_id))
        u = self.get_user_by_id(user_id, tenant_id=tenant_id)
        if u is None:
            return None
        for k, v in updates.items():
            setattr(u, k, v)
        return u

    def create_user(self, tenant_id, email, password_hash, full_name, role):
        self.create_calls.append((tenant_id, email, full_name, role))
        u = _user(999, tenant_id, role, full_name=full_name, email=email)
        self._users[999] = u
        return u

    def get_user_by_email(self, tenant_id, email):
        return None

    def count_active_users(self, tenant_id):
        return 0


class FakeTokenRepo:
    def __init__(self):
        self.revoked = []

    def revoke_all_user_tokens(self, user_id):
        self.revoked.append(user_id)


# Caller dicts (the request.user shape).
ADMIN_T1 = {"id": 1, "tenant_id": 1, "role": "admin"}
SUPER_T1 = {"id": 9, "tenant_id": 1, "role": "superadmin"}


def _svc(*users):
    repo = FakeUserRepo(list(users))
    return AuthService(repo, FakeTokenRepo()), repo


# ---------------------------------------------------------------- ALLOW
class TestUpdateAllowsLegitFlows:
    def test_admin_edits_same_tenant_member_name(self):
        svc, repo = _svc(_user(2, 1, "tester"))
        out = svc.update_user(2, ADMIN_T1, full_name="New Name")
        assert out["full_name"] == "New Name"
        assert len(repo.update_calls) == 1

    def test_admin_promotes_viewer_to_tester(self):
        svc, repo = _svc(_user(2, 1, "viewer"))
        out = svc.update_user(2, ADMIN_T1, role="tester")
        assert out["role"] == "tester" and len(repo.update_calls) == 1

    def test_admin_may_set_another_admin(self):
        # admin -> admin (target tier == caller tier) is allowed (matches create).
        svc, repo = _svc(_user(2, 1, "tester"))
        out = svc.update_user(2, ADMIN_T1, role="admin")
        assert out["role"] == "admin" and len(repo.update_calls) == 1

    def test_superadmin_may_set_superadmin(self):
        svc, repo = _svc(_user(2, 1, "admin"))
        out = svc.update_user(2, SUPER_T1, role="superadmin")
        assert out["role"] == "superadmin" and len(repo.update_calls) == 1

    def test_admin_deactivates_member_revokes_tokens(self):
        svc, repo = _svc(_user(2, 1, "tester"))
        svc.update_user(2, ADMIN_T1, is_active=False)
        assert len(repo.update_calls) == 1
        assert svc.token_repo.revoked == [2]   # deactivate revokes the row's tokens


# ---------------------------------------------------------------- DENY
# Every deny asserts the repo write NEVER happened (row unchanged).
class TestUpdateDeniesEscalationAndCrossTenant:
    def test_f1_admin_cannot_set_superadmin(self):
        svc, repo = _svc(_user(2, 1, "tester"))
        with pytest.raises(AuthorizationError):
            svc.update_user(2, ADMIN_T1, role="superadmin")
        assert repo.update_calls == []                       # no write
        assert repo._users[2].role == "tester"              # unchanged

    def test_f1_invalid_role_rejected(self):
        svc, repo = _svc(_user(2, 1, "tester"))
        with pytest.raises(ValueError):
            svc.update_user(2, ADMIN_T1, role="root")
        assert repo.update_calls == []

    def test_f1_self_promotion_blocked(self):
        # The admin targets their OWN id (1) -> superadmin.
        svc, repo = _svc(_user(1, 1, "admin"))
        with pytest.raises(AuthorizationError):
            svc.update_user(1, ADMIN_T1, role="superadmin")
        assert repo.update_calls == []
        assert repo._users[1].role == "admin"

    def test_f2_cross_tenant_target_is_not_found(self):
        # Admin in tenant 1 targets a user that lives in tenant 2.
        svc, repo = _svc(_user(2, 2, "tester"))
        with pytest.raises(ValueError):
            svc.update_user(2, ADMIN_T1, full_name="Hacked")
        assert repo.update_calls == []
        assert repo._users[2].full_name == "X"              # unchanged

    def test_decapitation_admin_cannot_edit_superadmin(self):
        # Same tenant, but the target outranks the caller.
        svc, repo = _svc(_user(3, 1, "superadmin"))
        with pytest.raises(AuthorizationError):
            svc.update_user(3, ADMIN_T1, full_name="Demoted")
        assert repo.update_calls == []
        assert repo._users[3].full_name == "X"

    def test_decapitation_admin_cannot_demote_superadmin(self):
        svc, repo = _svc(_user(3, 1, "superadmin"))
        with pytest.raises(AuthorizationError):
            svc.update_user(3, ADMIN_T1, role="viewer")
        assert repo.update_calls == []
        assert repo._users[3].role == "superadmin"

    def test_caller_without_tenant_fails_closed(self):
        svc, repo = _svc(_user(2, 1, "tester"))
        with pytest.raises(AuthorizationError):
            svc.update_user(2, {"id": 1, "role": "admin"}, full_name="X")  # no tenant_id
        assert repo.update_calls == []


# ---------------------------------------------------------------- CREATE both directions
class TestCreateChokepoint:
    def test_admin_creates_admin_ok(self):
        svc, repo = _svc()
        svc.create_user(1, "a@e.co", "pw", "A", "admin", caller=ADMIN_T1)
        assert len(repo.create_calls) == 1

    def test_admin_cannot_create_superadmin(self):  # F-1 create-twin (web /users/new)
        svc, repo = _svc()
        with pytest.raises(AuthorizationError):
            svc.create_user(1, "a@e.co", "pw", "A", "superadmin", caller=ADMIN_T1)
        assert repo.create_calls == []                       # no row created

    def test_invalid_role_on_create_rejected(self):
        svc, repo = _svc()
        with pytest.raises(ValueError):
            svc.create_user(1, "a@e.co", "pw", "A", "root", caller=ADMIN_T1)
        assert repo.create_calls == []

    def test_superadmin_creates_superadmin_ok(self):
        svc, repo = _svc()
        svc.create_user(1, "a@e.co", "pw", "A", "superadmin", caller=SUPER_T1)
        assert len(repo.create_calls) == 1
