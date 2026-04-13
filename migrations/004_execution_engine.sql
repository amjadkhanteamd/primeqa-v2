-- PrimeQA Migration 004: Execution Engine
-- Covers spec sections 4.1 through 4.9
-- Tables: pipeline_runs, pipeline_stages, run_test_results, run_step_results,
--         run_artifacts, run_created_entities, run_cleanup_attempts,
--         execution_slots, worker_heartbeats
-- Views:  v_run_queue, v_active_runs

BEGIN;

-- ============================================================
-- 4.1 pipeline_runs
-- ============================================================
CREATE TABLE pipeline_runs (
    id                       serial PRIMARY KEY,
    tenant_id                integer      NOT NULL REFERENCES tenants(id),
    environment_id           integer      NOT NULL REFERENCES environments(id),
    triggered_by             integer      NOT NULL REFERENCES users(id),
    run_type                 varchar(30)  NOT NULL
                               CHECK (run_type IN ('full', 'generate_only', 'execute_only')),
    source_type              varchar(30)  NOT NULL
                               CHECK (source_type IN ('jira_tickets', 'suite', 'requirements', 'rerun')),
    source_ids               jsonb        NOT NULL DEFAULT '[]'::jsonb,
    status                   varchar(20)  NOT NULL DEFAULT 'queued'
                               CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    priority                 varchar(20)  NOT NULL DEFAULT 'normal'
                               CHECK (priority IN ('normal', 'high', 'critical')),
    max_execution_time_sec   integer      NOT NULL DEFAULT 3600,
    cancellation_token       varchar(100) NOT NULL,
    config                   jsonb        NOT NULL DEFAULT '{}'::jsonb,
    total_tests              integer      NOT NULL DEFAULT 0,
    passed                   integer      NOT NULL DEFAULT 0,
    failed                   integer      NOT NULL DEFAULT 0,
    skipped                  integer      NOT NULL DEFAULT 0,
    error_message            text,
    queued_at                timestamptz  NOT NULL DEFAULT now(),
    started_at               timestamptz,
    completed_at             timestamptz
);

CREATE INDEX idx_pipeline_runs_tenant_status ON pipeline_runs(tenant_id, status);
CREATE INDEX idx_pipeline_runs_env_active
    ON pipeline_runs(environment_id, status)
    WHERE status IN ('queued', 'running');
CREATE INDEX idx_pipeline_runs_queue
    ON pipeline_runs(priority DESC, queued_at ASC)
    WHERE status = 'queued';
CREATE INDEX idx_pipeline_runs_triggered_by ON pipeline_runs(triggered_by);

-- ============================================================
-- 4.2 pipeline_stages
-- ============================================================
CREATE TABLE pipeline_stages (
    id                serial PRIMARY KEY,
    run_id            integer      NOT NULL REFERENCES pipeline_runs(id) ON DELETE CASCADE,
    stage_name        varchar(50)  NOT NULL
                        CHECK (stage_name IN ('metadata_refresh', 'jira_read', 'generate', 'store', 'execute', 'record')),
    stage_order       integer      NOT NULL CHECK (stage_order BETWEEN 1 AND 6),
    status            varchar(20)  NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'running', 'passed', 'failed', 'skipped')),
    input_payload     jsonb,
    output_payload    jsonb,
    attempt           integer      NOT NULL DEFAULT 1,
    max_attempts      integer      NOT NULL DEFAULT 1,
    last_error        text,
    duration_ms       integer,
    started_at        timestamptz,
    completed_at      timestamptz,
    CONSTRAINT pipeline_stages_run_order_unique UNIQUE (run_id, stage_order)
);

CREATE INDEX idx_pipeline_stages_run ON pipeline_stages(run_id, stage_order);
CREATE INDEX idx_pipeline_stages_running
    ON pipeline_stages(run_id) WHERE status = 'running';

-- ============================================================
-- 4.3 run_test_results
-- ============================================================
CREATE TABLE run_test_results (
    id                     serial PRIMARY KEY,
    run_id                 integer      NOT NULL REFERENCES pipeline_runs(id) ON DELETE CASCADE,
    test_case_id           integer      NOT NULL REFERENCES test_cases(id),
    test_case_version_id   integer      NOT NULL REFERENCES test_case_versions(id),
    environment_id         integer      NOT NULL REFERENCES environments(id),
    status                 varchar(20)  NOT NULL
                             CHECK (status IN ('passed', 'failed', 'error', 'skipped')),
    failure_type           varchar(30)
                             CHECK (failure_type IS NULL OR failure_type IN (
                                 'validation_rule', 'metadata_mismatch', 'system_error',
                                 'assertion_mismatch', 'dependency_failure'
                             )),
    failure_summary        text,
    total_steps            integer      NOT NULL DEFAULT 0,
    passed_steps           integer      NOT NULL DEFAULT 0,
    failed_steps           integer      NOT NULL DEFAULT 0,
    duration_ms            integer,
    executed_at            timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX idx_run_test_results_run ON run_test_results(run_id);
CREATE INDEX idx_run_test_results_case ON run_test_results(test_case_id);
CREATE INDEX idx_run_test_results_version ON run_test_results(test_case_version_id);
CREATE INDEX idx_run_test_results_status ON run_test_results(run_id, status);

-- ============================================================
-- 4.4 run_step_results (CORE IP)
-- ============================================================
CREATE TABLE run_step_results (
    id                     serial PRIMARY KEY,
    run_test_result_id     integer      NOT NULL REFERENCES run_test_results(id) ON DELETE CASCADE,
    step_order             integer      NOT NULL,
    step_action            varchar(20)  NOT NULL
                             CHECK (step_action IN ('create', 'update', 'query', 'verify', 'convert', 'wait', 'delete')),
    target_object          varchar(255),
    target_record_id       varchar(20),
    status                 varchar(20)  NOT NULL
                             CHECK (status IN ('passed', 'failed', 'error', 'skipped')),
    execution_state        varchar(20)  NOT NULL DEFAULT 'not_started'
                             CHECK (execution_state IN ('not_started', 'in_progress', 'partially_completed', 'completed')),
    before_state           jsonb,
    after_state            jsonb,
    field_diff             jsonb,
    api_request            jsonb,
    api_response           jsonb,
    error_message          text,
    duration_ms            integer,
    executed_at            timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX idx_run_step_results_test ON run_step_results(run_test_result_id);
CREATE INDEX idx_run_step_results_test_order
    ON run_step_results(run_test_result_id, step_order);
CREATE INDEX idx_run_step_results_failed
    ON run_step_results(run_test_result_id) WHERE status IN ('failed', 'error');

-- ============================================================
-- 4.5 run_artifacts
-- ============================================================
CREATE TABLE run_artifacts (
    id                    serial PRIMARY KEY,
    run_test_result_id    integer       NOT NULL REFERENCES run_test_results(id) ON DELETE CASCADE,
    run_step_result_id    integer       REFERENCES run_step_results(id) ON DELETE CASCADE,
    artifact_type         varchar(30)   NOT NULL
                            CHECK (artifact_type IN ('screenshot', 'log', 'debug_log', 'api_trace')),
    storage_url           varchar(1000) NOT NULL,
    filename              varchar(255),
    file_size_bytes       integer,
    captured_at           timestamptz   NOT NULL DEFAULT now()
);

CREATE INDEX idx_run_artifacts_test ON run_artifacts(run_test_result_id);
CREATE INDEX idx_run_artifacts_step ON run_artifacts(run_step_result_id);

-- ============================================================
-- 4.6 run_created_entities
-- ============================================================
CREATE TABLE run_created_entities (
    id                         serial PRIMARY KEY,
    run_id                     integer       NOT NULL REFERENCES pipeline_runs(id) ON DELETE CASCADE,
    run_step_result_id         integer       NOT NULL REFERENCES run_step_results(id) ON DELETE CASCADE,
    entity_type                varchar(255)  NOT NULL,
    sf_record_id               varchar(20)   NOT NULL,
    creation_source            varchar(30)   NOT NULL
                                 CHECK (creation_source IN ('direct', 'trigger', 'workflow', 'process_builder', 'flow')),
    logical_identifier         varchar(100),
    primeqa_idempotency_key    varchar(200),
    creation_fingerprint       varchar(64),
    parent_entity_id           integer       REFERENCES run_created_entities(id) ON DELETE SET NULL,
    cleanup_required           boolean       NOT NULL DEFAULT true,
    created_at                 timestamptz   NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX idx_run_created_entities_idempotency
    ON run_created_entities(primeqa_idempotency_key)
    WHERE primeqa_idempotency_key IS NOT NULL;
CREATE INDEX idx_run_created_entities_run ON run_created_entities(run_id);
CREATE INDEX idx_run_created_entities_step ON run_created_entities(run_step_result_id);
CREATE INDEX idx_run_created_entities_parent ON run_created_entities(parent_entity_id);
CREATE INDEX idx_run_created_entities_sf_record ON run_created_entities(entity_type, sf_record_id);
CREATE INDEX idx_run_created_entities_cleanup
    ON run_created_entities(run_id) WHERE cleanup_required = true;

-- ============================================================
-- 4.7 run_cleanup_attempts
-- ============================================================
CREATE TABLE run_cleanup_attempts (
    id                       serial PRIMARY KEY,
    run_created_entity_id    integer      NOT NULL REFERENCES run_created_entities(id) ON DELETE CASCADE,
    attempt_number           integer      NOT NULL,
    status                   varchar(20)  NOT NULL
                               CHECK (status IN ('success', 'failed', 'skipped')),
    failure_reason           text,
    failure_type             varchar(30)
                               CHECK (failure_type IS NULL OR failure_type IN (
                                   'validation_rule', 'dependency', 'permission', 'system_error'
                               )),
    api_response             jsonb,
    attempted_at             timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT run_cleanup_attempts_entity_attempt_unique UNIQUE (run_created_entity_id, attempt_number)
);

CREATE INDEX idx_run_cleanup_attempts_entity ON run_cleanup_attempts(run_created_entity_id);

-- ============================================================
-- 4.8 execution_slots
-- ============================================================
CREATE TABLE execution_slots (
    id               serial PRIMARY KEY,
    environment_id   integer      NOT NULL REFERENCES environments(id),
    run_id           integer      NOT NULL REFERENCES pipeline_runs(id) ON DELETE CASCADE,
    acquired_at      timestamptz  NOT NULL DEFAULT now(),
    released_at      timestamptz
);

CREATE INDEX idx_execution_slots_env_held
    ON execution_slots(environment_id) WHERE released_at IS NULL;
CREATE INDEX idx_execution_slots_run ON execution_slots(run_id);

-- ============================================================
-- 4.9 worker_heartbeats
-- ============================================================
CREATE TABLE worker_heartbeats (
    id                serial PRIMARY KEY,
    worker_id         varchar(100) NOT NULL UNIQUE,
    status            varchar(20)  NOT NULL DEFAULT 'alive'
                        CHECK (status IN ('alive', 'dead')),
    current_run_id    integer      REFERENCES pipeline_runs(id) ON DELETE SET NULL,
    current_stage     varchar(50),
    last_heartbeat    timestamptz  NOT NULL DEFAULT now(),
    started_at        timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX idx_worker_heartbeats_alive
    ON worker_heartbeats(last_heartbeat DESC) WHERE status = 'alive';

-- ============================================================
-- Views
-- ============================================================

-- v_run_queue: queued runs ordered by priority DESC, queued_at ASC,
-- with queue position and trigger author name.
CREATE OR REPLACE VIEW v_run_queue AS
SELECT
    pr.id                                AS run_id,
    pr.tenant_id,
    pr.environment_id,
    e.name                               AS environment_name,
    pr.triggered_by,
    u.full_name                          AS triggered_by_name,
    u.email                              AS triggered_by_email,
    pr.run_type,
    pr.source_type,
    pr.priority,
    pr.queued_at,
    ROW_NUMBER() OVER (
        PARTITION BY pr.environment_id
        ORDER BY
            CASE pr.priority
                WHEN 'critical' THEN 0
                WHEN 'high'     THEN 1
                ELSE 2
            END,
            pr.queued_at ASC
    ) AS position
FROM pipeline_runs pr
JOIN users u         ON u.id = pr.triggered_by
JOIN environments e  ON e.id = pr.environment_id
WHERE pr.status = 'queued';

-- v_active_runs: currently running jobs with the stage they are executing.
CREATE OR REPLACE VIEW v_active_runs AS
SELECT
    pr.id                                AS run_id,
    pr.tenant_id,
    pr.environment_id,
    e.name                               AS environment_name,
    pr.triggered_by,
    u.full_name                          AS triggered_by_name,
    pr.run_type,
    pr.source_type,
    pr.priority,
    pr.started_at,
    pr.max_execution_time_sec,
    ps.stage_name                        AS current_stage_name,
    ps.stage_order                       AS current_stage_order,
    ps.attempt                           AS current_stage_attempt,
    ps.started_at                        AS current_stage_started_at,
    wh.worker_id                         AS worker_id,
    wh.last_heartbeat                    AS worker_last_heartbeat
FROM pipeline_runs pr
JOIN users u         ON u.id = pr.triggered_by
JOIN environments e  ON e.id = pr.environment_id
LEFT JOIN LATERAL (
    SELECT stage_name, stage_order, attempt, started_at
    FROM pipeline_stages
    WHERE run_id = pr.id AND status = 'running'
    ORDER BY stage_order DESC
    LIMIT 1
) ps ON true
LEFT JOIN worker_heartbeats wh ON wh.current_run_id = pr.id AND wh.status = 'alive'
WHERE pr.status = 'running';

COMMIT;
