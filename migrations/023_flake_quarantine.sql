-- PrimeQA Migration 023: Flake quarantine support.
--
-- Simple approach: add `is_quarantined` boolean to test_cases + a
-- `flake_last_toggle_count` integer to track recent pass/fail flips. The
-- flake computation itself runs as a read-only query over run_test_results
-- history, so we don't need a dedicated flake-scoring table.

BEGIN;

ALTER TABLE test_cases
    ADD COLUMN IF NOT EXISTS is_quarantined BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS quarantined_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS quarantined_reason TEXT;

CREATE INDEX IF NOT EXISTS idx_test_cases_quarantined
    ON test_cases(tenant_id) WHERE is_quarantined = TRUE;

COMMIT;
