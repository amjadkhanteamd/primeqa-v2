-- Migration 050: per-tenant LLM-interpretation-phrasing flag (D-117)
--
-- The S6 LLM-phrasing presentation layer (D-117) is feature-gated per tenant,
-- mirroring the story_view enrichment flag (migration 048). Default off. The v1
-- on-demand helper (intelligence.interpretation_phrasing.get_or_phrase) checks
-- this flag before phrasing an interpretation; with it off, get_or_phrase
-- returns None (no LLM call, no cache write).
--
-- Idempotent (ADD COLUMN IF NOT EXISTS) per the migrations-016+ convention.

ALTER TABLE tenant_agent_settings
    ADD COLUMN IF NOT EXISTS llm_enable_interpretation_phrasing BOOLEAN NOT NULL DEFAULT false;
