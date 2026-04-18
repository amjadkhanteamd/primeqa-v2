-- PrimeQA Migration 027: durable run event log.
--
-- The run detail page previously showed "Waiting for steps to start..."
-- for the first 15+ seconds of every run because the in-process SSE
-- EventBus fires only within the publishing process, and Railway
-- splits web/worker/scheduler into separate services. The web-side
-- SSE endpoint only saw status/counts via a 5s DB snapshot poll \u2014
-- everything intermediate (stage transitions, OAuth fetch, resolving
-- test cases, per-step progress) was invisible.
--
-- This table is the durable sink. Worker writes a row for every
-- milestone; web SSE endpoint tails it alongside the snapshot poll.
-- Survives page refresh, worker restart, and cross-service execution.
--
-- Not a replacement for run_step_results (which stores full api_request
-- / api_response / before_state / after_state for each step) \u2014 this
-- is the human-readable timeline, deliberately small per row.
--
-- Retention: scheduler keeps at most 1000 events per run (trimming the
-- oldest). 1000 events is ~50 test cases with 20 steps each \u2014 more
-- than enough for any realistic run.
--
-- Idempotent.

BEGIN;

CREATE TABLE IF NOT EXISTS run_events (
    id           BIGSERIAL PRIMARY KEY,
    run_id       INT       NOT NULL REFERENCES pipeline_runs(id) ON DELETE CASCADE,
    tenant_id    INT       NOT NULL REFERENCES tenants(id),
    ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- kind: stage_started | stage_finished | test_started | test_finished
    --       | step_started | step_finished | log | run_started | run_finished
    kind         VARCHAR(30) NOT NULL,
    -- level: info | warn | error  (drives UI styling)
    level        VARCHAR(10) NOT NULL DEFAULT 'info',
    message      TEXT NOT NULL,
    -- Structured context for UI rendering and future queries
    -- (stage_name, test_case_id, step_order, status, duration_ms,
    --  error_summary, etc.). Never contains API bodies or credentials.
    context      JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- Tail-style list query: events for a run in chronological order,
-- often filtered to "since event_id N" for incremental SSE updates.
CREATE INDEX IF NOT EXISTS idx_run_events_run_ts
    ON run_events (run_id, ts ASC, id ASC);

-- Tenant-wide observability queries (future): "last 50 errors across
-- all runs in this tenant".
CREATE INDEX IF NOT EXISTS idx_run_events_tenant_ts
    ON run_events (tenant_id, ts DESC)
    WHERE level IN ('warn', 'error');

COMMIT;
