-- Backfill: link llm_usage_log rows to the generation_batches they
-- produced. Pre-dates the attach_batch() change in gateway/service; the
-- LLM call was logged first (batch row didn't exist yet) and the batch
-- id was never written back.
--
-- Strategy: match by (task='test_plan_generation', status='ok',
-- tenant_id) and timestamp within a 60-second window of the batch's
-- created_at. One row per batch — pick the closest match.
--
-- 2026-04-19: 3 orphan rows (ids 5, 7, 9) linked to batches 4, 5, 6.

BEGIN;

-- Dry-run preview of the links that will be written.
SELECT u.id AS usage_id, u.ts, u.cost_usd AS usage_cost,
       b.id AS batch_id, b.created_at, b.cost_usd AS batch_cost
FROM llm_usage_log u
JOIN generation_batches b
  ON b.tenant_id = u.tenant_id
 AND b.created_at BETWEEN u.ts - INTERVAL '60 seconds'
                      AND u.ts + INTERVAL '60 seconds'
WHERE u.task = 'test_plan_generation'
  AND u.status = 'ok'
  AND u.generation_batch_id IS NULL
ORDER BY u.id;

-- Apply the link. LATERAL subquery picks the single closest batch per
-- usage row so a double-burst within 60s doesn't cross-link.
UPDATE llm_usage_log u
SET generation_batch_id = (
    SELECT b.id FROM generation_batches b
    WHERE b.tenant_id = u.tenant_id
      AND b.created_at BETWEEN u.ts - INTERVAL '60 seconds'
                           AND u.ts + INTERVAL '60 seconds'
    ORDER BY ABS(EXTRACT(EPOCH FROM (b.created_at - u.ts))) ASC
    LIMIT 1
)
WHERE u.task = 'test_plan_generation'
  AND u.status = 'ok'
  AND u.generation_batch_id IS NULL;

COMMIT;

-- Post-check
SELECT id, task, status, generation_batch_id, cost_usd
FROM llm_usage_log
WHERE task = 'test_plan_generation' AND status = 'ok'
ORDER BY id;
