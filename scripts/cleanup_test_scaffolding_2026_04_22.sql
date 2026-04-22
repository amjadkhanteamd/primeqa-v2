-- scripts/cleanup_test_scaffolding_2026_04_22.sql
-- -----------------------------------------------------------------------------
-- Post-Prompt-16 audit: remove throwaway test scaffolding left by the
-- /run page overhaul test suite.
--
-- Safe removals only. Leaves everything that carries historical value:
--   * generation_batches with soft-deleted TCs -> KEPT (cost + LLM provenance)
--   * Soft-deleted TCs / versions              -> KEPT (supersession history)
--   * The 4 pre-existing test fixture sections -> KEPT (still actively used)
--
-- Removes:
--   * Custom permission sets from test_run_page_overhaul.py
--     (_rp_sprint_only, _rp_single_only) + their user assignments
--
-- Wrapped in a transaction; review row counts before COMMIT.
--
-- Run:
--   psql "$DATABASE_URL" < scripts/cleanup_test_scaffolding_2026_04_22.sql
-- -----------------------------------------------------------------------------

BEGIN;

-- Drop user assignments first (FK CASCADE would handle it but be explicit)
DELETE FROM user_permission_sets
WHERE permission_set_id IN (
    SELECT id FROM permission_sets
    WHERE tenant_id = 1 AND api_name IN ('_rp_sprint_only', '_rp_single_only')
);

DELETE FROM permission_sets
WHERE tenant_id = 1
  AND api_name IN ('_rp_sprint_only', '_rp_single_only');

-- Prune stale generation_jobs that never finished (>24h since heartbeat,
-- still in active state). These can accumulate if a worker died mid-job
-- and the reaper didn't clean up. Keep TERMINAL rows for audit.
UPDATE generation_jobs
SET status = 'failed',
    error_code = 'STALE_CLAIMED',
    error_message = 'Claimed row stale >24h with no heartbeat; reaped.',
    completed_at = NOW()
WHERE tenant_id = 1
  AND status IN ('queued', 'claimed', 'running')
  AND (heartbeat_at IS NULL OR heartbeat_at < NOW() - INTERVAL '24 hours');

-- Echo back the state so the operator can verify before COMMITting
\echo ---- permission_sets after cleanup ----
SELECT COUNT(*) AS remaining FROM permission_sets WHERE tenant_id = 1;
\echo ---- stale generation_jobs count ----
SELECT COUNT(*) AS stale_in_flight FROM generation_jobs
 WHERE tenant_id = 1
   AND status IN ('queued', 'claimed', 'running');

COMMIT;
