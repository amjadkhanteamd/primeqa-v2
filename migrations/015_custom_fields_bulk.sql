-- PrimeQA Migration 015: Custom fields, step templates, parametrization
BEGIN;

CREATE TABLE custom_fields (
    id          serial PRIMARY KEY,
    tenant_id   integer      NOT NULL REFERENCES tenants(id),
    entity_type varchar(30)  NOT NULL
                  CHECK (entity_type IN ('test_case', 'test_case_version', 'release', 'suite')),
    name        varchar(100) NOT NULL,
    field_type  varchar(20)  NOT NULL
                  CHECK (field_type IN ('text', 'number', 'date', 'select', 'multiselect', 'user')),
    options     jsonb        NOT NULL DEFAULT '[]'::jsonb,
    required    boolean      NOT NULL DEFAULT false,
    position    integer      NOT NULL DEFAULT 0,
    created_at  timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT custom_fields_unique UNIQUE (tenant_id, entity_type, name)
);

CREATE TABLE custom_field_values (
    id              serial PRIMARY KEY,
    custom_field_id integer NOT NULL REFERENCES custom_fields(id) ON DELETE CASCADE,
    entity_id       integer NOT NULL,
    value           jsonb,
    CONSTRAINT custom_field_values_unique UNIQUE (custom_field_id, entity_id)
);

CREATE INDEX idx_custom_field_values_entity ON custom_field_values(custom_field_id, entity_id);

CREATE TABLE step_templates (
    id          serial PRIMARY KEY,
    tenant_id   integer      NOT NULL REFERENCES tenants(id),
    name        varchar(255) NOT NULL,
    description text,
    steps       jsonb        NOT NULL DEFAULT '[]'::jsonb,
    created_by  integer      NOT NULL REFERENCES users(id),
    usage_count integer      NOT NULL DEFAULT 0,
    created_at  timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT step_templates_tenant_name_unique UNIQUE (tenant_id, name)
);

CREATE TABLE test_case_parameter_sets (
    id                   serial PRIMARY KEY,
    test_case_version_id integer      NOT NULL REFERENCES test_case_versions(id) ON DELETE CASCADE,
    name                 varchar(100) NOT NULL,
    parameters           jsonb        NOT NULL DEFAULT '{}'::jsonb,
    is_default           boolean      NOT NULL DEFAULT false,
    position             integer      NOT NULL DEFAULT 0,
    CONSTRAINT test_case_parameter_sets_unique UNIQUE (test_case_version_id, name)
);

ALTER TABLE run_test_results ADD COLUMN parameter_set_id integer REFERENCES test_case_parameter_sets(id);
CREATE INDEX idx_run_test_results_param_set ON run_test_results(parameter_set_id) WHERE parameter_set_id IS NOT NULL;

COMMIT;
