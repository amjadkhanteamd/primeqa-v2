-- One-shot data cleanup for audit findings C-5 (ghost runs) + M-10
-- (dup environments). Must run BEFORE migration 036 or the UNIQUE
-- index creation will fail.
--
-- Apply with:
--   psql "$DATABASE_URL" -f scripts/audit_cleanup_ghost_runs_2026_04_19.sql
--   psql "$DATABASE_URL" -f migrations/036_pipeline_run_integrity.sql
--
-- Idempotent; WHERE clauses narrow mutations so re-run is a no-op.

-- Two separate transactions — either can succeed independently and
-- both are idempotent.

-- 1. Cancel ghost "completed" runs (19 rows).
BEGIN;
UPDATE pipeline_runs
   SET status = 'cancelled',
       completed_at = COALESCE(completed_at, now()),
       error_message = COALESCE(
           error_message,
           'Auto-cancelled during audit 2026-04-19: completed with 0 tests (ghost run)'
       )
 WHERE status = 'completed'
   AND (total_tests IS NULL OR total_tests = 0);
COMMIT;

-- 2. Rename duplicate environments. Cannot DELETE because pipeline_runs
-- reference them via FK; rename with a " (dup)" suffix so the new UNIQUE
-- index accepts the table. Keeps oldest row canonical.
BEGIN;
WITH dups AS (
    SELECT id, ROW_NUMBER() OVER (
        PARTITION BY tenant_id, lower(name) ORDER BY id
    ) AS rn
    FROM environments
)
UPDATE environments
   SET name = environments.name || ' (dup)'
 WHERE id IN (SELECT id FROM dups WHERE rn > 1);
COMMIT;

-- Verify:
--   SELECT status, COUNT(*) FROM pipeline_runs GROUP BY 1;
--   SELECT tenant_id, name, COUNT(*) FROM environments GROUP BY 1, 2 HAVING COUNT(*) > 1;
