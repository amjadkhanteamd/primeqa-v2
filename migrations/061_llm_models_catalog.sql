-- 061: platform-wide LLM model catalog overlay (per-tenant model control arc).
-- The selectable-model set is (router.SELECTABLE_MODELS ∪ active rows here)
-- − (retired rows here); see router.selectable_model_ids(). Rows are written
-- only at runtime by the superadmin Models panel + the catalog refresh:
--   * status='active'  — a model enabled from the refresh panel. Pricing is
--     entered at enable time (Anthropic's Models API returns NO price data),
--     so "selectable ⇒ correctly priced" holds by construction
--     (pricing.resolve_rates checks this table before MODEL_PRICING).
--   * status='retired' — a model gone from GET /v1/models upstream. May also
--     name a CODE model (router constant), subtracting it from the picker
--     without a deploy. Never deleted: retirement is audit state.
-- NOT tenant-scoped by design: the model catalog is a platform fact, like
-- MODEL_PRICING. Cache read/write rates derive from input (×0.10 / ×1.25,
-- the ModelPrice rule) — only the two entered rates are stored.
CREATE TABLE IF NOT EXISTS llm_models (
    model_id            VARCHAR(64) PRIMARY KEY,
    display_name        VARCHAR(128),
    status              VARCHAR(16) NOT NULL DEFAULT 'active',
    input_usd_per_mtok  NUMERIC(10, 4),
    output_usd_per_mtok NUMERIC(10, 4),
    last_seen_upstream_at TIMESTAMPTZ,
    enabled_by          INTEGER REFERENCES users(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'llm_models_status_ck') THEN
        ALTER TABLE llm_models
            ADD CONSTRAINT llm_models_status_ck
            CHECK (status IN ('active', 'retired'));
    END IF;
    -- An ACTIVE (selectable) row must carry its own pricing; a RETIRED row
    -- may be pricing-less (it can mark a code model whose pricing lives in
    -- MODEL_PRICING).
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'llm_models_active_priced_ck') THEN
        ALTER TABLE llm_models
            ADD CONSTRAINT llm_models_active_priced_ck
            CHECK (status <> 'active'
                   OR (input_usd_per_mtok IS NOT NULL
                       AND output_usd_per_mtok IS NOT NULL));
    END IF;
END $$;
