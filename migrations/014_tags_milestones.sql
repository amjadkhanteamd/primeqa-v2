-- PrimeQA Migration 014: Tags and Milestones
BEGIN;

CREATE TABLE tags (
    id         serial PRIMARY KEY,
    tenant_id  integer      NOT NULL REFERENCES tenants(id),
    name       varchar(100) NOT NULL,
    color      varchar(20)  DEFAULT 'gray',
    created_by integer      NOT NULL REFERENCES users(id),
    created_at timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT tags_tenant_name_unique UNIQUE (tenant_id, name)
);

CREATE INDEX idx_tags_tenant ON tags(tenant_id);

CREATE TABLE test_case_tags (
    id           serial PRIMARY KEY,
    test_case_id integer NOT NULL REFERENCES test_cases(id) ON DELETE CASCADE,
    tag_id       integer NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    CONSTRAINT test_case_tags_unique UNIQUE (test_case_id, tag_id)
);

CREATE INDEX idx_test_case_tags_tc ON test_case_tags(test_case_id);
CREATE INDEX idx_test_case_tags_tag ON test_case_tags(tag_id);

CREATE TABLE milestones (
    id          serial PRIMARY KEY,
    tenant_id   integer      NOT NULL REFERENCES tenants(id),
    name        varchar(255) NOT NULL,
    description text,
    due_date    date,
    status      varchar(20)  NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active', 'completed', 'archived')),
    created_by  integer      NOT NULL REFERENCES users(id),
    created_at  timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT milestones_tenant_name_unique UNIQUE (tenant_id, name)
);

CREATE INDEX idx_milestones_tenant ON milestones(tenant_id);

CREATE TABLE milestone_suites (
    id           serial PRIMARY KEY,
    milestone_id integer     NOT NULL REFERENCES milestones(id) ON DELETE CASCADE,
    suite_id     integer     NOT NULL REFERENCES test_suites(id) ON DELETE CASCADE,
    added_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT milestone_suites_unique UNIQUE (milestone_id, suite_id)
);

COMMIT;
