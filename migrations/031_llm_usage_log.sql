-- PrimeQA Migration 031: single-source LLM usage log.
--
-- All Anthropic API calls in PrimeQA now funnel through LLMGateway.
-- Each call writes one row here so superadmins can answer, for any
-- window of time:
--
--   Who spent what, on what feature, with which model, and was it
--   cache-hit, did it escalate, did it fail?
--
-- Populates:
--   /settings/llm-usage superadmin dashboard (cost / efficiency / quality)
--   per-run cost panel (joins via run_id)
--   per-tenant budget alerts (Phase 6)
--   feedback loop quality signals (Phase 4)
--
-- Idempotent.

BEGIN;

CREATE TABLE IF NOT EXISTS llm_usage_log (
    id                  BIGSERIAL PRIMARY KEY,
    ts                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    tenant_id           INT         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id             INT         REFERENCES users(id)            ON DELETE SET NULL,

    -- Registered task name (see primeqa/intelligence/llm/prompts/registry.py).
    -- Canonical values: test_plan_generation, failure_analysis, agent_fix,
    -- failure_summary, connection_test. New tasks add rows for future types.
    task                VARCHAR(40) NOT NULL,

    -- Model actually used (post-router). Kept as string to survive
    -- vendor / version changes without schema churn.
    model               VARCHAR(100) NOT NULL,

    -- Semver-ish prompt identifier from PromptRegistry
    -- (e.g. "test_plan_generation@v1"). Lets us A/B different prompts
    -- and attribute regressions to a prompt change.
    prompt_version      VARCHAR(60) NOT NULL,

    -- Raw token counts from provider.usage
    input_tokens        INT NOT NULL DEFAULT 0,
    output_tokens       INT NOT NULL DEFAULT 0,
    -- Anthropic prompt-caching stats. cached_input_tokens > 0 means
    -- we read from cache (~90% cheaper than normal input). _write is
    -- the one-off cost to populate the cache (~125% of normal).
    cached_input_tokens INT NOT NULL DEFAULT 0,
    cache_write_tokens  INT NOT NULL DEFAULT 0,

    cost_usd            NUMERIC(10, 6) NOT NULL DEFAULT 0,
    latency_ms          INT,

    -- ok | rate_limited | overloaded | timeout | network | auth_error |
    -- content_error | quota_exceeded | provider_error
    status              VARCHAR(20) NOT NULL DEFAULT 'ok',

    -- Router metadata \u2014 which complexity bucket drove model choice,
    -- and was this an escalation retry (second call in a chain).
    complexity          VARCHAR(10),
    escalated           BOOLEAN NOT NULL DEFAULT FALSE,

    -- Anthropic's request_id for support escalation
    request_id          VARCHAR(80),

    -- Cross-references \u2014 null-safe so deleting a TC / run doesn't
    -- erase history.
    run_id              INT REFERENCES pipeline_runs(id)    ON DELETE SET NULL,
    requirement_id      INT REFERENCES requirements(id)     ON DELETE SET NULL,
    test_case_id        INT REFERENCES test_cases(id)       ON DELETE SET NULL,
    generation_batch_id BIGINT REFERENCES generation_batches(id) ON DELETE SET NULL,

    -- Free-form bucket for task-specific extras (e.g. validation critical
    -- count, agent_fix_attempt_id). Never contains PII or prompt text.
    context             JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- Per-tenant rollups (dashboard: top tenants, per-feature, per-model)
CREATE INDEX IF NOT EXISTS idx_llm_usage_tenant_ts
    ON llm_usage_log (tenant_id, ts DESC);

-- Per-feature analysis ("cost per test_plan_generation last 30 days")
CREATE INDEX IF NOT EXISTS idx_llm_usage_task_ts
    ON llm_usage_log (task, ts DESC);

-- Non-OK calls surface errors for the dashboard error-rate widget
CREATE INDEX IF NOT EXISTS idx_llm_usage_errors
    ON llm_usage_log (tenant_id, ts DESC)
    WHERE status <> 'ok';

-- Run attribution: "show me the LLM cost of run #89"
CREATE INDEX IF NOT EXISTS idx_llm_usage_run
    ON llm_usage_log (run_id)
    WHERE run_id IS NOT NULL;

COMMIT;
