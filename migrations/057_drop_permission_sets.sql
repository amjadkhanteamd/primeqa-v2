-- 057_drop_permission_sets.sql
-- Drop the additive permission-set authorization layer (D-245).
--
-- Authorization is now the role ladder (primeqa/core/authz.py — Viewer < Member
-- < Admin < Superadmin via rank()) × environment access scope (Groups). The
-- permission-set union model — its tables, the require_permission family, the
-- resolve/assign/revoke functions, and the /settings/permission-sets UI — was
-- removed in code first (this migration's sibling commit). These two tables are
-- the last remnant.
--
-- Scope: drops ONLY the two permission-set tables created by migration 039.
-- Everything else 039 added stays — it is live and unrelated to the deleted
-- layer:
--   * environments run-policy + ownership columns (allow_*_run, is_production,
--     require_approval, owner_user_id, ...) — is_production drives the Phase-4
--     production dispatch gate.
--   * test_suites.owner_user_id, pipeline_runs release-state columns.
--   * shared_dashboard_links + notification_preferences — surviving feature
--     tables (their ORM models were kept in primeqa/core/permissions.py).
--
-- FK note: user_permission_sets.permission_set_id REFERENCES permission_sets(id)
-- ON DELETE CASCADE, so user_permission_sets is dropped first (CASCADE on the
-- second drop is belt-and-braces against any stray dependent object).
--
-- The users.role column — marked DEPRECATED by migration 039 ("use
-- permission_sets instead") — is now the CANONICAL authorization axis again.
-- We correct its comment here so the DB self-documentation matches reality.
--
-- IRREVERSIBLE. Idempotent (IF EXISTS), per the migrations/ 016+ convention.

BEGIN;

DROP TABLE IF EXISTS user_permission_sets CASCADE;
DROP TABLE IF EXISTS permission_sets CASCADE;

COMMENT ON COLUMN users.role IS
    'Canonical authorization axis (D-245): mapped to a ladder tier by '
    'primeqa.core.authz.rank() — viewer->Viewer, ba/tester->Member, '
    'admin->Admin, superadmin->Superadmin. Stored values + CHECK constraint '
    'unchanged.';

COMMIT;
