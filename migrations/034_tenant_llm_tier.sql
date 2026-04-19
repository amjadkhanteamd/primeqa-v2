-- PrimeQA Migration 034: tenant LLM tier.
--
-- Sells the shared-key proposition as product tiers instead of raw
-- numeric caps. Sets a single `llm_tier` field from which the defaults
-- for llm_max_calls_per_minute / _per_hour / _spend_per_day flow if
-- they are NULL, while preserving the ability for a superadmin to pin
-- a custom number on any tier (e.g. "Pro tenant, 1000/day spend").
--
-- Tier presets lived in primeqa/intelligence/llm/tiers.py; this column
-- just names which tier a tenant is on.
--
-- Tiers:
--   starter     default for fresh tenants; generous for trial usage
--   pro         paid tier; higher caps
--   enterprise  effectively unlimited caps + premium models allowed
--   custom      use the raw numeric columns, ignore tier presets
--
-- Idempotent.

BEGIN;

ALTER TABLE tenant_agent_settings
    ADD COLUMN IF NOT EXISTS llm_tier VARCHAR(20) NOT NULL DEFAULT 'starter';

-- Constraint to keep the value enumerated. DEFERRABLE so it applies
-- to existing rows (all default to 'starter' by the column default).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'tas_llm_tier_ck'
    ) THEN
        ALTER TABLE tenant_agent_settings
            ADD CONSTRAINT tas_llm_tier_ck
            CHECK (llm_tier IN ('starter', 'pro', 'enterprise', 'custom'));
    END IF;
END $$;

COMMIT;
