-- Migration 036: schema guards for pipeline_run + environment integrity.
--
-- Audit findings (2026-04-19):
--   C-5: 19 "completed" pipeline_runs had total_tests IN (0, NULL) — ghost
--        data that polluted dashboards.
--   M-9: no CHECK constraint on pipeline_runs.status — code could flip a
--        terminal run back to 'running' silently.
--   M-10: duplicate "Pipeline Test Env" on tenant 1.
--
-- Before running THIS migration, run
--   scripts/audit_cleanup_ghost_runs_2026_04_19.sql
-- which fixes the existing dirty data so the constraints don't reject
-- on creation.
--
-- Idempotent (DO-block checks for existing constraint / index).

BEGIN;

-- 1. Restrict pipeline_runs.status to the known enum values.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pipeline_runs_status_ck') THEN
        ALTER TABLE pipeline_runs
            ADD CONSTRAINT pipeline_runs_status_ck
            CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled'));
    END IF;
END $$;

-- 2. If a run is terminal (completed | failed | cancelled), it must
--    have a completed_at. Prevents the "completed with no timestamp"
--    class of bug + nudges state-machine callers.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pipeline_runs_terminal_completed_at_ck') THEN
        ALTER TABLE pipeline_runs
            ADD CONSTRAINT pipeline_runs_terminal_completed_at_ck
            CHECK (
                status NOT IN ('completed', 'failed', 'cancelled')
                OR completed_at IS NOT NULL
            );
    END IF;
END $$;

-- 3. UNIQUE (tenant_id, name) on environments.
--    Environments has no deleted_at (hard-delete table), so a plain
--    unique index is fine.
CREATE UNIQUE INDEX IF NOT EXISTS environments_tenant_name_uk
    ON environments (tenant_id, lower(name));

COMMIT;
