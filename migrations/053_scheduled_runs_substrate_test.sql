-- 053: D-199 trigger 2 — scheduled substrate re-verification.
-- scheduled_runs gains a nullable substrate_test_id (the S2 claim test_id, a
-- tenant-schema UUID — logical FK only, not DB-enforced across schemas);
-- suite_id relaxes to nullable so a schedule can target EITHER a v1 suite OR a
-- substrate claim, with a CHECK that at least one source is set. Idempotent.
BEGIN;

ALTER TABLE scheduled_runs ADD COLUMN IF NOT EXISTS substrate_test_id UUID;

ALTER TABLE scheduled_runs ALTER COLUMN suite_id DROP NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'scheduled_runs_one_source_ck'
    ) THEN
        ALTER TABLE scheduled_runs ADD CONSTRAINT scheduled_runs_one_source_ck
            CHECK (suite_id IS NOT NULL OR substrate_test_id IS NOT NULL);
    END IF;
END $$;

COMMIT;
