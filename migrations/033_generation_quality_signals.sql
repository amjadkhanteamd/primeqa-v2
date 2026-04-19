-- PrimeQA Migration 033: generation_quality_signals \u2014 the feedback loop.
--
-- "You're missing this \u2014 feedback loop" was the architect's biggest
-- callout. Every system-generated test case produces signals back:
--
--   validation_critical   validator caught a critical issue immediately
--   regenerated_soon      same user regenerated for the same requirement
--                         within 15 minutes (implicit rejection)
--   execution_failed      run produced a validation_blocked / SF error
--                         referencing fields/objects the AI hallucinated
--   ba_rejected           BA review workflow marked the version rejected
--
-- Signals are collected by FeedbackCollector (primeqa/intelligence/llm/
-- feedback.py) from the validator, the generation service, the worker,
-- and the BA review flow. PromptBuilder reads the last few signals for
-- the calling tenant and includes them in the next generation prompt as
-- "don't do this" few-shot context. That closes the loop: system
-- improves without manual prompt-engineering intervention.
--
-- Idempotent.

BEGIN;

CREATE TABLE IF NOT EXISTS generation_quality_signals (
    id                    BIGSERIAL PRIMARY KEY,
    tenant_id             INT          NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    generation_batch_id   BIGINT       REFERENCES generation_batches(id) ON DELETE SET NULL,
    test_case_id          INT          REFERENCES test_cases(id)         ON DELETE SET NULL,
    test_case_version_id  INT          REFERENCES test_case_versions(id) ON DELETE SET NULL,

    signal_type           VARCHAR(40)  NOT NULL,
    -- validation_critical | validation_warning | regenerated_soon |
    -- execution_failed | ba_rejected

    severity              VARCHAR(10)  NOT NULL DEFAULT 'medium',
    -- low | medium | high  (drives which signals enter the prompt)

    -- Free-form detail for dashboards + prompt inclusion. Shape varies
    -- per signal_type, e.g.:
    --   validation_critical: {"rule": "field_not_found",
    --                          "object": "Account", "field": "Last_E..."}
    --   execution_failed:    {"error": "MALFORMED_ID: $test_account"}
    --   regenerated_soon:    {"delta_seconds": 42,
    --                          "prior_batch_id": 12}
    detail                JSONB        NOT NULL DEFAULT '{}'::jsonb,

    captured_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    expires_at            TIMESTAMPTZ
    -- NULL means "no expiry". Setter may set this for ephemeral signals
    -- we don't want feeding into prompts forever (e.g. regression'd
    -- tests after metadata refresh).
);

-- Primary access pattern: recent signals for a tenant for the prompt.
CREATE INDEX IF NOT EXISTS idx_gqs_tenant_ts
    ON generation_quality_signals (tenant_id, captured_at DESC);

-- Dashboard drill-through: signals for a specific requirement / batch.
CREATE INDEX IF NOT EXISTS idx_gqs_batch
    ON generation_quality_signals (generation_batch_id)
    WHERE generation_batch_id IS NOT NULL;

COMMIT;
