-- Migration 035: run_test_results.test_case_id → ON DELETE SET NULL.
--
-- Audit finding 2026-04-19 (deferred): hard-purging soft-deleted test
-- cases was blocked by a restrictive FK on run_test_results. Changing
-- the FK to SET NULL:
--   - preserves run history (the RunTestResult row stays — useful for
--     timeline/audit even when the TC is gone)
--   - unblocks admin purge of old soft-deleted test cases
--   - matches the existing pattern on generation_quality_signals,
--     llm_usage_log, and test_cases.generation_batch_id (all already
--     SET NULL / SET NULL)
--
-- Postgres doesn't support ALTER CONSTRAINT to change delete cascade;
-- we drop + re-add in one transaction. Idempotent.
--
-- Apply:
--   psql "$DATABASE_URL" -f migrations/035_run_test_results_tc_set_null.sql

BEGIN;

-- Make the column nullable so SET NULL has somewhere to land when a TC
-- is hard-deleted. Historical run_test_results keep their timeline value
-- but lose their TC backlink — that's acceptable because the rest of
-- the run context (run_id, status, timing, failure_summary) is intact.
ALTER TABLE run_test_results
    ALTER COLUMN test_case_id DROP NOT NULL;

ALTER TABLE run_test_results
    DROP CONSTRAINT IF EXISTS run_test_results_test_case_id_fkey;

ALTER TABLE run_test_results
    ADD CONSTRAINT run_test_results_test_case_id_fkey
    FOREIGN KEY (test_case_id) REFERENCES test_cases(id) ON DELETE SET NULL;

-- Same for test_case_version_id — cascading TC delete also cascades
-- to test_case_versions; SET NULL so historical results survive.
ALTER TABLE run_test_results
    ALTER COLUMN test_case_version_id DROP NOT NULL;

ALTER TABLE run_test_results
    DROP CONSTRAINT IF EXISTS run_test_results_test_case_version_id_fkey;

ALTER TABLE run_test_results
    ADD CONSTRAINT run_test_results_test_case_version_id_fkey
    FOREIGN KEY (test_case_version_id) REFERENCES test_case_versions(id) ON DELETE SET NULL;

-- `test_cases.current_version_id` self-references the version table;
-- leaving it NO ACTION because the delete chain starts at the TC (so
-- current_version_id is cleared via the TC row going away, not via
-- the version table). Nothing to change here.

COMMIT;
