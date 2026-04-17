-- PrimeQA Migration 017: Super Admin role + run provenance columns.
--
-- Adds:
--   - `superadmin` role ('god mode' per tenant; excluded from 20-user cap)
--   - `pipeline_runs.source_refs JSONB`: rich provenance for wizard-based runs
--     (mixed Jira projects + sprints + suites + hand-picked tests) so the run
--     history can display exactly what was requested and "Rerun" can re-POST
--     the same payload without relying on the legacy `source_type`+`source_ids`
--     pair.
--   - `pipeline_runs.parent_run_id`: rerun lineage (used by R5 agent loop but
--     harmless to add now).
--
-- This migration is idempotent; re-running is safe.

BEGIN;

-- ---- Super Admin role -------------------------------------------------------
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check
    CHECK (role IN ('superadmin', 'admin', 'tester', 'ba', 'viewer'));

-- Promote the bootstrap admin to super admin for existing tenants.
UPDATE users
SET role = 'superadmin'
WHERE email = 'admin@primeqa.io' AND role = 'admin';

-- ---- Run provenance ---------------------------------------------------------
ALTER TABLE pipeline_runs
    ADD COLUMN IF NOT EXISTS source_refs JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE pipeline_runs
    ADD COLUMN IF NOT EXISTS parent_run_id INTEGER
        REFERENCES pipeline_runs(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_parent
    ON pipeline_runs(parent_run_id)
    WHERE parent_run_id IS NOT NULL;

COMMIT;
