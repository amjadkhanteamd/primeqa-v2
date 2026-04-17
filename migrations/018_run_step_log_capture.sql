-- PrimeQA Migration 018: Run-step log capture extensions.
--
-- Adds columns to `run_step_results` so the executor can persist, per step:
--   - `soql_queries JSONB`     — SOQL statements issued during the step
--   - `llm_prompt_sha`/`llm_response_sha` (text hashes) + `llm_payload_json`
--     (the full prompt+response) for any AI-involved step
--   - `http_status INTEGER`    — HTTP status of the outbound SF call (if any)
--   - `timings JSONB`          — sub-timings {sf_ms, llm_ms, setup_ms, ...}
--   - `failure_class`          — classification tag written by R5 triage
--     (kept here so the column exists from R1 onward, even if R1 never writes it)
--   - `correlation_id`         — ties SF logs / Anthropic logs / Railway logs
--
-- Also adds `run_test_results.correlation_id` so consumers can scope by test.
--
-- Idempotent.

BEGIN;

ALTER TABLE run_step_results
    ADD COLUMN IF NOT EXISTS soql_queries     JSONB,
    ADD COLUMN IF NOT EXISTS llm_prompt_sha   VARCHAR(64),
    ADD COLUMN IF NOT EXISTS llm_response_sha VARCHAR(64),
    ADD COLUMN IF NOT EXISTS llm_payload      JSONB,
    ADD COLUMN IF NOT EXISTS http_status      INTEGER,
    ADD COLUMN IF NOT EXISTS timings          JSONB,
    ADD COLUMN IF NOT EXISTS failure_class    VARCHAR(40),
    ADD COLUMN IF NOT EXISTS correlation_id   VARCHAR(64);

ALTER TABLE run_test_results
    ADD COLUMN IF NOT EXISTS correlation_id   VARCHAR(64);

-- Filterable index on failure_class (for the agent / dashboards later)
CREATE INDEX IF NOT EXISTS idx_run_step_results_failure_class
    ON run_step_results(failure_class)
    WHERE failure_class IS NOT NULL;

-- Index on correlation_id for cross-system log joins
CREATE INDEX IF NOT EXISTS idx_run_step_results_correlation
    ON run_step_results(correlation_id)
    WHERE correlation_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_run_test_results_correlation
    ON run_test_results(correlation_id)
    WHERE correlation_id IS NOT NULL;

COMMIT;
