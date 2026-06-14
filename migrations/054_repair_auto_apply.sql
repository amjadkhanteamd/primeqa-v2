-- Migration 054: per-tenant flag for the auto-fix agent's AUTONOMOUS apply
-- (theme #6, D-236).
--
-- The D-215.1 spine proposes deterministic repairs; D-236 adds the LLM layer
-- that proposes a concrete recipe_edit + scores confidence. By default EVERY
-- proposal is human-approved on the Repairs panel. This flag (default FALSE)
-- enables AUTONOMOUS apply — and even then ONLY on a SANDBOX env at confidence
-- >= the tenant's trust_threshold_high; a PRODUCTION env is NEVER auto-applied.
--
-- The gate REUSES the v1 agent's existing tenant_agent_settings columns:
--   trust_threshold_high (default "0.85") — the auto-apply confidence floor,
--   max_fix_attempts_per_run (default 3)  — the per-claim attempt cap,
--   agent_enabled (default true)          — the master agent switch.
-- This flag is the NEW, separate, default-OFF autonomy switch for the substrate.
--
-- Superadmin toggles it on /settings/llm-usage (the per-tenant Plan cell),
-- alongside the Story / Packs toggles (migrations 048 / 049).
--
-- Idempotent (IF NOT EXISTS). Safe to re-apply.

ALTER TABLE tenant_agent_settings
    ADD COLUMN IF NOT EXISTS repair_auto_apply BOOLEAN NOT NULL DEFAULT false;
