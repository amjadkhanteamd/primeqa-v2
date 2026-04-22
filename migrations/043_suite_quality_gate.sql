-- Migration 043: test_suites.quality_gate_threshold.
--
-- Integer percent 0-100. The Release Owner Dashboard reads this: a
-- suite "passes its gate" when the most recent run's pass rate >=
-- threshold. NULL means no gate defined (skip for Go/No-Go purposes).
--
-- CHECK constraint keeps values sensible. Idempotent.

BEGIN;

ALTER TABLE test_suites
    ADD COLUMN IF NOT EXISTS quality_gate_threshold INTEGER;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'test_suites_quality_gate_range'
    ) THEN
        ALTER TABLE test_suites
            ADD CONSTRAINT test_suites_quality_gate_range
            CHECK (quality_gate_threshold IS NULL
                OR (quality_gate_threshold >= 0 AND quality_gate_threshold <= 100));
    END IF;
END$$;

COMMENT ON COLUMN test_suites.quality_gate_threshold IS
    'Percent (0-100). Release Owner Dashboard marks this suite NO-GO '
    'when its latest run pass rate is below this threshold. NULL = no '
    'gate defined.';

COMMIT;
