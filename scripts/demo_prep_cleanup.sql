-- Demo-prep safe DB cleanup — 2026-04-22.
--
-- Transactional; review \gset output before COMMIT.
-- Applied against tenant_id=1 (demo/dev tenant).
--
-- Scope:
--   1. Stale generation_jobs (completed/failed/cancelled older than 7 days)
--   2. Orphan test_cases (non-deleted with dangling requirement_id)
--   3. Abandoned pipeline_runs (>14d, zero tests, no results)
--   4. Fixture-only permission sets (_rp_*, _rd_*, _test_*, pytest_*, fixture_*)
--   5. Stale shared_dashboard_links (>30d, not revoked)
--   6. Stale in-flight generation_jobs (claimed >24h with no heartbeat)
--
-- KEEPS (intentional):
--   * Soft-deleted test_cases + their versions (supersession history)
--   * generation_batches (cost + LLM provenance attribution)
--   * Seeded behaviour_facts (system init data, not test writes)
--   * Fixture users / environments (tests still reference them)
--   * System-seeded permission_sets (per migration 039)

BEGIN;

\echo ---- Category 1: stale generation_jobs >7d ----
WITH deleted AS (
  DELETE FROM generation_jobs
   WHERE tenant_id = 1
     AND status IN ('completed','failed','cancelled')
     AND completed_at < NOW() - INTERVAL '7 days'
   RETURNING id
) SELECT COUNT(*) AS deleted FROM deleted;

\echo ---- Category 2: orphan test_cases (bad requirement_id) ----
WITH orphans AS (
  UPDATE test_cases
     SET deleted_at = NOW(),
         deleted_by = (SELECT id FROM users WHERE role='superadmin' AND tenant_id=1 LIMIT 1)
   WHERE tenant_id = 1
     AND deleted_at IS NULL
     AND requirement_id IS NOT NULL
     AND NOT EXISTS (SELECT 1 FROM requirements r
                       WHERE r.id = test_cases.requirement_id
                         AND r.deleted_at IS NULL)
   RETURNING id
) SELECT COUNT(*) AS soft_deleted FROM orphans;

\echo ---- Category 3: abandoned pipeline_runs >14d, 0 tests ----
WITH abandoned AS (
  DELETE FROM pipeline_runs
   WHERE tenant_id = 1
     AND queued_at < NOW() - INTERVAL '14 days'
     AND total_tests = 0
     AND NOT EXISTS (SELECT 1 FROM run_test_results rtr
                       WHERE rtr.run_id = pipeline_runs.id)
   RETURNING id
) SELECT COUNT(*) AS deleted FROM abandoned;

\echo ---- Category 4: fixture-only permission sets ----
WITH targets AS (
  SELECT id FROM permission_sets
   WHERE tenant_id = 1
     AND is_system = false
     AND (api_name LIKE '\_rp\_%' ESCAPE '\'
          OR api_name LIKE '\_rd\_%' ESCAPE '\'
          OR api_name LIKE '\_test\_%' ESCAPE '\'
          OR api_name LIKE 'pytest\_%' ESCAPE '\'
          OR api_name LIKE 'fixture\_%' ESCAPE '\')
),
clr AS (
  DELETE FROM user_permission_sets
   WHERE permission_set_id IN (SELECT id FROM targets)
   RETURNING user_id
),
drp AS (
  DELETE FROM permission_sets
   WHERE id IN (SELECT id FROM targets)
   RETURNING id
) SELECT (SELECT COUNT(*) FROM drp) AS perm_sets_dropped,
         (SELECT COUNT(*) FROM clr) AS user_links_cleared;

\echo ---- Category 5: stale shared_dashboard_links >30d ----
WITH revoked AS (
  UPDATE shared_dashboard_links
     SET revoked_at = NOW()
   WHERE tenant_id = 1
     AND revoked_at IS NULL
     AND created_at < NOW() - INTERVAL '30 days'
   RETURNING id
) SELECT COUNT(*) AS revoked FROM revoked;

\echo ---- Category 6: stale in-flight generation_jobs >24h no heartbeat ----
WITH reaped AS (
  UPDATE generation_jobs
     SET status = 'failed',
         error_code = 'STALE_CLAIMED',
         error_message = 'Claimed row stale >24h with no heartbeat; reaped in demo-prep.',
         completed_at = NOW()
   WHERE tenant_id = 1
     AND status IN ('queued','claimed','running')
     AND (heartbeat_at IS NULL OR heartbeat_at < NOW() - INTERVAL '24 hours')
   RETURNING id
) SELECT COUNT(*) AS reaped FROM reaped;

COMMIT;
