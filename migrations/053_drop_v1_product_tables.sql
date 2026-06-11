-- 053: THE v1 PRODUCT-TABLE DROP (D-221 R5) — IRREVERSIBLE.
--
-- DO NOT APPLY until ALL of (D-220):
--   1. 3 consecutive clean s4_run_schedules fires (infrastructure-clean),
--   2. the pre-drop checklist in docs/architecture/5b_predrop_checklist.md
--      re-verified on the day,
--   3. AK's explicit GO recorded in DECISIONS_LOG.
--
-- Every table verified ZERO ROWS on 2026-06-11 (D-220). CASCADE removes the
-- FK constraints that surviving tables (llm_usage_log, ba_reviews,
-- release_runs, intelligence tables, generation_jobs, user_recent_tickets)
-- still hold into this set — their columns remain as plain integers (the
-- ORM declarations were already stripped in D-221.4).

BEGIN;

DROP TABLE IF EXISTS run_events            CASCADE;
DROP TABLE IF EXISTS run_step_results      CASCADE;
DROP TABLE IF EXISTS run_test_results      CASCADE;
DROP TABLE IF EXISTS run_artifacts         CASCADE;
DROP TABLE IF EXISTS run_created_entities  CASCADE;
DROP TABLE IF EXISTS run_cleanup_attempts  CASCADE;
DROP TABLE IF EXISTS pipeline_stages       CASCADE;
DROP TABLE IF EXISTS execution_slots       CASCADE;  -- v1 concurrency slots (D-221.4 candidate, 0 rows)
DROP TABLE IF EXISTS pipeline_runs         CASCADE;
DROP TABLE IF EXISTS suite_test_cases      CASCADE;
DROP TABLE IF EXISTS milestone_suites      CASCADE;
DROP TABLE IF EXISTS scheduled_runs        CASCADE;
DROP TABLE IF EXISTS test_suites           CASCADE;
DROP TABLE IF EXISTS ba_reviews            CASCADE;  -- orphaned with its queue (0 rows)
DROP TABLE IF EXISTS test_case_tags        CASCADE;
DROP TABLE IF EXISTS test_case_parameter_sets CASCADE;
DROP TABLE IF EXISTS test_case_versions    CASCADE;
DROP TABLE IF EXISTS generation_batches    CASCADE;
DROP TABLE IF EXISTS test_cases            CASCADE;
DROP TABLE IF EXISTS generation_jobs       CASCADE;  -- v1 async-gen queue (consumers retired in D-221.3)

COMMIT;
