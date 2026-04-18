-- PrimeQA Migration 028: multi-test-case generation foundation.
--
-- One "Generate" click used to produce a single TC per requirement.
-- That hides coverage gaps (positive happy path only; negative /
-- boundary / edge / regression tests missing). This migration sets
-- up the schema so one click can produce a *batch* of 3\u20136 TCs each
-- tagged with a coverage angle.
--
-- coverage_type: scenario angle the test validates. Free-form VARCHAR
-- (no CHECK yet) because we expect to add new types in future. The
-- current set used by the generator:
--   positive             \u2014 happy path works
--   negative_validation  \u2014 forbidden combination rejected
--   boundary             \u2014 at-threshold values (null, zero, max)
--   edge_case            \u2014 unusual but legal combinations
--   regression           \u2014 existing data still works after the feature
--
-- generation_batches: one row per "click Generate" \u2014 links the N TCs
-- that were produced together, captures the AI's rationale for the
-- selected coverage, and stores token / cost for superadmin audit.
--
-- Idempotent.

BEGIN;

ALTER TABLE test_cases
    ADD COLUMN IF NOT EXISTS coverage_type VARCHAR(30),
    ADD COLUMN IF NOT EXISTS generation_batch_id BIGINT;

CREATE INDEX IF NOT EXISTS idx_test_cases_coverage_type
    ON test_cases (tenant_id, coverage_type)
    WHERE deleted_at IS NULL AND coverage_type IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_test_cases_batch
    ON test_cases (generation_batch_id)
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS generation_batches (
    id BIGSERIAL PRIMARY KEY,
    tenant_id INT NOT NULL REFERENCES tenants(id),
    requirement_id INT NOT NULL REFERENCES requirements(id),
    created_by INT NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    llm_model VARCHAR(100),
    input_tokens INT,
    output_tokens INT,
    cost_usd NUMERIC(10, 4),
    -- LLM's rationale: "why these test cases?". Surfaced on the
    -- requirement detail page so users can audit coverage decisions.
    explanation TEXT,
    -- Snapshot of which coverage types were in this batch. Denormalised
    -- for fast UI queries; the authoritative list is the test_cases
    -- where generation_batch_id = this row's id.
    coverage_types TEXT[]
);

CREATE INDEX IF NOT EXISTS idx_generation_batches_req
    ON generation_batches (requirement_id, created_at DESC);

-- Add FK from test_cases now that generation_batches exists (was deferred
-- so the column could be created before the target table).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'test_cases_generation_batch_id_fkey'
    ) THEN
        ALTER TABLE test_cases
            ADD CONSTRAINT test_cases_generation_batch_id_fkey
            FOREIGN KEY (generation_batch_id)
            REFERENCES generation_batches(id)
            ON DELETE SET NULL;
    END IF;
END $$;

COMMIT;
