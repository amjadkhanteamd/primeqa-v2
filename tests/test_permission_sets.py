"""Integration tests for the Permission Set data model (migration 039).

Covers:
  1. Migration is idempotent (no-op on second apply).
  2. All 5 base Permission Sets seeded per tenant.
  3. Granular Permission Sets seeded (one per unique permission string).
  4. get_effective_permissions returns the union across assigned sets.
  5. get_effective_permissions returns empty set for unassigned users.
  6. Layered base + granular assignments produce correct union.
  7. Existing users are mapped to the correct default base set by legacy role.
  8. Environment run-policy columns + pipeline_runs release_status exist.
  9. release_status CHECK constraint rejects invalid values.
 10. seed_permission_sets_for_tenant is idempotent when called twice.

Style matches tests/test_auth.py: hits the real Railway database through
the in-process Flask client or a direct SQLAlchemy session.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text

from primeqa.app import app  # noqa: F401 — ensures model registration
from primeqa.db import SessionLocal
from primeqa.core.permissions import (
    BASE_PERMISSION_SETS,
    GRANULAR_PERMISSION_META,
    PermissionSet,
    UserPermissionSet,
    all_known_permissions,
    assign_default_permission_set,
    assign_permission_set,
    default_permission_set_for_role,
    get_effective_permissions,
    revoke_permission_set,
    seed_permission_sets_for_tenant,
)
from primeqa.core.models import User

TENANT_ID = 1
MIGRATION_PATH = "migrations/039_permission_sets_and_ownership.sql"


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


def _psql_run(sql: str) -> tuple[str, str, int]:
    """Run a SQL string via psql against DATABASE_URL. Returns (stdout, stderr, rc)."""
    url = os.environ["DATABASE_URL"]
    p = subprocess.run(
        ["psql", url, "-c", sql],
        capture_output=True, text=True,
    )
    return p.stdout, p.stderr, p.returncode


def _psql_file(path: str) -> tuple[str, str, int]:
    url = os.environ["DATABASE_URL"]
    p = subprocess.run(
        ["psql", url, "-f", path],
        capture_output=True, text=True,
    )
    return p.stdout, p.stderr, p.returncode


def run_tests():
    results = []
    print("\n=== Permission Set Data Model Tests (migration 039) ===\n")

    db = SessionLocal()

    # ---------------------------------------------------------------
    # 1. Idempotency — applying the migration twice in a row is a no-op
    #    on the second apply. We run it once to catch up on any state
    #    that upstream tests may have left (e.g. a new user without a
    #    permission-set assignment), then run it again and assert every
    #    INSERT line reads "INSERT 0 0".
    # ---------------------------------------------------------------
    def test_migration_idempotent():
        # First apply catches the DB up to the migration's desired state.
        _psql_file(MIGRATION_PATH)
        # Second apply is the true idempotency assertion.
        stdout, stderr, rc = _psql_file(MIGRATION_PATH)
        assert rc == 0, f"Re-run of migration failed: rc={rc}\n{stderr}"
        lines = [ln.strip() for ln in stdout.splitlines() if ln.startswith("INSERT ")]
        assert lines, "Expected INSERT lines in psql output"
        for ln in lines:
            assert ln.endswith(" 0"), f"Non-idempotent INSERT line: {ln!r}"
    results.append(test("1. Migration is idempotent — two consecutive runs insert zero rows",
                        test_migration_idempotent))

    # ---------------------------------------------------------------
    # 2. All five base sets are seeded for tenant 1.
    # ---------------------------------------------------------------
    def test_five_base_sets_seeded():
        rows = (db.query(PermissionSet)
                .filter_by(tenant_id=TENANT_ID, is_base=True)
                .all())
        api_names = {r.api_name for r in rows}
        expected = {"developer_base", "tester_base", "release_owner_base",
                    "admin_base", "api_access"}
        assert api_names == expected, f"Base sets mismatch: got {api_names}, expected {expected}"
        # Every base set has is_system = true
        for r in rows:
            assert r.is_system, f"Base set {r.api_name} missing is_system"
            assert isinstance(r.permissions, list) and r.permissions, \
                f"Base set {r.api_name} has empty permissions list"
    results.append(test("2. All 5 base Permission Sets seeded for tenant",
                        test_five_base_sets_seeded))

    # ---------------------------------------------------------------
    # 3. Admin Base permission count matches the BASE_PERMISSION_SETS spec.
    # ---------------------------------------------------------------
    def test_admin_base_permissions_match_spec():
        spec = next(s for s in BASE_PERMISSION_SETS if s["api_name"] == "admin_base")
        row = (db.query(PermissionSet)
               .filter_by(tenant_id=TENANT_ID, api_name="admin_base")
               .first())
        assert row is not None, "admin_base missing"
        assert set(row.permissions) == set(spec["permissions"]), \
            f"admin_base permissions drift: row={sorted(row.permissions)}, spec={sorted(spec['permissions'])}"
    results.append(test("3. admin_base permissions match BASE_PERMISSION_SETS spec",
                        test_admin_base_permissions_match_spec))

    # ---------------------------------------------------------------
    # 4. Granular sets exist — one per unique permission string.
    # ---------------------------------------------------------------
    def test_granular_sets_seeded():
        granular = (db.query(PermissionSet)
                    .filter_by(tenant_id=TENANT_ID, is_base=False, is_system=True)
                    .all())
        api_names = {g.api_name for g in granular}
        expected = set(GRANULAR_PERMISSION_META.keys())
        assert api_names == expected, \
            f"Granular set mismatch: missing={expected - api_names}, extra={api_names - expected}"
        # Each granular set contains exactly its own permission string.
        for g in granular:
            assert g.permissions == [g.api_name], \
                f"Granular {g.api_name} permissions should be [{g.api_name}], got {g.permissions}"
    results.append(test("4. Granular Permission Sets seeded — one per permission string",
                        test_granular_sets_seeded))

    # ---------------------------------------------------------------
    # 5. all_known_permissions covers every permission referenced anywhere.
    # ---------------------------------------------------------------
    def test_all_known_permissions_covers_every_base_permission():
        known = all_known_permissions()
        for base in BASE_PERMISSION_SETS:
            for p in base["permissions"]:
                assert p in known, f"Permission {p!r} in {base['api_name']} missing from all_known_permissions"
    results.append(test("5. all_known_permissions covers every base-set permission",
                        test_all_known_permissions_covers_every_base_permission))

    # ---------------------------------------------------------------
    # 6. get_effective_permissions for a user with just the base Admin set.
    # ---------------------------------------------------------------
    def test_effective_permissions_base_only():
        # Use the seeded admin. Make sure they have exactly admin_base.
        admin = (db.query(User)
                 .filter_by(tenant_id=TENANT_ID, email="admin@primeqa.io")
                 .first())
        assert admin is not None, "admin@primeqa.io not found in tenant 1"
        admin_base = (db.query(PermissionSet)
                      .filter_by(tenant_id=TENANT_ID, api_name="admin_base")
                      .first())
        assert admin_base is not None

        # Clear assignments + re-grant exactly admin_base for a predictable state.
        (db.query(UserPermissionSet)
            .filter_by(user_id=admin.id).delete())
        assign_permission_set(admin.id, admin_base.id, db)
        db.commit()

        perms = get_effective_permissions(admin.id, db)
        assert perms == set(admin_base.permissions), \
            f"Effective perms {perms} != admin_base perms {set(admin_base.permissions)}"
    results.append(test("6. get_effective_permissions returns base-set perms",
                        test_effective_permissions_base_only))

    # ---------------------------------------------------------------
    # 7. get_effective_permissions with base + an extra granular set —
    #    union should match base ∪ granular.
    # ---------------------------------------------------------------
    def test_effective_permissions_base_plus_granular():
        admin = db.query(User).filter_by(tenant_id=TENANT_ID,
                                         email="admin@primeqa.io").first()
        # Grant api_authenticate (granular) on top of admin_base.
        granular = (db.query(PermissionSet)
                    .filter_by(tenant_id=TENANT_ID, api_name="api_authenticate")
                    .first())
        assert granular is not None
        assign_permission_set(admin.id, granular.id, db)
        db.commit()

        perms = get_effective_permissions(admin.id, db)
        assert "api_authenticate" in perms, "Granted api_authenticate not in effective perms"
        # Admin base perms all still present
        admin_base = (db.query(PermissionSet)
                      .filter_by(tenant_id=TENANT_ID, api_name="admin_base")
                      .first())
        for p in admin_base.permissions:
            assert p in perms, f"admin_base perm {p} missing after adding granular"

        # Revoke returns True first time, False on repeat (idempotent)
        assert revoke_permission_set(admin.id, granular.id, db) is True
        assert revoke_permission_set(admin.id, granular.id, db) is False
        db.commit()
    results.append(test("7. get_effective_permissions unions base + granular",
                        test_effective_permissions_base_plus_granular))

    # ---------------------------------------------------------------
    # 8. Empty set for a user with no assignments.
    # ---------------------------------------------------------------
    def test_effective_permissions_empty():
        # Create a throwaway user with no assignments. Delete any leftover first.
        db.execute(text("DELETE FROM users WHERE email = :e"),
                   {"e": "perms_test_empty@primeqa.io"})
        db.commit()
        u = User(
            tenant_id=TENANT_ID,
            email="perms_test_empty@primeqa.io",
            password_hash="x" * 60,
            full_name="Perms Empty",
            role="tester",
            is_active=True,
        )
        db.add(u); db.flush()
        # Ensure no assignment exists
        (db.query(UserPermissionSet).filter_by(user_id=u.id).delete())
        db.commit()

        try:
            perms = get_effective_permissions(u.id, db)
            assert perms == set(), f"Unassigned user should have empty perms, got {perms}"
        finally:
            db.execute(text("DELETE FROM users WHERE id = :id"), {"id": u.id})
            db.commit()
    results.append(test("8. get_effective_permissions returns empty set when unassigned",
                        test_effective_permissions_empty))

    # ---------------------------------------------------------------
    # 9. Role -> default base-set mapping is correct for every role
    #    the system understands.
    #
    # (Historical note: this test used to check tenant-wide state, but
    # the admin UI / enforcement / dynamic-UI suites now reassign
    # permission sets for their fixture users — so tenant-wide
    # snapshots drift across test runs. We now assert the resolver's
    # role-mapping table directly, which is what migration 039 installed
    # and what assign_default_permission_set honours.)
    # ---------------------------------------------------------------
    def test_role_to_default_set_mapping():
        cases = [
            ("admin",      "admin_base"),
            ("superadmin", "admin_base"),
            ("ba",         "tester_base"),
            ("viewer",     "release_owner_base"),
            ("tester",     "developer_base"),
            (None,         "developer_base"),      # no role -> safe default
            ("unknown",    "developer_base"),      # unknown role -> same
        ]
        for role, expected in cases:
            actual = default_permission_set_for_role(role)
            assert actual == expected, \
                f"default_permission_set_for_role({role!r}) = {actual!r}, expected {expected!r}"

        # End-to-end: create a throwaway user with role=tester,
        # assign_default_permission_set gives them developer_base.
        db.execute(text("DELETE FROM users WHERE email = :e"),
                   {"e": "perms_role_map@primeqa.io"})
        db.commit()
        u = User(
            tenant_id=TENANT_ID,
            email="perms_role_map@primeqa.io",
            password_hash="x" * 60,
            full_name="Role Map",
            role="tester",
            is_active=True,
        )
        db.add(u); db.flush()
        try:
            from primeqa.core.permissions import (
                assign_default_permission_set, list_user_permission_sets,
            )
            assign_default_permission_set(u.id, TENANT_ID, u.role, db)
            db.commit()
            sets = list_user_permission_sets(u.id, db)
            api_names = {p.api_name for p in sets}
            assert "developer_base" in api_names, \
                f"tester role should get developer_base, got {api_names}"
        finally:
            db.execute(text("DELETE FROM user_permission_sets WHERE user_id = :id"),
                       {"id": u.id})
            db.execute(text("DELETE FROM users WHERE id = :id"), {"id": u.id})
            db.commit()
    results.append(test("9. Role -> default base-set mapping is correct",
                        test_role_to_default_set_mapping))

    # ---------------------------------------------------------------
    # 10. seed_permission_sets_for_tenant is itself idempotent — second
    #    call returns 0 new rows even on a fully-seeded tenant.
    # ---------------------------------------------------------------
    def test_seed_function_idempotent():
        inserted = seed_permission_sets_for_tenant(TENANT_ID, db)
        db.commit()
        assert inserted == 0, f"Expected 0 new rows on re-seed, got {inserted}"
    results.append(test("10. seed_permission_sets_for_tenant is idempotent",
                        test_seed_function_idempotent))

    # ---------------------------------------------------------------
    # 11. Environment run-policy columns exist with correct defaults.
    # ---------------------------------------------------------------
    def test_environment_policy_columns_exist():
        row = db.execute(text("""
            SELECT column_name, column_default
            FROM information_schema.columns
            WHERE table_name = 'environments'
              AND column_name IN (
                'allow_single_run', 'allow_bulk_run', 'allow_scheduled_run',
                'is_production', 'require_approval', 'max_api_calls_per_run',
                'environment_type', 'owner_user_id', 'parent_team_env_id'
              )
        """)).fetchall()
        cols = {r[0] for r in row}
        expected = {
            'allow_single_run', 'allow_bulk_run', 'allow_scheduled_run',
            'is_production', 'require_approval', 'max_api_calls_per_run',
            'environment_type', 'owner_user_id', 'parent_team_env_id',
        }
        assert cols == expected, f"Missing env cols: {expected - cols}"
    results.append(test("11. Environment run-policy + ownership columns exist",
                        test_environment_policy_columns_exist))

    # ---------------------------------------------------------------
    # 12. pipeline_runs release_status column + CHECK constraint work.
    # ---------------------------------------------------------------
    def test_pipeline_runs_release_status_check():
        # Find a real pipeline_run id (migration backfill doesn't set release_status,
        # so every row starts NULL). We'll probe the CHECK by trying to UPDATE.
        row = db.execute(text("""
            SELECT id FROM pipeline_runs
             WHERE tenant_id = :t
             ORDER BY id DESC LIMIT 1
        """), {"t": TENANT_ID}).fetchone()
        if row is None:
            # No runs in this tenant — skip but don't fail.
            return
        run_id = row[0]

        # Valid values accepted
        for val in ('PENDING', 'APPROVED', 'OVERRIDDEN', None):
            db.execute(text("UPDATE pipeline_runs SET release_status = :v WHERE id = :id"),
                       {"v": val, "id": run_id})
        db.commit()

        # Invalid value rejected by CHECK
        try:
            db.execute(text("UPDATE pipeline_runs SET release_status = 'WIBBLE' WHERE id = :id"),
                       {"id": run_id})
            db.commit()
            raise AssertionError("CHECK constraint did not reject 'WIBBLE'")
        except Exception as e:
            db.rollback()
            msg = str(e).lower()
            assert "check" in msg or "constraint" in msg, \
                f"Expected CHECK violation, got: {e}"

        # Reset to NULL so the fixture doesn't leave state behind.
        db.execute(text("UPDATE pipeline_runs SET release_status = NULL WHERE id = :id"),
                   {"id": run_id})
        db.commit()
    results.append(test("12. pipeline_runs.release_status CHECK rejects invalid values",
                        test_pipeline_runs_release_status_check))

    # ---------------------------------------------------------------
    # 13. assign_default_permission_set on a fresh user grants the
    #     correct base set and is idempotent.
    # ---------------------------------------------------------------
    def test_assign_default_permission_set_helper():
        db.execute(text("DELETE FROM users WHERE email = :e"),
                   {"e": "perms_test_default@primeqa.io"})
        db.commit()
        u = User(
            tenant_id=TENANT_ID,
            email="perms_test_default@primeqa.io",
            password_hash="x" * 60,
            full_name="Perms Default",
            role="ba",
            is_active=True,
        )
        db.add(u); db.flush()

        try:
            created = assign_default_permission_set(u.id, TENANT_ID, "ba", db)
            db.commit()
            assert created is True, "First assignment should return True"

            created_again = assign_default_permission_set(u.id, TENANT_ID, "ba", db)
            db.commit()
            assert created_again is False, "Second assignment should be a no-op"

            perms = get_effective_permissions(u.id, db)
            assert "review_test_cases" in perms, "ba should inherit review_test_cases via tester_base"
        finally:
            (db.query(UserPermissionSet).filter_by(user_id=u.id).delete())
            db.execute(text("DELETE FROM users WHERE id = :id"), {"id": u.id})
            db.commit()
    results.append(test("13. assign_default_permission_set helper grants + is idempotent",
                        test_assign_default_permission_set_helper))

    # ---------------------------------------------------------------
    db.close()

    passed = sum(results)
    total = len(results)
    print(f"\n{'='*60}")
    print(f"  {passed}/{total} passed")
    print(f"{'='*60}\n")
    return passed == total


if __name__ == "__main__":
    ok = run_tests()
    sys.exit(0 if ok else 1)
