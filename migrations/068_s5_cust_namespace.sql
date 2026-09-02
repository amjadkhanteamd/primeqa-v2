-- 068: the PLM-CUST namespace CHECK widening — ONCE, before any custom
-- id is minted (LLD_PHASE5_AUTHORING §g, RULED 2026-09-01; D-471 era).
--
-- The 062 CHECK (`^PLM-[A-Z0-9]+-[0-9]{3}$`) admits any family at three
-- digits. The ruling narrows AND widens it in one act: exactly
-- PLM-A11Y- at three digits (the public catalogue as it exists) and
-- PLM-CUST- at five (99,999 custom rules per tenant; no second widening
-- ever needed, which is the point of ruling now). Custom rules live in
-- TENANT schemas (alembic 20260902_0010); this public CHECK exists so a
-- PLM-CUST id can never be minted into the public registry at the wrong
-- shape, and so the namespace split is enforced where the spine lives.
-- Idempotent.
BEGIN;

ALTER TABLE s5_rules DROP CONSTRAINT IF EXISTS s5_rules_id_shape;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 's5_rules_id_shape_v2') THEN
        ALTER TABLE s5_rules
            ADD CONSTRAINT s5_rules_id_shape_v2
            CHECK (rule_id ~ '^(PLM-A11Y-[0-9]{3}|PLM-CUST-[0-9]{5})$');
    END IF;
END $$;

COMMIT;
