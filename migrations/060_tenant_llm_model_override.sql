-- 060: per-tenant LLM model override (superadmin picker, /settings/llm-usage).
-- NULL = no override — the tenant follows existing routing untouched
-- (explicit pin > tier policy > task default). A non-NULL value is the exact
-- model id ALL the tenant's LLM tasks run on (except the two entity_summary_*
-- tasks, which stay on the platform SUMMARY_MODEL — their enrichment gate
-- hashes the model id and cannot see tenant policy).
-- Precedence-of-record: primeqa/intelligence/llm/router.py module docstring.
-- Unlike migrations 054/059 this column IS ORM-mapped (load_tenant_config
-- builds TenantPolicy from the ORM row on the per-call hot path; an unmapped
-- column would force a second query). Safe because deploy is MIGRATE-FIRST:
-- apply this file to Railway and verify before pushing the code.
ALTER TABLE tenant_agent_settings
  ADD COLUMN IF NOT EXISTS llm_model_override VARCHAR(64);
