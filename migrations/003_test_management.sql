-- PrimeQA Migration 003: Test Management
-- Covers spec sections 3.1 through 3.8
-- Tables: sections, requirements, test_cases, test_case_versions,
--         test_suites, suite_test_cases, ba_reviews, metadata_impacts
-- Also defines bump_version() trigger for optimistic concurrency on test_cases.

BEGIN;

-- ============================================================
-- 3.1 sections
-- ============================================================
CREATE TABLE sections (
    id           serial PRIMARY KEY,
    tenant_id    integer      NOT NULL REFERENCES tenants(id),
    parent_id    integer      REFERENCES sections(id) ON DELETE CASCADE,
    name         varchar(255) NOT NULL,
    description  text,
    position     integer      NOT NULL DEFAULT 0,
    created_by   integer      NOT NULL REFERENCES users(id),
    created_at   timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX idx_sections_tenant_parent ON sections(tenant_id, parent_id, position);

-- ============================================================
-- 3.2 requirements
-- ============================================================
CREATE TABLE requirements (
    id                    serial PRIMARY KEY,
    tenant_id             integer      NOT NULL REFERENCES tenants(id),
    section_id            integer      NOT NULL REFERENCES sections(id),
    source                varchar(20)  NOT NULL
                            CHECK (source IN ('jira', 'manual')),
    jira_key              varchar(50),
    jira_summary          varchar(500),
    jira_description      text,
    acceptance_criteria   text,
    jira_version          integer      NOT NULL DEFAULT 0,
    is_stale              boolean      NOT NULL DEFAULT false,
    jira_last_synced      timestamptz,
    created_by            integer      NOT NULL REFERENCES users(id),
    created_at            timestamptz  NOT NULL DEFAULT now(),
    updated_at            timestamptz  NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX idx_requirements_tenant_jira_key
    ON requirements(tenant_id, jira_key)
    WHERE jira_key IS NOT NULL;
CREATE INDEX idx_requirements_section ON requirements(section_id);
CREATE INDEX idx_requirements_stale ON requirements(tenant_id) WHERE is_stale = true;

-- ============================================================
-- 3.3 test_cases
-- ============================================================
-- Note: current_version_id FK is added after test_case_versions is created,
-- since there is a circular dependency between the two tables.
CREATE TABLE test_cases (
    id                  serial PRIMARY KEY,
    tenant_id           integer      NOT NULL REFERENCES tenants(id),
    requirement_id      integer      REFERENCES requirements(id),
    section_id          integer      REFERENCES sections(id),
    title               varchar(500) NOT NULL,
    owner_id            integer      NOT NULL REFERENCES users(id),
    visibility          varchar(20)  NOT NULL DEFAULT 'private'
                          CHECK (visibility IN ('private', 'shared')),
    status              varchar(20)  NOT NULL DEFAULT 'draft'
                          CHECK (status IN ('draft', 'approved', 'active')),
    current_version_id  integer,
    created_by          integer      NOT NULL REFERENCES users(id),
    updated_at          timestamptz  NOT NULL DEFAULT now(),
    version             integer      NOT NULL DEFAULT 1,
    CONSTRAINT test_cases_anchor_check
        CHECK (requirement_id IS NOT NULL OR section_id IS NOT NULL)
);

CREATE INDEX idx_test_cases_tenant_status ON test_cases(tenant_id, status);
CREATE INDEX idx_test_cases_requirement ON test_cases(requirement_id) WHERE requirement_id IS NOT NULL;
CREATE INDEX idx_test_cases_section ON test_cases(section_id) WHERE section_id IS NOT NULL;
CREATE INDEX idx_test_cases_owner ON test_cases(owner_id);

-- Optimistic concurrency: bump version counter on UPDATE.
CREATE OR REPLACE FUNCTION bump_version() RETURNS trigger AS $$
BEGIN
    NEW.version := OLD.version + 1;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER test_cases_bump_version
    BEFORE UPDATE ON test_cases
    FOR EACH ROW
    EXECUTE FUNCTION bump_version();

-- ============================================================
-- 3.4 test_case_versions
-- ============================================================
CREATE TABLE test_case_versions (
    id                    serial PRIMARY KEY,
    test_case_id          integer      NOT NULL REFERENCES test_cases(id) ON DELETE CASCADE,
    version_number        integer      NOT NULL,
    metadata_version_id   integer      NOT NULL REFERENCES meta_versions(id),
    steps                 jsonb        NOT NULL DEFAULT '[]'::jsonb,
    expected_results      jsonb        NOT NULL DEFAULT '[]'::jsonb,
    preconditions         jsonb        NOT NULL DEFAULT '[]'::jsonb,
    generation_method     varchar(20)  NOT NULL
                            CHECK (generation_method IN ('ai', 'manual', 'regenerated')),
    confidence_score      double precision,
    referenced_entities   jsonb        NOT NULL DEFAULT '[]'::jsonb,
    created_by            integer      NOT NULL REFERENCES users(id),
    created_at            timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT test_case_versions_case_number_unique UNIQUE (test_case_id, version_number)
);

CREATE INDEX idx_test_case_versions_case ON test_case_versions(test_case_id, version_number DESC);
CREATE INDEX idx_test_case_versions_meta ON test_case_versions(metadata_version_id);
CREATE INDEX idx_test_case_versions_referenced_entities
    ON test_case_versions USING gin (referenced_entities);

-- Now add the deferred FK from test_cases.current_version_id
ALTER TABLE test_cases
    ADD CONSTRAINT fk_test_cases_current_version
    FOREIGN KEY (current_version_id) REFERENCES test_case_versions(id);

-- ============================================================
-- 3.5 test_suites
-- ============================================================
CREATE TABLE test_suites (
    id           serial PRIMARY KEY,
    tenant_id    integer      NOT NULL REFERENCES tenants(id),
    name         varchar(255) NOT NULL,
    description  text,
    suite_type   varchar(30)  NOT NULL
                   CHECK (suite_type IN ('regression', 'smoke', 'sprint', 'custom')),
    created_by   integer      NOT NULL REFERENCES users(id),
    created_at   timestamptz  NOT NULL DEFAULT now(),
    updated_at   timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX idx_test_suites_tenant ON test_suites(tenant_id);

-- ============================================================
-- 3.6 suite_test_cases
-- ============================================================
CREATE TABLE suite_test_cases (
    id             serial PRIMARY KEY,
    suite_id       integer NOT NULL REFERENCES test_suites(id) ON DELETE CASCADE,
    test_case_id   integer NOT NULL REFERENCES test_cases(id) ON DELETE CASCADE,
    position       integer NOT NULL DEFAULT 0,
    CONSTRAINT suite_test_cases_unique UNIQUE (suite_id, test_case_id)
);

CREATE INDEX idx_suite_test_cases_suite ON suite_test_cases(suite_id, position);
CREATE INDEX idx_suite_test_cases_case ON suite_test_cases(test_case_id);

-- ============================================================
-- 3.7 ba_reviews
-- ============================================================
CREATE TABLE ba_reviews (
    id                     serial PRIMARY KEY,
    tenant_id              integer      NOT NULL REFERENCES tenants(id),
    test_case_version_id   integer      NOT NULL REFERENCES test_case_versions(id) ON DELETE CASCADE,
    assigned_to            integer      NOT NULL REFERENCES users(id),
    reviewed_by            integer      REFERENCES users(id),
    status                 varchar(20)  NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending', 'approved', 'rejected', 'needs_edit')),
    feedback               text,
    reviewed_at            timestamptz,
    created_at             timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX idx_ba_reviews_assigned ON ba_reviews(assigned_to, status);
CREATE INDEX idx_ba_reviews_version ON ba_reviews(test_case_version_id);
CREATE INDEX idx_ba_reviews_tenant_pending ON ba_reviews(tenant_id) WHERE status = 'pending';

-- ============================================================
-- 3.8 metadata_impacts
-- ============================================================
CREATE TABLE metadata_impacts (
    id                    serial PRIMARY KEY,
    new_meta_version_id   integer       NOT NULL REFERENCES meta_versions(id) ON DELETE CASCADE,
    prev_meta_version_id  integer       NOT NULL REFERENCES meta_versions(id) ON DELETE CASCADE,
    test_case_id          integer       NOT NULL REFERENCES test_cases(id) ON DELETE CASCADE,
    impact_type           varchar(30)   NOT NULL
                            CHECK (impact_type IN (
                                'field_removed', 'field_added', 'field_changed',
                                'vr_changed', 'flow_changed', 'trigger_changed'
                            )),
    entity_ref            varchar(255)  NOT NULL,
    change_details        jsonb         NOT NULL DEFAULT '{}'::jsonb,
    resolution            varchar(20)   NOT NULL DEFAULT 'pending'
                            CHECK (resolution IN ('pending', 'regenerated', 'edited', 'dismissed')),
    resolved_by           integer       REFERENCES users(id),
    resolved_at           timestamptz,
    created_at            timestamptz   NOT NULL DEFAULT now()
);

CREATE INDEX idx_metadata_impacts_new_version ON metadata_impacts(new_meta_version_id);
CREATE INDEX idx_metadata_impacts_test_case ON metadata_impacts(test_case_id);
CREATE INDEX idx_metadata_impacts_pending
    ON metadata_impacts(new_meta_version_id) WHERE resolution = 'pending';

COMMIT;
