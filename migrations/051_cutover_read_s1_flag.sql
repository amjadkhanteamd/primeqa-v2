-- Cutover Step 3 / Slice 3.1 (D-158): the per-tenant read-path switch flag.
--
-- When ON (and the tenant's S1 is synced), v1's metadata READS (generation
-- context, validator CRUDQ) route to the S1 semantic org model via
-- primeqa/metadata/accessor.py::MetadataAccessor; when OFF (the default), they
-- read meta_* unchanged. Reversible per tenant (flip the flag) — the start of
-- the parallel run (the meta_* tables stay populated as the fallback).
--
-- Idempotent (ADD COLUMN IF NOT EXISTS) per the migrations-016+ convention.
-- Public tenant_agent_settings (mirrors migrations 048 / 049 / 050).
ALTER TABLE tenant_agent_settings
    ADD COLUMN IF NOT EXISTS cutover_read_s1 BOOLEAN NOT NULL DEFAULT false;
