-- PrimeQA Migration 026: delta-sync support on meta_versions.
--
-- Adds delta_since_ts: if set, run_queued_sync uses LastModifiedDate
-- filters on Tooling API queries and only re-describes objects whose
-- FieldDefinitions changed since the given timestamp. NULL = full sync.
--
-- Used by the "Quick-refresh changed entities" button on the Run Preview
-- drift banner (F2). A quick-refresh creates a meta_version with
-- delta_since_ts = prior_mv.completed_at + parent_meta_version_id set,
-- so the resulting sync typically touches a handful of entities in
-- seconds rather than the full org in minutes.
--
-- Idempotent.

BEGIN;

ALTER TABLE meta_versions
    ADD COLUMN IF NOT EXISTS delta_since_ts timestamptz;

COMMIT;
