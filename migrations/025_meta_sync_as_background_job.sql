-- PrimeQA Migration 025: meta_versions becomes a background-job row.
--
-- The refresh-metadata POST stops running the sync inline; it queues the
-- work and redirects to a progress page. The Railway worker process picks
-- up queued rows, claims them with FOR UPDATE SKIP LOCKED, heartbeats, and
-- commits per-category. The scheduler service reaps rows whose heartbeat
-- has stalled. The web SSE endpoint forwards pg LISTEN/NOTIFY events with
-- a DB-snapshot fallback (already in place).
--
-- Columns added to meta_versions:
--   queued_at           timestamptz   when the user hit "Sync"
--   triggered_by        integer       user who triggered it
--   categories_requested jsonb        subset of 6 categories selected
--   worker_id           varchar(100)  id of the worker that claimed it
--   heartbeat_at        timestamptz   last heartbeat from the claiming worker
--   cancel_requested    boolean       user clicked Cancel; worker checks
--                                     between categories
--   parent_meta_version_id integer    "retry failed + skipped" points here
--
-- Status values: the existing CHECK on meta_versions.status already allows
-- 'in_progress' / 'complete' / 'failed'. Add 'queued' and 'cancelled'.
--
-- Idempotent.

BEGIN;

ALTER TABLE meta_versions
    ADD COLUMN IF NOT EXISTS queued_at              timestamptz,
    ADD COLUMN IF NOT EXISTS triggered_by           integer REFERENCES users(id),
    ADD COLUMN IF NOT EXISTS categories_requested   jsonb,
    ADD COLUMN IF NOT EXISTS worker_id              varchar(100),
    ADD COLUMN IF NOT EXISTS heartbeat_at           timestamptz,
    ADD COLUMN IF NOT EXISTS cancel_requested       boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS parent_meta_version_id integer REFERENCES meta_versions(id) ON DELETE SET NULL;

-- Back-compat: existing status CHECK only allows in_progress / complete /
-- failed. Drop + recreate with the new values the background job needs.
DO $$
DECLARE con_name text;
BEGIN
    SELECT conname INTO con_name
    FROM pg_constraint
    WHERE conrelid = 'meta_versions'::regclass
      AND contype  = 'c'
      AND pg_get_constraintdef(oid) ILIKE '%status%';
    IF con_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE meta_versions DROP CONSTRAINT %I', con_name);
    END IF;
END$$;

ALTER TABLE meta_versions ADD CONSTRAINT meta_versions_status_check
    CHECK (status IN ('queued', 'in_progress', 'complete', 'failed', 'cancelled'));

-- Worker poll: grab the oldest queued row cheaply
CREATE INDEX IF NOT EXISTS idx_meta_versions_queued
    ON meta_versions (queued_at)
    WHERE status = 'queued';

-- Reaper: find stalled in_progress rows
CREATE INDEX IF NOT EXISTS idx_meta_versions_heartbeat
    ON meta_versions (heartbeat_at)
    WHERE status = 'in_progress';

-- Single-flight guard: quickly ask "is any sync running for this env?"
CREATE INDEX IF NOT EXISTS idx_meta_versions_active_per_env
    ON meta_versions (environment_id)
    WHERE status IN ('queued', 'in_progress');

COMMIT;
