-- PrimeQA Migration 001: Core Platform
-- Covers spec sections 1.1 through 1.6
-- Tables: tenants, users, refresh_tokens, environments, environment_credentials, activity_log

BEGIN;

-- ============================================================
-- 1.1 tenants
-- ============================================================
CREATE TABLE tenants (
    id          serial PRIMARY KEY,
    name        varchar(255) NOT NULL,
    slug        varchar(100) NOT NULL UNIQUE,
    status      varchar(20)  NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active', 'suspended')),
    settings    jsonb        NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz  NOT NULL DEFAULT now(),
    updated_at  timestamptz  NOT NULL DEFAULT now()
);

-- ============================================================
-- 1.2 users
-- ============================================================
CREATE TABLE users (
    id              serial PRIMARY KEY,
    tenant_id       integer      NOT NULL REFERENCES tenants(id),
    email           varchar(255) NOT NULL,
    password_hash   varchar(255) NOT NULL,
    full_name       varchar(255) NOT NULL,
    role            varchar(20)  NOT NULL
                      CHECK (role IN ('admin', 'tester', 'ba', 'viewer')),
    is_active       boolean      NOT NULL DEFAULT true,
    last_login_at   timestamptz,
    created_at      timestamptz  NOT NULL DEFAULT now(),
    updated_at      timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT users_tenant_email_unique UNIQUE (tenant_id, email)
);

CREATE INDEX idx_users_tenant_active ON users(tenant_id) WHERE is_active = true;

-- ============================================================
-- 1.3 refresh_tokens
-- ============================================================
CREATE TABLE refresh_tokens (
    id          serial PRIMARY KEY,
    user_id     integer      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  varchar(255) NOT NULL UNIQUE,
    expires_at  timestamptz  NOT NULL,
    revoked     boolean      NOT NULL DEFAULT false,
    created_at  timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX idx_refresh_tokens_user ON refresh_tokens(user_id) WHERE revoked = false;

-- ============================================================
-- 1.4 environments
-- ============================================================
-- Note: current_meta_version_id is declared as plain integer here.
-- The FK to meta_versions(id) is added via ALTER TABLE in migration 002,
-- since meta_versions does not exist yet.
CREATE TABLE environments (
    id                       serial PRIMARY KEY,
    tenant_id                integer      NOT NULL REFERENCES tenants(id),
    name                     varchar(255) NOT NULL,
    env_type                 varchar(30)  NOT NULL
                               CHECK (env_type IN ('sandbox', 'uat', 'staging', 'production')),
    sf_instance_url          varchar(500) NOT NULL,
    sf_api_version           varchar(10)  NOT NULL,
    execution_policy         varchar(20)  NOT NULL DEFAULT 'full'
                               CHECK (execution_policy IN ('full', 'read_only', 'disabled')),
    capture_mode             varchar(20)  NOT NULL DEFAULT 'smart'
                               CHECK (capture_mode IN ('minimal', 'smart', 'full')),
    max_execution_slots      integer      NOT NULL DEFAULT 2,
    cleanup_mandatory        boolean      NOT NULL DEFAULT false,
    current_meta_version_id  integer,
    is_active                boolean      NOT NULL DEFAULT true,
    created_at               timestamptz  NOT NULL DEFAULT now(),
    updated_at               timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX idx_environments_tenant ON environments(tenant_id) WHERE is_active = true;

-- ============================================================
-- 1.5 environment_credentials
-- ============================================================
CREATE TABLE environment_credentials (
    id                  serial PRIMARY KEY,
    environment_id      integer       NOT NULL UNIQUE
                          REFERENCES environments(id) ON DELETE CASCADE,
    client_id           varchar(500)  NOT NULL,
    client_secret       varchar(500)  NOT NULL,
    access_token        varchar(2000),
    refresh_token       varchar(2000),
    token_expires_at    timestamptz,
    last_refreshed_at   timestamptz,
    status              varchar(20)   NOT NULL DEFAULT 'valid'
                          CHECK (status IN ('valid', 'expired', 'failed'))
);

-- ============================================================
-- 1.6 activity_log
-- ============================================================
CREATE TABLE activity_log (
    id           serial PRIMARY KEY,
    tenant_id    integer      NOT NULL REFERENCES tenants(id),
    user_id      integer      REFERENCES users(id),
    action       varchar(50)  NOT NULL,
    entity_type  varchar(50)  NOT NULL,
    entity_id    integer,
    details      jsonb        NOT NULL DEFAULT '{}'::jsonb,
    created_at   timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX idx_activity_log_created_at_desc ON activity_log(created_at DESC);
CREATE INDEX idx_activity_log_tenant_created ON activity_log(tenant_id, created_at DESC);
CREATE INDEX idx_activity_log_entity ON activity_log(entity_type, entity_id);

-- ============================================================
-- Seed data
-- ============================================================

-- Default tenant (id = 1)
INSERT INTO tenants (name, slug, status)
VALUES ('Default', 'default', 'active');

-- Admin user (id = 1)
-- Password: changeme123
-- Bcrypt hash generated with: bcrypt.hashpw(b'changeme123', bcrypt.gensalt(rounds=12))
INSERT INTO users (tenant_id, email, password_hash, full_name, role, is_active)
VALUES (
    1,
    'admin@primeqa.io',
    '$2b$12$dtJIDq1.ZDGPNnfUYehUQei8Gxa172rItQk6ZAX2tS7bqgS771UKK',
    'PrimeQA Admin',
    'admin',
    true
);

COMMIT;
