-- Migration 045: lint result columns on test_case_versions.
--
-- GenerationLinter (primeqa/intelligence/linter.py) runs after the LLM
-- returns a flow and before the row lands in the DB. It can:
--   - auto-fix structural issues (remove Id from create, drop formula
--     fields from update payloads, reformat ISO dates)
--   - warn on suspect values it doesn't feel confident auto-fixing
--     (picklist values not in synced metadata)
--   - block in strict mode
--
-- These columns let the UI + BA review queue show which generations
-- were modified + why. review_reason='linter_modified' (migration 042)
-- is set alongside on any TC that had a fix applied.
--
-- Idempotent.

BEGIN;

ALTER TABLE test_case_versions
    ADD COLUMN IF NOT EXISTS lint_fixes INTEGER NOT NULL DEFAULT 0;

ALTER TABLE test_case_versions
    ADD COLUMN IF NOT EXISTS lint_warnings INTEGER NOT NULL DEFAULT 0;

ALTER TABLE test_case_versions
    ADD COLUMN IF NOT EXISTS lint_details JSONB;

CREATE INDEX IF NOT EXISTS idx_tcv_lint_fixed
    ON test_case_versions (test_case_id)
    WHERE lint_fixes > 0;

COMMIT;
