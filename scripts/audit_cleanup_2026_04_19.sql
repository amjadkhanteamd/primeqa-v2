-- Audit cleanup — executed 2026-04-19.
--
-- Two targeted mutations:
--
-- 1. Purge integration-test pollution — 38 soft-deleted test cases whose
--    title is "Cleanup Test" or "Updated title" (integration-test fixture
--    remnants from test_pipeline.py / test_executor.py / test_cleanup.py).
--    These are already soft-deleted; purging them removes the dead weight
--    from the test_cases row count (38 soft-deleted vs 13 active → 3×
--    more deleted than live). FKs are ON DELETE SET NULL or CASCADE, so
--    no orphans.
--
-- 2. Reap 27 stuck pipeline_runs with status='running' + started_at older
--    than 24h. The new `reap_stuck_runs` scheduler task (audit F2, this
--    commit) catches future cases; this one-shot clears the existing
--    backlog so the runs table doesn't present a misleading 46% "running"
--    rate.
--
-- Safe to rerun — both ops are idempotent (no re-purge if row is gone;
-- the UPDATE is a WHERE status='running' filter so it's a no-op for
-- already-cancelled rows).

-- NOTE: on-run, (1) was dropped because run_test_results.test_case_id
-- is a restrictive FK (ON DELETE RESTRICT) — the 38 soft-deleted TCs
-- have run history and can't be hard-purged without cascading. They're
-- already filtered from dashboards by `deleted_at IS NULL`, so this is
-- cosmetic only. Leaving them is the safer call.

BEGIN;

-- 2. Cancel stuck runs older than 24h (complement to the new reaper).
UPDATE pipeline_runs
   SET status = 'cancelled',
       completed_at = COALESCE(completed_at, now()),
       error_message = COALESCE(
           error_message,
           'Auto-cancelled during audit cleanup 2026-04-19: stuck in running > 24h'
       )
 WHERE status = 'running'
   AND started_at < now() - interval '24 hours';

COMMIT;

-- Post-run verification (run separately):
--   SELECT status, COUNT(*) FROM pipeline_runs GROUP BY 1 ORDER BY 2 DESC;
--   SELECT status, COUNT(*) FROM test_cases GROUP BY status ORDER BY 2 DESC;
