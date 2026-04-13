-- PrimeQA Migration 002: Relational Metadata (Versioned)
-- Covers spec sections 2.1 through 2.7
-- Tables: meta_versions, meta_objects, meta_fields, meta_validation_rules,
--         meta_flows, meta_triggers, meta_record_types
-- Also adds the deferred FK from environments.current_meta_version_id.

BEGIN;

-- ============================================================
-- 2.1 meta_versions
-- ============================================================
CREATE TABLE meta_versions (
    id              serial PRIMARY KEY,
    environment_id  integer      NOT NULL REFERENCES environments(id) ON DELETE CASCADE,
    version_label   varchar(20)  NOT NULL,
    snapshot_hash   varchar(64),
    status          varchar(20)  NOT NULL DEFAULT 'in_progress'
                      CHECK (status IN ('in_progress', 'complete', 'partial', 'failed')),
    lifecycle       varchar(20)  NOT NULL DEFAULT 'active'
                      CHECK (lifecycle IN ('active', 'archived', 'deleted')),
    object_count    integer      NOT NULL DEFAULT 0,
    field_count     integer      NOT NULL DEFAULT 0,
    vr_count        integer      NOT NULL DEFAULT 0,
    flow_count      integer      NOT NULL DEFAULT 0,
    trigger_count   integer      NOT NULL DEFAULT 0,
    started_at      timestamptz  NOT NULL DEFAULT now(),
    completed_at    timestamptz,
    CONSTRAINT meta_versions_env_label_unique UNIQUE (environment_id, version_label)
);

CREATE INDEX idx_meta_versions_env_lifecycle
    ON meta_versions(environment_id, lifecycle, started_at DESC);
CREATE INDEX idx_meta_versions_env_active
    ON meta_versions(environment_id, started_at DESC)
    WHERE lifecycle = 'active' AND status = 'complete';

-- Deferred FK from migration 001: environments.current_meta_version_id → meta_versions.id
ALTER TABLE environments
    ADD CONSTRAINT fk_environments_current_meta_version
    FOREIGN KEY (current_meta_version_id) REFERENCES meta_versions(id);

-- ============================================================
-- 2.2 meta_objects
-- ============================================================
CREATE TABLE meta_objects (
    id               serial PRIMARY KEY,
    meta_version_id  integer      NOT NULL REFERENCES meta_versions(id) ON DELETE CASCADE,
    api_name         varchar(255) NOT NULL,
    label            varchar(255),
    key_prefix       varchar(5),
    is_custom        boolean      NOT NULL DEFAULT false,
    is_queryable     boolean      NOT NULL DEFAULT true,
    is_createable    boolean      NOT NULL DEFAULT true,
    is_updateable    boolean      NOT NULL DEFAULT true,
    is_deletable     boolean      NOT NULL DEFAULT true,
    CONSTRAINT meta_objects_version_apiname_unique UNIQUE (meta_version_id, api_name)
);

-- ============================================================
-- 2.3 meta_fields
-- ============================================================
CREATE TABLE meta_fields (
    id               serial PRIMARY KEY,
    meta_version_id  integer      NOT NULL REFERENCES meta_versions(id) ON DELETE CASCADE,
    meta_object_id   integer      NOT NULL REFERENCES meta_objects(id) ON DELETE CASCADE,
    api_name         varchar(255) NOT NULL,
    label            varchar(255),
    field_type       varchar(50)  NOT NULL,
    is_required      boolean      NOT NULL DEFAULT false,
    is_custom        boolean      NOT NULL DEFAULT false,
    is_createable    boolean      NOT NULL DEFAULT true,
    is_updateable    boolean      NOT NULL DEFAULT true,
    reference_to     varchar(255),
    length           integer,
    precision        integer,
    scale            integer,
    picklist_values  jsonb,
    default_value    varchar(500),
    CONSTRAINT meta_fields_version_object_apiname_unique
        UNIQUE (meta_version_id, meta_object_id, api_name)
);

CREATE INDEX idx_meta_fields_object ON meta_fields(meta_object_id);

-- ============================================================
-- 2.4 meta_validation_rules
-- ============================================================
CREATE TABLE meta_validation_rules (
    id                        serial PRIMARY KEY,
    meta_version_id           integer      NOT NULL REFERENCES meta_versions(id) ON DELETE CASCADE,
    meta_object_id            integer      NOT NULL REFERENCES meta_objects(id) ON DELETE CASCADE,
    rule_name                 varchar(255) NOT NULL,
    error_condition_formula   text,
    error_message             text,
    is_active                 boolean      NOT NULL DEFAULT true
);

CREATE INDEX idx_meta_vr_version_object
    ON meta_validation_rules(meta_version_id, meta_object_id);

-- ============================================================
-- 2.5 meta_flows
-- ============================================================
CREATE TABLE meta_flows (
    id                 serial PRIMARY KEY,
    meta_version_id    integer      NOT NULL REFERENCES meta_versions(id) ON DELETE CASCADE,
    api_name           varchar(255) NOT NULL,
    label              varchar(255),
    flow_type          varchar(50)  NOT NULL
                         CHECK (flow_type IN ('autolaunched', 'record_triggered', 'screen', 'process_builder')),
    trigger_object     varchar(255),
    trigger_event      varchar(50)
                         CHECK (trigger_event IS NULL OR trigger_event IN ('create', 'update', 'delete', 'create_or_update')),
    is_active          boolean      NOT NULL DEFAULT true,
    entry_conditions   jsonb
);

CREATE INDEX idx_meta_flows_version_trigger
    ON meta_flows(meta_version_id, trigger_object);

-- ============================================================
-- 2.6 meta_triggers
-- ============================================================
CREATE TABLE meta_triggers (
    id               serial PRIMARY KEY,
    meta_version_id  integer      NOT NULL REFERENCES meta_versions(id) ON DELETE CASCADE,
    meta_object_id   integer      NOT NULL REFERENCES meta_objects(id) ON DELETE CASCADE,
    trigger_name     varchar(255) NOT NULL,
    events           varchar(255),
    is_active        boolean      NOT NULL DEFAULT true
);

CREATE INDEX idx_meta_triggers_version_object
    ON meta_triggers(meta_version_id, meta_object_id);

-- ============================================================
-- 2.7 meta_record_types
-- ============================================================
CREATE TABLE meta_record_types (
    id               serial PRIMARY KEY,
    meta_version_id  integer      NOT NULL REFERENCES meta_versions(id) ON DELETE CASCADE,
    meta_object_id   integer      NOT NULL REFERENCES meta_objects(id) ON DELETE CASCADE,
    api_name         varchar(255) NOT NULL,
    label            varchar(255),
    is_active        boolean      NOT NULL DEFAULT true,
    is_default       boolean      NOT NULL DEFAULT false
);

CREATE INDEX idx_meta_record_types_version_object
    ON meta_record_types(meta_version_id, meta_object_id);

COMMIT;
