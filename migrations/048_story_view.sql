-- Migration 048: story-view enrichment
--
-- Adds:
--   * test_case_versions.story_view JSONB — nullable, populated by the
--     StoryViewEnricher when the tenant flag below is enabled. Shape:
--       { title, description, preconditions_narrative,
--         expected_outcome, model, prompt_version, generated_at }
--     Nullable; rendering code falls back to the mechanical step
--     view when NULL (backward compat + feature-off).
--
--   * tenant_agent_settings.llm_enable_story_enrichment BOOLEAN —
--     per-tenant feature flag, default false. Superadmin toggles via
--     /settings/llm-usage. When false, generate_test_plan skips the
--     enrichment entirely (no LLM cost, no story_view populated).
--
-- Both columns are idempotent (ADD COLUMN IF NOT EXISTS).

BEGIN;

ALTER TABLE test_case_versions
    ADD COLUMN IF NOT EXISTS story_view JSONB;

ALTER TABLE tenant_agent_settings
    ADD COLUMN IF NOT EXISTS llm_enable_story_enrichment BOOLEAN NOT NULL DEFAULT false;

COMMIT;
