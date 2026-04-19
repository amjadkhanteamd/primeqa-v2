-- One-off backfill: heal historic ghost run_test_results.
--
-- Discovered 2026-04-19: the scheduler's reap_orphan_rtrs task caps at
-- 6 hours, but the CHECK-constraint + worker-death bugs predate that
-- window. Scan showed 9 ghost rtrs aged 6-24h — all from the old bug.
--
-- This script does what the healer does but without the time window:
--   1. UPDATE rtr.status = 'failed', failure_type = 'step_error',
--      failure_summary = first failed child step's error (truncated)
--   2. Works for any rtr whose status='passed' but has a failed/error
--      child step_result.
--
-- Feedback signals are NOT fired here — that's a separate concern:
-- historical data may reference fields that have since been added /
-- removed, so signals from old failures would pollute the prompt
-- context. The online healer catches new failures in the 6h window
-- and fires signals for those; for historical data we only reconcile
-- the status so dashboards tell the truth.
--
-- Idempotent. Re-running is a no-op (subsequent runs won't find any
-- 'passed' rtr with failed children).

BEGIN;

WITH ghosts AS (
    SELECT r.id,
           (SELECT error_message FROM run_step_results
             WHERE run_test_result_id = r.id
               AND status IN ('failed', 'error')
             ORDER BY step_order LIMIT 1) AS first_err
    FROM run_test_results r
    WHERE r.status = 'passed'
      AND EXISTS (
        SELECT 1 FROM run_step_results s
        WHERE s.run_test_result_id = r.id
          AND s.status IN ('failed', 'error')
      )
)
UPDATE run_test_results r
   SET status = 'failed',
       failure_type = 'step_error',
       failure_summary = COALESCE(
           LEFT(g.first_err, 500),
           'Historic ghost rtr healed by backfill 2026-04-19'
       )
FROM ghosts g
WHERE r.id = g.id;

COMMIT;

-- Verify:
--   SELECT r.id, r.status, r.failure_type, LEFT(r.failure_summary, 80)
--   FROM run_test_results r
--   WHERE r.status = 'passed'
--     AND EXISTS (SELECT 1 FROM run_step_results s
--                  WHERE s.run_test_result_id = r.id
--                    AND s.status IN ('failed','error'));
-- — should return 0 rows after the backfill.
