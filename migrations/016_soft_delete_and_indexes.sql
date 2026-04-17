-- PrimeQA Migration 016: Soft delete, optimistic-lock columns, and indexes for
-- list/search/sort on all test-management entities.
--
-- Idempotent: uses IF NOT EXISTS throughout so it can be re-run without error.
--
-- Changes:
--   - deleted_at/deleted_by columns on: test_cases, requirements, test_suites,
--     sections, ba_reviews, metadata_impacts
--   - version INT NOT NULL DEFAULT 1 on requirements, test_suites, sections,
--     ba_reviews (test_cases.version already exists)
--   - pg_trgm extension + GIN trigram indexes for fuzzy search
--   - Partial indexes on (tenant_id) WHERE deleted_at IS NULL
--   - Composite indexes (tenant_id, status, updated_at DESC) where applicable
--   - FK / filter column indexes (owner_id, jira_key, assigned_to, ...)

BEGIN;

-- ---- pg_trgm for fuzzy ILIKE / trigram search ---------------------------------
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---- Soft-delete columns (idempotent) ----------------------------------------
ALTER TABLE test_cases        ADD COLUMN IF NOT EXISTS deleted_at timestamptz;
ALTER TABLE test_cases        ADD COLUMN IF NOT EXISTS deleted_by integer REFERENCES users(id);

ALTER TABLE requirements      ADD COLUMN IF NOT EXISTS deleted_at timestamptz;
ALTER TABLE requirements      ADD COLUMN IF NOT EXISTS deleted_by integer REFERENCES users(id);

ALTER TABLE test_suites       ADD COLUMN IF NOT EXISTS deleted_at timestamptz;
ALTER TABLE test_suites       ADD COLUMN IF NOT EXISTS deleted_by integer REFERENCES users(id);

ALTER TABLE sections          ADD COLUMN IF NOT EXISTS deleted_at timestamptz;
ALTER TABLE sections          ADD COLUMN IF NOT EXISTS deleted_by integer REFERENCES users(id);

ALTER TABLE ba_reviews        ADD COLUMN IF NOT EXISTS deleted_at timestamptz;
ALTER TABLE ba_reviews        ADD COLUMN IF NOT EXISTS deleted_by integer REFERENCES users(id);

ALTER TABLE metadata_impacts  ADD COLUMN IF NOT EXISTS deleted_at timestamptz;
ALTER TABLE metadata_impacts  ADD COLUMN IF NOT EXISTS deleted_by integer REFERENCES users(id);

-- ---- Optimistic-lock version columns -----------------------------------------
ALTER TABLE requirements      ADD COLUMN IF NOT EXISTS version integer NOT NULL DEFAULT 1;
ALTER TABLE test_suites       ADD COLUMN IF NOT EXISTS version integer NOT NULL DEFAULT 1;
ALTER TABLE sections          ADD COLUMN IF NOT EXISTS version integer NOT NULL DEFAULT 1;
ALTER TABLE ba_reviews        ADD COLUMN IF NOT EXISTS version integer NOT NULL DEFAULT 1;

-- ---- updated_at backfill for tables that lack it -----------------------------
ALTER TABLE sections          ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE ba_reviews        ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE metadata_impacts  ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

-- ---- Partial indexes: scan only active rows ----------------------------------
CREATE INDEX IF NOT EXISTS idx_test_cases_active       ON test_cases       (tenant_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_requirements_active     ON requirements     (tenant_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_test_suites_active      ON test_suites      (tenant_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_sections_active         ON sections         (tenant_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_ba_reviews_active       ON ba_reviews       (tenant_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_metadata_impacts_active ON metadata_impacts (test_case_id) WHERE deleted_at IS NULL;

-- ---- Composite indexes for list ordering + status filter ---------------------
CREATE INDEX IF NOT EXISTS idx_test_cases_list     ON test_cases   (tenant_id, status, updated_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_requirements_list   ON requirements (tenant_id, updated_at DESC)         WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_test_suites_list    ON test_suites  (tenant_id, updated_at DESC)         WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_sections_list       ON sections     (tenant_id, updated_at DESC)         WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_ba_reviews_list     ON ba_reviews   (tenant_id, status, created_at DESC) WHERE deleted_at IS NULL;

-- ---- Filter column indexes (idempotent) --------------------------------------
-- test_cases_owner / _requirement / _section already exist on Railway (earlier migration)
CREATE INDEX IF NOT EXISTS idx_test_cases_owner          ON test_cases   (owner_id)        WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_test_cases_requirement    ON test_cases   (requirement_id)  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_test_cases_section        ON test_cases   (section_id)      WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_requirements_jira_key     ON requirements (jira_key)        WHERE deleted_at IS NULL AND jira_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_requirements_section      ON requirements (section_id)      WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_ba_reviews_assigned_to    ON ba_reviews   (assigned_to)     WHERE deleted_at IS NULL;

-- ---- GIN trigram indexes for fuzzy search -----------------------------------
CREATE INDEX IF NOT EXISTS idx_test_cases_title_trgm     ON test_cases   USING gin (title       gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_requirements_summary_trgm ON requirements USING gin (jira_summary gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_requirements_jira_trgm    ON requirements USING gin (jira_key    gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_test_suites_name_trgm     ON test_suites  USING gin (name        gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_sections_name_trgm        ON sections     USING gin (name        gin_trgm_ops);

COMMIT;
