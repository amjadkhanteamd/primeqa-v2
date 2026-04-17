-- PrimeQA Migration 021: Scheduled runs.
--
-- Per Q5: v1 schedules only test suites. cron_expr is standard 5-field cron
-- (parsed by croniter). max_silence_hours powers the dead-man's-switch alert
-- (super admins are notified if a schedule should have fired but hasn't).

BEGIN;

CREATE TABLE IF NOT EXISTS scheduled_runs (
    id                   SERIAL PRIMARY KEY,
    tenant_id            INTEGER     NOT NULL REFERENCES tenants(id),
    suite_id             INTEGER     NOT NULL REFERENCES test_suites(id) ON DELETE CASCADE,
    environment_id       INTEGER     NOT NULL REFERENCES environments(id),
    cron_expr            VARCHAR(100) NOT NULL,
    preset_label         VARCHAR(40),
    priority             VARCHAR(20) NOT NULL DEFAULT 'normal',
    enabled              BOOLEAN     NOT NULL DEFAULT true,
    max_silence_hours    INTEGER,
    next_fire_at         TIMESTAMPTZ,
    last_fired_at        TIMESTAMPTZ,
    last_run_id          INTEGER     REFERENCES pipeline_runs(id) ON DELETE SET NULL,
    created_by           INTEGER     NOT NULL REFERENCES users(id),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT scheduled_runs_priority_ck
      CHECK (priority IN ('normal','high','critical'))
);

-- Scheduler poll uses this partial index so it scans only due rows
CREATE INDEX IF NOT EXISTS idx_scheduled_runs_due
    ON scheduled_runs(next_fire_at)
    WHERE enabled = true;

CREATE INDEX IF NOT EXISTS idx_scheduled_runs_tenant
    ON scheduled_runs(tenant_id, enabled);

COMMIT;
