-- Migration 049: per-tenant feature flag for Domain Pack prompt augmentation.
--
-- Domain Packs are long-form prescriptive knowledge files (markdown with
-- YAML frontmatter) that match against requirement text and, when the
-- flag is ON, get injected into the test_plan_generation prompt so
-- Sonnet has concrete patterns to follow for covered domains.
--
-- Default is off per tenant; superadmin opts tenants in via
-- /settings/llm-usage (the "Packs" checkbox on the per-tenant Plan cell,
-- alongside the Story toggle from migration 048).
--
-- Attribution (which packs fired on which call) is logged into the
-- existing `llm_usage_log.context` JSONB column under key
-- `domain_packs_applied` — NO dedicated column. This mirrors the
-- story_view precedent (story_view.py writes its attribution dict the
-- same way). See primeqa/intelligence/llm/usage.py:78 for the single
-- `context` JSONB write path and gateway.py:278 for the wiring.
--
-- Idempotent (IF NOT EXISTS). Safe to re-apply on already-migrated
-- databases.

ALTER TABLE tenant_agent_settings
    ADD COLUMN IF NOT EXISTS llm_enable_domain_packs BOOLEAN NOT NULL DEFAULT false;
