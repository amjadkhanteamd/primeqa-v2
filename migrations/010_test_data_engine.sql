-- PrimeQA Migration 010: Test Data Engine
-- Templates, factories, and snapshots for reliable test data.

BEGIN;

-- ============================================================
-- data_templates — reusable pre-filled object definitions
-- ============================================================
CREATE TABLE data_templates (
    id             serial PRIMARY KEY,
    tenant_id      integer      NOT NULL REFERENCES tenants(id),
    name           varchar(255) NOT NULL,
    description    text,
    object_type    varchar(255) NOT NULL,
    field_values   jsonb        NOT NULL DEFAULT '{}'::jsonb,
    created_by     integer      NOT NULL REFERENCES users(id),
    created_at     timestamptz  NOT NULL DEFAULT now(),
    updated_at     timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT data_templates_tenant_name_unique UNIQUE (tenant_id, name)
);

CREATE INDEX idx_data_templates_tenant ON data_templates(tenant_id);
CREATE INDEX idx_data_templates_object ON data_templates(object_type);

-- ============================================================
-- data_factories — generative data producers (faker-style)
-- ============================================================
CREATE TABLE data_factories (
    id             serial PRIMARY KEY,
    tenant_id      integer      NOT NULL REFERENCES tenants(id),
    name           varchar(255) NOT NULL,
    description    text,
    factory_type   varchar(30)  NOT NULL
                     CHECK (factory_type IN ('uuid', 'email', 'phone', 'name', 'company',
                                             'address', 'timestamp', 'counter', 'custom')),
    config         jsonb        NOT NULL DEFAULT '{}'::jsonb,
    created_by     integer      NOT NULL REFERENCES users(id),
    created_at     timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT data_factories_tenant_name_unique UNIQUE (tenant_id, name)
);

CREATE INDEX idx_data_factories_tenant ON data_factories(tenant_id);

-- ============================================================
-- data_snapshots — captured org state for restore
-- ============================================================
CREATE TABLE data_snapshots (
    id               serial PRIMARY KEY,
    tenant_id        integer      NOT NULL REFERENCES tenants(id),
    environment_id   integer      NOT NULL REFERENCES environments(id) ON DELETE CASCADE,
    name             varchar(255) NOT NULL,
    description      text,
    snapshot_data    jsonb        NOT NULL DEFAULT '{}'::jsonb,
    record_count     integer      NOT NULL DEFAULT 0,
    status           varchar(20)  NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'capturing', 'ready', 'restoring', 'failed')),
    created_by       integer      NOT NULL REFERENCES users(id),
    created_at       timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX idx_data_snapshots_env ON data_snapshots(environment_id);

-- ============================================================
-- test_case_data_bindings — which data templates/factories a test uses
-- ============================================================
CREATE TABLE test_case_data_bindings (
    id                       serial PRIMARY KEY,
    test_case_version_id     integer      NOT NULL REFERENCES test_case_versions(id) ON DELETE CASCADE,
    binding_key              varchar(100) NOT NULL,
    binding_type             varchar(20)  NOT NULL
                               CHECK (binding_type IN ('template', 'factory', 'snapshot')),
    reference_id             integer      NOT NULL,
    CONSTRAINT test_case_data_bindings_unique UNIQUE (test_case_version_id, binding_key)
);

CREATE INDEX idx_test_case_data_bindings_version ON test_case_data_bindings(test_case_version_id);

COMMIT;
