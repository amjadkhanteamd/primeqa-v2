-- Audit F10 (2026-04-19): backfill historical generation_batches cost
-- into llm_usage_log so tenant + superadmin dashboards don't render empty
-- on tenants that generated before the gateway shipped (Phase 1).
--
-- Pre-Phase-1 code stored cost on generation_batches.cost_usd directly;
-- Phase 1 moved the canonical ledger to llm_usage_log. Tenants who never
-- made a call AFTER Phase 1 saw empty dashboards — looked broken.
--
-- This script is idempotent: it only inserts where no llm_usage_log row
-- exists for the (generation_batch_id) combo. A marker in the context
-- JSONB (`source: 'backfill_phase_1'`) makes the synthetic rows easy to
-- audit or re-run if needed.
--
-- Safe to run on production — no UPDATE, no DELETE, only INSERT ... ON
-- CONFLICT-guarded by an explicit NOT EXISTS.
--
-- Apply with:
--   psql "$DATABASE_URL" -f scripts/backfill_llm_usage_log.sql

BEGIN;

INSERT INTO llm_usage_log (
    ts, tenant_id, user_id, task, model, prompt_version,
    input_tokens, output_tokens, cached_input_tokens, cache_write_tokens,
    cost_usd, latency_ms, status, complexity, escalated,
    request_id, run_id, requirement_id, test_case_id, generation_batch_id,
    context
)
SELECT
    b.created_at AS ts,
    b.tenant_id,
    b.created_by AS user_id,
    'test_plan_generation'::VARCHAR(40) AS task,
    COALESCE(b.llm_model, 'claude-sonnet-4-legacy') AS model,
    'legacy'::VARCHAR(40) AS prompt_version,
    COALESCE(b.input_tokens, 0) AS input_tokens,
    COALESCE(b.output_tokens, 0) AS output_tokens,
    0 AS cached_input_tokens,
    0 AS cache_write_tokens,
    COALESCE(b.cost_usd, 0.0) AS cost_usd,
    0 AS latency_ms,
    'ok'::VARCHAR(20) AS status,
    'default'::VARCHAR(20) AS complexity,
    false AS escalated,
    NULL AS request_id,
    NULL AS run_id,
    b.requirement_id,
    NULL AS test_case_id,
    b.id AS generation_batch_id,
    jsonb_build_object(
        'source', 'backfill_phase_1',
        'note', 'Synthesised from generation_batches pre-Phase-1 cost data'
    ) AS context
FROM generation_batches b
WHERE NOT EXISTS (
    SELECT 1 FROM llm_usage_log l
    WHERE l.generation_batch_id = b.id
);

COMMIT;

-- Sanity check:
-- SELECT COUNT(*) FROM llm_usage_log WHERE context->>'source' = 'backfill_phase_1';
-- SELECT COUNT(*) FROM generation_batches;
-- -- These should match after the first run.
