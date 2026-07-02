-- 059: D-300 — the per-tenant bva-boundary authoring flag (default OFF).
-- When ON, S3 generation authors a boundary probe set (firing + just-inside)
-- for single-threshold-numeric prohibition claims and stamps
-- strategy_kind='bva' (the D-285 column) so the claim routes run-all.
-- Precedent: llm_enable_domain_packs (migration 049) for the column;
-- repair_auto_apply (migration 054) for the ACCESS pattern — the column is
-- DELIBERATELY NOT mapped in the ORM (an unmigrated ORM column would break
-- every tenant_agent_settings load, which the LLM gateway does per call).
-- It is read best-effort via raw SQL (generation/consumer.py); a missing
-- column reads as False (the feature stays dormant), so the code deploys
-- safely BEFORE this migration runs. Apply at arm time (D-300 S4).
ALTER TABLE tenant_agent_settings
  ADD COLUMN IF NOT EXISTS llm_enable_bva_boundaries BOOLEAN NOT NULL DEFAULT false;
