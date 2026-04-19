-- Migration 037: expand run_test_results.failure_type CHECK constraint.
--
-- Discovered during audit follow-up (2026-04-19): the worker code in
-- primeqa/worker.py has been setting `failure_type = 'step_error'` and
-- `'unexpected_error'` since forever, but the CHECK constraint only
-- allowed `validation_rule / metadata_mismatch / system_error /
-- assertion_mismatch / dependency_failure`. Every worker write of
-- failure_type silently failed the CHECK and left the column NULL —
-- the update_result call rolled back, which explains some of the
-- ghost-rtr cases where status stayed 'passed'.
--
-- Expand the allowed set to include the actual enum the code writes.
-- Idempotent.

BEGIN;

ALTER TABLE run_test_results
    DROP CONSTRAINT IF EXISTS run_test_results_failure_type_check;

ALTER TABLE run_test_results
    ADD CONSTRAINT run_test_results_failure_type_check
    CHECK (
        failure_type IS NULL
        OR failure_type IN (
            -- Legacy values (retained for back-compat):
            'validation_rule', 'metadata_mismatch', 'system_error',
            'assertion_mismatch', 'dependency_failure',
            -- Values the worker has been trying to set since forever:
            'step_error', 'unexpected_error',
            -- Audit additions:
            'validation_blocked'
        )
    );

COMMIT;
