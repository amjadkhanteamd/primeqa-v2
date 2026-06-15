-- 056_drop_dead_test_data_tables.sql
-- Remove the orphaned v1 "Test Data Engine" tables (created by migration 010).
--
-- The Settings -> Test Data feature (Templates + Factories) was v1 code that no
-- live consumer ever read: the only runtime reader would have been
-- DataEngineService.resolve_references() — never called anywhere in the repo —
-- and the substrate execution engine (primeqa/execution_engine/) has ZERO
-- references to these models/tables. Run-time test-data injection shipped as a
-- DIFFERENT mechanism (D-235 field-overrides, wired through execution_engine/
-- run.py + data_executor.py); the template/factory concept was explicitly
-- deferred (DECISIONS_LOG D-235 / D-106 F6).
--
-- The dead UI, routes, module (primeqa/execution/data_engine.py) and nav entry
-- were removed in code first (this migration's sibling commit). These four
-- tables are the last orphaned remnant: v1 product tables dropped in 053, but
-- these were left behind, and test_case_data_bindings still carries a (now-dead)
-- FK to test_case_versions.
--
-- Safe: grep of all migrations confirms NO other table has an incoming FK to any
-- of these four — they hold only OUTGOING FKs to live tables (tenants / users /
-- environments / test_case_versions), so dropping them cannot cascade into live
-- data. No live feature is affected (D-235 field-overrides is untouched).
--
-- IRREVERSIBLE; apply only after verifying zero rows. Idempotent (IF EXISTS),
-- per the migrations/ 016+ convention.

BEGIN;

DROP TABLE IF EXISTS test_case_data_bindings CASCADE;
DROP TABLE IF EXISTS data_snapshots CASCADE;
DROP TABLE IF EXISTS data_factories CASCADE;
DROP TABLE IF EXISTS data_templates CASCADE;

COMMIT;
