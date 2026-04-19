-- PrimeQA Migration 032: per-tenant LLM limits on tenant_agent_settings.
--
-- Shared-key SaaS means one tenant can exhaust shared Anthropic quota
-- and block the rest. These limits let the LLMGateway throttle or
-- block calls before they leave the building.
--
-- NULL on a column = no limit (default for starter / dev tenants until
-- the superadmin sets a cap). Counts are windowed over the llm_usage_log
-- table so no state needs to be maintained outside Postgres.
--
--   llm_max_calls_per_minute   : soft sanity ceiling, e.g. 30 for Starter
--   llm_max_calls_per_hour     : mid-window cap, e.g. 300 for Starter
--   llm_max_spend_per_day_usd  : hard daily wallet cap (spend-based, not count)
--
-- Router + LLMGateway consult these in gateway.llm_call() before
-- invoking the provider. A blocked call emits status='rate_limited'
-- to the usage log (0 tokens) so the dashboard sees it, then raises
-- LLMError("rate_limited", ...).
--
-- Idempotent.

BEGIN;

ALTER TABLE tenant_agent_settings
    ADD COLUMN IF NOT EXISTS llm_max_calls_per_minute  INT,
    ADD COLUMN IF NOT EXISTS llm_max_calls_per_hour    INT,
    ADD COLUMN IF NOT EXISTS llm_max_spend_per_day_usd NUMERIC(10, 2),
    -- Policy flags used by the router (see TenantPolicy in llm/router.py):
    ADD COLUMN IF NOT EXISTS llm_always_use_opus  BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS llm_allow_haiku      BOOLEAN NOT NULL DEFAULT TRUE;

COMMIT;
