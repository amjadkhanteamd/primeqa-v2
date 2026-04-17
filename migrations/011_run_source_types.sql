-- PrimeQA Migration 011: Expand pipeline_runs.source_type for multi-point triggers
BEGIN;

ALTER TABLE pipeline_runs DROP CONSTRAINT pipeline_runs_source_type_check;
ALTER TABLE pipeline_runs ADD CONSTRAINT pipeline_runs_source_type_check
    CHECK (source_type IN ('jira_tickets', 'suite', 'requirements', 'rerun', 'test_cases', 'release'));

COMMIT;
