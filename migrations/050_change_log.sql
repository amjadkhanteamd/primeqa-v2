-- migrations/050_change_log.sql
--
-- Substrate 1 — Tier 0 foundation: change_log table.
--
-- Per D-023, this is the first implementation milestone for Substrate 1.
-- The SPEC's full structural commitments (entities/edges, schema-per-tenant,
-- logical_versions, UUID target_ids, GUC-based tenant assertions, Alembic)
-- are deferred. This migration ships in `public` using v2 conventions:
--   * raw SQL, idempotent
--   * INT primary keys, BIGINT for high-volume target_id
--   * explicit tenant_id column with FK to tenants(id)
--   * meta_versions.id as the version anchor (not version_seq)
--
-- Cross-row invariant NOT enforced at the DB layer:
--   change_log.tenant_id must equal meta_versions[change_log.meta_version_id].tenant_id.
--   Postgres CHECK can't reach across rows; the application writer is responsible.
--   Consider a trigger if drift is observed in practice.

BEGIN;

CREATE TABLE IF NOT EXISTS change_log (
    id BIGSERIAL PRIMARY KEY,

    -- What changed.
    -- 'updated' is for any in-place mutation including soft-deletes (is_deleted
    -- toggling true). 'deleted' is reserved for hard row removal.
    change_type VARCHAR(30) NOT NULL
        CHECK (change_type IN ('created', 'updated', 'deleted')),

    -- Which v2 table the change targets, e.g. 'meta_object', 'meta_field',
    -- 'meta_validation_rule', 'meta_flow', 'meta_trigger', 'meta_record_type'.
    target_table VARCHAR(40) NOT NULL,
    target_id BIGINT NOT NULL,

    -- Diff payload. Discipline: store the columns that drove the diff, not the
    -- full row. JSONB so the shape can evolve per target_table.
    before_state JSONB,
    after_state JSONB,
    changed_field_names TEXT[],

    -- Version anchor. We reuse v2's existing per-environment meta_versions row
    -- as the version marker. logical_versions arrives only if D-016 ships.
    -- ON DELETE RESTRICT: never silently lose change events when a meta_version
    -- is purged. If retention forces it, write a tombstone.
    meta_version_id INT NOT NULL REFERENCES meta_versions(id) ON DELETE RESTRICT,

    -- Optional sync correlation. NULL until MetadataSyncEngine starts emitting
    -- a sync_run_id and the writer threads it through.
    sync_run_id UUID,

    -- Tenant scope. v2 convention: explicit column, explicit predicates, FK
    -- cascade so tenant deletion sweeps change_log.
    tenant_id INT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Sanity: created has no before, deleted has no after, updated has both.
    -- Strict-by-default; relax only if a real sync pattern hits it.
    CONSTRAINT change_log_state_shape CHECK (
        (change_type = 'created' AND before_state IS NULL AND after_state IS NOT NULL)
        OR (change_type = 'deleted' AND after_state IS NULL AND before_state IS NOT NULL)
        OR (change_type = 'updated' AND before_state IS NOT NULL AND after_state IS NOT NULL)
    )
);

-- Hot path: time-window scan for diff_window(from, to).
CREATE INDEX IF NOT EXISTS idx_change_log_meta_version
    ON change_log (meta_version_id);

-- Targeted: "what changes hit this row" — diff_for_entities at Tier 1.
CREATE INDEX IF NOT EXISTS idx_change_log_target
    ON change_log (target_table, target_id, meta_version_id);

-- Tenant-scoped scans. Required predicate for every read; index supports it.
CREATE INDEX IF NOT EXISTS idx_change_log_tenant_meta_version
    ON change_log (tenant_id, meta_version_id);

-- Field-level "find changes that touched field X".
CREATE INDEX IF NOT EXISTS idx_change_log_field_names
    ON change_log USING GIN (changed_field_names);

-- Sync correlation. Sparse — partial index avoids bloating against NULL.
CREATE INDEX IF NOT EXISTS idx_change_log_sync_run
    ON change_log (sync_run_id) WHERE sync_run_id IS NOT NULL;

COMMIT;
