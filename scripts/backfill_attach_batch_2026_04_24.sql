-- Backfill: re-attach llm_usage_log rows to the generation_batches
-- they produced, for test_plan_generation calls orphaned between
-- Prompt 15 (atomic-batch refactor, 2026-04-19) and this fix
-- (2026-04-24).
--
-- Root cause: service.generate_test_plan called attach_batch between
-- db.flush() and db.commit(). attach_batch opens its own Session, so
-- the FK check couldn't see the flushed-but-uncommitted batch row and
-- failed silently. See commit message for the fix itself.
--
-- Strategy: match by (tenant_id, requirement_id, ts within window).
-- LLMUsageLog has dedicated tenant_id + requirement_id columns so
-- the match is tighter than the 2026-04-19 backfill, which only had
-- timestamp + tenant to go on. The 2026-04-19 script already handled
-- test_plan rows that were never attached for an earlier reason, so
-- some of the current orphans pre-date even that bug — we match
-- without a lower time bound and rely on requirement_id to avoid
-- cross-linking.
--
-- 2026-04-24 expected outcome on production: 30 orphan rows linked
-- to their batches. Post-check at the bottom should show zero
-- remaining rows where requirement_id IS NOT NULL and
-- generation_batch_id IS NULL.

BEGIN;

-- Dry-run preview of the links that will be written.
SELECT u.id AS usage_id, u.tenant_id, u.requirement_id, u.ts,
       u.cost_usd AS usage_cost,
       b.id AS batch_id, b.created_at, b.cost_usd AS batch_cost
FROM llm_usage_log u
JOIN LATERAL (
    SELECT b.id, b.created_at, b.cost_usd
    FROM generation_batches b
    WHERE b.tenant_id = u.tenant_id
      AND b.requirement_id = u.requirement_id
      AND b.created_at BETWEEN u.ts - INTERVAL '60 seconds'
                           AND u.ts + INTERVAL '120 seconds'
    ORDER BY ABS(EXTRACT(EPOCH FROM (b.created_at - u.ts))) ASC
    LIMIT 1
) b ON TRUE
WHERE u.task = 'test_plan_generation'
  AND u.status = 'ok'
  AND u.generation_batch_id IS NULL
  AND u.requirement_id IS NOT NULL
ORDER BY u.id;

-- Apply the link. LATERAL subquery picks the single closest batch per
-- usage row so a double-burst within the window doesn't cross-link.
-- Bracketing on requirement_id means we won't match a same-tenant
-- batch for a different requirement that happened to fire around
-- the same second.
UPDATE llm_usage_log u
SET generation_batch_id = (
    SELECT b.id FROM generation_batches b
    WHERE b.tenant_id = u.tenant_id
      AND b.requirement_id = u.requirement_id
      AND b.created_at BETWEEN u.ts - INTERVAL '60 seconds'
                           AND u.ts + INTERVAL '120 seconds'
    ORDER BY ABS(EXTRACT(EPOCH FROM (b.created_at - u.ts))) ASC
    LIMIT 1
)
WHERE u.task = 'test_plan_generation'
  AND u.status = 'ok'
  AND u.generation_batch_id IS NULL
  AND u.requirement_id IS NOT NULL;

COMMIT;

-- Post-check: orphans remaining after backfill. Expected: 0 (or rows
-- where no matching batch exists within the window — those are truly
-- orphaned, likely from failed generations where the batch row rolled
-- back).
SELECT COUNT(*) AS remaining_orphans
FROM llm_usage_log
WHERE task = 'test_plan_generation'
  AND status = 'ok'
  AND generation_batch_id IS NULL
  AND requirement_id IS NOT NULL;

-- Spot-check a few newly-linked rows.
SELECT id, tenant_id, requirement_id, ts, cost_usd, generation_batch_id
FROM llm_usage_log
WHERE task = 'test_plan_generation'
  AND status = 'ok'
ORDER BY id DESC
LIMIT 20;
