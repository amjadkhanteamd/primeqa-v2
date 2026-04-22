-- Migration 044: generation_jobs — async queue for test-plan generation.
--
-- Flips the Generate flow from sync (Gunicorn worker blocks 30-90s on the
-- LLM round-trip) to async (web request creates a queued row + returns
-- 202; primeqa.worker picks it up; UI polls /api/generation-jobs/:id/
-- status). See prompt 11 / the sync-to-async conversion for context.
--
-- Columns:
--   status             queued | claimed | running | completed | failed | cancelled
--   progress_pct       0-100, bumped by the worker during the run
--   progress_msg       short human-readable status line
--   heartbeat_at       worker bumps every ~10s so the scheduler can reap
--                      stalled rows (worker crashed mid-generation)
--   generation_batch_id  linkage to the batch row the worker committed
--
-- Dedup: one active (queued/claimed/running) job per
-- (requirement_id, environment_id). Enforced in application code rather
-- than a partial UNIQUE index since Postgres DDL can't express this
-- constraint cleanly; see primeqa/intelligence/generation_jobs.py.
--
-- Idempotent.

BEGIN;

CREATE TABLE IF NOT EXISTS generation_jobs (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id),
    environment_id      INTEGER NOT NULL REFERENCES environments(id),
    requirement_id      INTEGER NOT NULL REFERENCES requirements(id),
    created_by          INTEGER NOT NULL REFERENCES users(id),

    status              VARCHAR(20) NOT NULL DEFAULT 'queued',
    progress_pct        INTEGER DEFAULT 0,
    progress_msg        VARCHAR(200),

    generation_batch_id INTEGER REFERENCES generation_batches(id),
    test_case_count     INTEGER,

    error_code          VARCHAR(50),
    error_message       TEXT,

    claimed_at          TIMESTAMPTZ,
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    heartbeat_at        TIMESTAMPTZ,

    model_used          VARCHAR(100),
    tokens_used         INTEGER,

    CONSTRAINT generation_jobs_status_check CHECK (
        status IN ('queued', 'claimed', 'running', 'completed', 'failed', 'cancelled')
    )
);

CREATE INDEX IF NOT EXISTS idx_gen_jobs_status_active
    ON generation_jobs (status)
    WHERE status IN ('queued', 'claimed', 'running');

CREATE INDEX IF NOT EXISTS idx_gen_jobs_tenant
    ON generation_jobs (tenant_id, status);

-- Fast claim path: SELECT FOR UPDATE SKIP LOCKED orders by created_at.
CREATE INDEX IF NOT EXISTS idx_gen_jobs_queued_fifo
    ON generation_jobs (created_at)
    WHERE status = 'queued';

-- Fast active-job-per-requirement lookup used by dedup + the detail-
-- page "is there a job in flight?" check.
CREATE INDEX IF NOT EXISTS idx_gen_jobs_req_env_active
    ON generation_jobs (requirement_id, environment_id)
    WHERE status IN ('queued', 'claimed', 'running');

COMMIT;
