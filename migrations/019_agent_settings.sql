-- PrimeQA Migration 019: Tenant agent settings + release decision CI verdict flag.
--
-- Adds:
--   - tenant_agent_settings: per-tenant agent autonomy + trust-band thresholds
--     (Q12: defaults 0.85/0.60, Super Admin configurable).
--   - release_decisions.agent_verdict_counts (Q3): per-release flag determining
--     whether /api/releases/:id/status returns pre-agent or post-agent verdict.
--     Default true (post-agent).
--
-- Idempotent.

BEGIN;

CREATE TABLE IF NOT EXISTS tenant_agent_settings (
    tenant_id                INTEGER     PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    agent_enabled            BOOLEAN     NOT NULL DEFAULT true,
    trust_threshold_high     NUMERIC(3,2) NOT NULL DEFAULT 0.85,
    trust_threshold_medium   NUMERIC(3,2) NOT NULL DEFAULT 0.60,
    max_fix_attempts_per_run INTEGER     NOT NULL DEFAULT 3,
    updated_by               INTEGER     REFERENCES users(id),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT trust_bands_sane
      CHECK (trust_threshold_high > trust_threshold_medium
             AND trust_threshold_high <= 1.0
             AND trust_threshold_medium >= 0.0)
);

-- Backfill row for every existing tenant so lookups never miss.
INSERT INTO tenant_agent_settings (tenant_id)
SELECT id FROM tenants
ON CONFLICT (tenant_id) DO NOTHING;

-- Per-release CI verdict flag (Q3): default TRUE = post-agent verdict counts
ALTER TABLE release_decisions
    ADD COLUMN IF NOT EXISTS agent_verdict_counts BOOLEAN NOT NULL DEFAULT TRUE;

COMMIT;
