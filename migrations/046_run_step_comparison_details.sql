-- Migration 046: run_step_results.comparison_details.
--
-- Verify-step mismatches currently land in api_response.body
-- .assertion_failures as a flat list of strings. That's hard for the
-- UI + diagnosis engine to parse programmatically. This column stores
-- structured data:
--   {"mismatches": [{"field": "IsEscalated",
--                    "expected": false, "actual": true}, ...]}
-- so /runs/:id can render per-field expected/actual rows and
-- Copy Diagnosis can produce a clean list.
--
-- Nullable — only set when a verify step has at least one mismatch.
-- Idempotent.

BEGIN;

ALTER TABLE run_step_results
    ADD COLUMN IF NOT EXISTS comparison_details JSONB;

CREATE INDEX IF NOT EXISTS idx_run_step_results_comparison
    ON run_step_results (run_test_result_id)
    WHERE comparison_details IS NOT NULL;

COMMIT;
