-- PrimeQA Migration 006: Groups and Connections
-- Adds: groups, group_members, group_environments, connections
-- Alters: environments (add created_by column)

BEGIN;

-- ============================================================
-- Groups
-- ============================================================
CREATE TABLE groups (
    id          serial PRIMARY KEY,
    tenant_id   integer      NOT NULL REFERENCES tenants(id),
    name        varchar(255) NOT NULL,
    description text,
    created_by  integer      NOT NULL REFERENCES users(id),
    created_at  timestamptz  NOT NULL DEFAULT now(),
    updated_at  timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT groups_tenant_name_unique UNIQUE (tenant_id, name)
);

CREATE INDEX idx_groups_tenant ON groups(tenant_id);

-- ============================================================
-- Group Members (junction: users ↔ groups)
-- ============================================================
CREATE TABLE group_members (
    id        serial PRIMARY KEY,
    group_id  integer     NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    user_id   integer     NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    added_by  integer     NOT NULL REFERENCES users(id),
    added_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT group_members_unique UNIQUE (group_id, user_id)
);

-- ============================================================
-- Group Environments (junction: environments ↔ groups)
-- ============================================================
CREATE TABLE group_environments (
    id             serial PRIMARY KEY,
    group_id       integer     NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    environment_id integer     NOT NULL REFERENCES environments(id) ON DELETE CASCADE,
    added_by       integer     NOT NULL REFERENCES users(id),
    added_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT group_environments_unique UNIQUE (group_id, environment_id)
);

CREATE INDEX idx_group_environments_env ON group_environments(environment_id);

-- ============================================================
-- Connections (external service connections: salesforce, jira, llm)
-- ============================================================
CREATE TABLE connections (
    id              serial PRIMARY KEY,
    tenant_id       integer      NOT NULL REFERENCES tenants(id),
    connection_type varchar(20)  NOT NULL
                      CHECK (connection_type IN ('salesforce', 'jira', 'llm')),
    name            varchar(255) NOT NULL,
    config          jsonb        NOT NULL DEFAULT '{}'::jsonb,
    status          varchar(20)  NOT NULL DEFAULT 'inactive'
                      CHECK (status IN ('active', 'inactive', 'error')),
    created_by      integer      NOT NULL REFERENCES users(id),
    created_at      timestamptz  NOT NULL DEFAULT now(),
    updated_at      timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT connections_tenant_name_unique UNIQUE (tenant_id, name)
);

CREATE INDEX idx_connections_tenant_type ON connections(tenant_id, connection_type);

-- ============================================================
-- ALTER environments: add created_by for ownership tracking
-- ============================================================
ALTER TABLE environments ADD COLUMN created_by integer REFERENCES users(id);
UPDATE environments SET created_by = 1 WHERE created_by IS NULL;
ALTER TABLE environments ALTER COLUMN created_by SET NOT NULL;

COMMIT;
