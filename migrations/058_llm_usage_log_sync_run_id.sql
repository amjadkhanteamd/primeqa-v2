-- 058_llm_usage_log_sync_run_id.sql
-- S1 sync cost-telemetry (1d): attribute embedding + summary LLM usage to the
-- sync_run that drove it, so a per-sync-run cost roll-up is possible
-- (primeqa/intelligence/llm/usage.get_sync_run_cost).
--
-- SOFT cross-schema reference (UUID, NO FK). llm_usage_log lives in the PUBLIC
-- schema (this file, migration 031); sync_runs lives PER-TENANT (alembic
-- schema-per-tenant, 20260430_0020_phase2_sync_runs). PostgreSQL forbids a
-- foreign key from a public table into a tenant schema, so sync_run_id is a
-- plain nullable UUID column whose referential integrity is enforced in
-- application code (the writer resolves it via
-- primeqa/sync/readiness.resolve_active_sync_run_id), not by a DB constraint.
--
-- Pure instrumentation: additive + idempotent (016+ convention). NULL for all
-- existing rows and for every non-sync LLM call (generation, interpretation,
-- conversation). Does NOT change embedding/summary behaviour or bucketing.

ALTER TABLE llm_usage_log
    ADD COLUMN IF NOT EXISTS sync_run_id UUID;

COMMENT ON COLUMN llm_usage_log.sync_run_id IS
    'Soft (no-FK) reference to <tenant_schema>.sync_runs(id): the S1 sync run '
    'this embedding/summary LLM call was performed for. NULL for non-sync usage. '
    'Cross-schema (public -> per-tenant) so a FK is impossible; integrity is '
    'enforced in app code (readiness.resolve_active_sync_run_id). Migration 058.';

-- Partial index: only sync-attributed rows are indexed (mirrors the
-- idx_llm_usage_run WHERE run_id IS NOT NULL pattern from migration 031).
-- get_sync_run_cost filters by an exact sync_run_id, so this stays lean.
CREATE INDEX IF NOT EXISTS idx_llm_usage_sync_run
    ON llm_usage_log (sync_run_id)
    WHERE sync_run_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Downgrade (manual — public migrations are forward-only; reversible by hand):
--   DROP INDEX IF EXISTS idx_llm_usage_sync_run;
--   ALTER TABLE llm_usage_log DROP COLUMN IF EXISTS sync_run_id;
-- ---------------------------------------------------------------------------
