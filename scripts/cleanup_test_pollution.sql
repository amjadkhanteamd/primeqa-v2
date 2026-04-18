-- scripts/cleanup_test_pollution.sql
-- -----------------------------------------------------------------------------
-- Cleans up duplicate sections + orphan test-case rows that accumulated from
-- integration tests running against the live Railway DB without per-run
-- teardown.
--
-- Safety audit (as of 2026-04-18, tenant_id=1):
--   * 8  "Regression Tests" root sections — all 0 active TCs, 0 suite refs
--   * 8  "Account Tests"   child sections — all 0 active TCs, 0 suite refs
--   * 4  "Hardening Section" root sections — all 0 active TCs, 0 suite refs
--   * 12 "Cleanup Test" TCs (ids 3-14, all in Cleanup Section id=4)
--   * 2  "Updated title" TCs (ids 36, 38)
--
-- The plan keeps the oldest (lowest-id) of each group so any historical
-- reference stays resolvable. Everything is soft-delete (set deleted_at),
-- so you can reverse with UPDATE ... SET deleted_at = NULL WHERE id IN (...)
-- if anything looks wrong.
--
-- Run wrapped in a transaction so you can BEGIN/verify/ROLLBACK or COMMIT:
--
--   psql "$DATABASE_URL" < scripts/cleanup_test_pollution.sql
--
-- Always review the row counts printed before committing in prod.
-- -----------------------------------------------------------------------------

BEGIN;

-- 1. Soft-delete duplicate "Regression Tests" roots (keep lowest id)
UPDATE sections SET deleted_at = NOW(), deleted_by = (
    SELECT id FROM users WHERE role = 'superadmin' AND tenant_id = 1 LIMIT 1
)
WHERE tenant_id = 1
  AND deleted_at IS NULL
  AND name = 'Regression Tests'
  AND parent_id IS NULL
  AND id > (
    SELECT MIN(id) FROM sections
    WHERE tenant_id = 1 AND deleted_at IS NULL
      AND name = 'Regression Tests' AND parent_id IS NULL
  );

-- 2. Soft-delete duplicate "Hardening Section" roots (keep lowest id)
UPDATE sections SET deleted_at = NOW(), deleted_by = (
    SELECT id FROM users WHERE role = 'superadmin' AND tenant_id = 1 LIMIT 1
)
WHERE tenant_id = 1
  AND deleted_at IS NULL
  AND name = 'Hardening Section'
  AND parent_id IS NULL
  AND id > (
    SELECT MIN(id) FROM sections
    WHERE tenant_id = 1 AND deleted_at IS NULL
      AND name = 'Hardening Section' AND parent_id IS NULL
  );

-- 3. Soft-delete all "Account Tests" whose parent is now soft-deleted
UPDATE sections SET deleted_at = NOW(), deleted_by = (
    SELECT id FROM users WHERE role = 'superadmin' AND tenant_id = 1 LIMIT 1
)
WHERE tenant_id = 1
  AND deleted_at IS NULL
  AND name = 'Account Tests'
  AND parent_id IN (
    SELECT id FROM sections
    WHERE tenant_id = 1 AND deleted_at IS NOT NULL
  );

-- 4. Soft-delete 11 of the 12 "Cleanup Test" TCs (keep highest id = most recent)
UPDATE test_cases SET deleted_at = NOW(), deleted_by = (
    SELECT id FROM users WHERE role = 'superadmin' AND tenant_id = 1 LIMIT 1
)
WHERE tenant_id = 1
  AND deleted_at IS NULL
  AND title = 'Cleanup Test'
  AND id < (
    SELECT MAX(id) FROM test_cases
    WHERE tenant_id = 1 AND deleted_at IS NULL AND title = 'Cleanup Test'
  );

-- 5. Soft-delete 1 of the 2 "Updated title" TCs (keep highest id)
UPDATE test_cases SET deleted_at = NOW(), deleted_by = (
    SELECT id FROM users WHERE role = 'superadmin' AND tenant_id = 1 LIMIT 1
)
WHERE tenant_id = 1
  AND deleted_at IS NULL
  AND title = 'Updated title'
  AND id < (
    SELECT MAX(id) FROM test_cases
    WHERE tenant_id = 1 AND deleted_at IS NULL AND title = 'Updated title'
  );

-- Verification: these counts should all be 1 (or 0 for Account Tests if all
-- parents were dupes, which is the case here)
SELECT 'Regression Tests roots remaining' as label,
       COUNT(*) FILTER (WHERE name='Regression Tests' AND parent_id IS NULL AND deleted_at IS NULL) as n
  FROM sections WHERE tenant_id = 1
UNION ALL SELECT 'Hardening Section roots remaining',
       COUNT(*) FILTER (WHERE name='Hardening Section' AND parent_id IS NULL AND deleted_at IS NULL)
  FROM sections WHERE tenant_id = 1
UNION ALL SELECT 'Account Tests remaining',
       COUNT(*) FILTER (WHERE name='Account Tests' AND deleted_at IS NULL)
  FROM sections WHERE tenant_id = 1
UNION ALL SELECT 'Cleanup Test TCs remaining',
       COUNT(*) FILTER (WHERE title='Cleanup Test' AND deleted_at IS NULL)
  FROM test_cases WHERE tenant_id = 1
UNION ALL SELECT 'Updated title TCs remaining',
       COUNT(*) FILTER (WHERE title='Updated title' AND deleted_at IS NULL)
  FROM test_cases WHERE tenant_id = 1;

-- Change COMMIT to ROLLBACK if the verification above looks wrong.
COMMIT;
