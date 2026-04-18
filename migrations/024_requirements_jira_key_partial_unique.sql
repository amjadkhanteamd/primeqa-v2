-- PrimeQA Migration 024: requirements.jira_key unique is partial on live rows.
--
-- Problem: `idx_requirements_tenant_jira_key` is a plain UNIQUE index on
-- (tenant_id, jira_key), but `RequirementRepository.find_by_jira_key()`
-- filters `deleted_at IS NULL`. So when a user soft-deletes requirement
-- PROJ-123 and later tries to re-import it, the service's existence check
-- sees no live row and issues an INSERT \u2014 which trips the unique index
-- because the soft-deleted row still holds the key. User gets a 500 with
-- `duplicate key value violates unique constraint`.
--
-- Fix: drop the full unique index, replace with a partial unique index
-- that only applies to non-deleted rows. Soft-deleted rows are no longer
-- constraint-conflicting, so re-importing a previously-trashed Jira
-- requirement is now a normal INSERT.
--
-- Idempotent (safe to re-run).

BEGIN;

-- Drop the existing unique index if it's the non-partial form.
DROP INDEX IF EXISTS idx_requirements_tenant_jira_key;

-- Replace with a partial unique index that skips soft-deleted + NULL keys
-- (manual requirements have jira_key = NULL and shouldn't conflict with
-- each other either).
CREATE UNIQUE INDEX IF NOT EXISTS idx_requirements_tenant_jira_key
    ON requirements (tenant_id, jira_key)
    WHERE deleted_at IS NULL AND jira_key IS NOT NULL;

COMMIT;
