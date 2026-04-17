-- PrimeQA Migration 020: Per-category metadata sync status.
--
-- Replaces the old all-or-nothing meta_version refresh with a DAG of
-- independently retryable category syncs:
--   objects -> {fields, record_types} -> {validation_rules, flows, triggers}
--
-- One row per (meta_version_id, category). If the parent category fails,
-- dependents are marked 'skipped_parent_failed' (Q11).

BEGIN;

CREATE TABLE IF NOT EXISTS meta_sync_status (
    id              SERIAL PRIMARY KEY,
    meta_version_id INTEGER     NOT NULL REFERENCES meta_versions(id) ON DELETE CASCADE,
    category        VARCHAR(30) NOT NULL,
    status          VARCHAR(30) NOT NULL DEFAULT 'pending',
    items_count     INTEGER     NOT NULL DEFAULT 0,
    retry_count     INTEGER     NOT NULL DEFAULT 0,
    error_message   TEXT,
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT meta_sync_status_category_ck
      CHECK (category IN ('objects','fields','record_types',
                          'validation_rules','flows','triggers')),
    CONSTRAINT meta_sync_status_status_ck
      CHECK (status IN ('pending','running','complete','failed',
                        'skipped','skipped_parent_failed')),
    CONSTRAINT meta_sync_status_unique UNIQUE (meta_version_id, category)
);

CREATE INDEX IF NOT EXISTS idx_meta_sync_status_version
    ON meta_sync_status(meta_version_id);
CREATE INDEX IF NOT EXISTS idx_meta_sync_status_running
    ON meta_sync_status(meta_version_id, status) WHERE status IN ('pending','running');

COMMIT;
