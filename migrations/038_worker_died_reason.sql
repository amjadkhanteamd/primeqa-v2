-- Migration 038: worker death observability.
--
-- When a worker dies — whether from Railway SIGTERM, OOM-kill, or
-- unhandled exception — we want to know which and why. Today we
-- flip status='dead' via the scheduler's reap_stale_workers (after
-- 2 min of no heartbeat), but there's no reason captured.
--
-- `died_reason` is a short string written by the worker's own shutdown
-- hook when it can (graceful SIGTERM, uncaught exception). The reaper
-- sets it to 'heartbeat_timeout' when it reaps a silently-dead worker.
--
-- `died_at` is set at the same moment as died_reason so we can tell
-- how long ago the crash happened without grepping `last_heartbeat`.
--
-- Idempotent.

BEGIN;

ALTER TABLE worker_heartbeats
    ADD COLUMN IF NOT EXISTS died_reason VARCHAR(255);

ALTER TABLE worker_heartbeats
    ADD COLUMN IF NOT EXISTS died_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_worker_heartbeats_dead
    ON worker_heartbeats (died_at DESC)
    WHERE status = 'dead';

COMMIT;
