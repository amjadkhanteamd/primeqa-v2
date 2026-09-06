-- Migration 069: Step A — the repair-proposal gate's settings surface
-- (LLD_STEP_A_REPAIR_GATE §c, §f; rulings D2 + dormant-first).
--
-- ADDITIVE:
--   repair_gate_apply_enabled (default FALSE) — the dormant-first switch.
--   While FALSE, NO apply path is reachable: the human route refuses and
--   the autonomous pass returns dormant, whatever the verdict. Switch-on is
--   its own gated act (superadmin, /settings/agent, audited).
--
-- DESTRUCTIVE (dump-first per D-476 / D-285 MIGRATE-FIRST):
--   trust_threshold_high + trust_threshold_medium are DROPPED. Since Step A
--   the apply paths read the VERDICT, never repair_proposals.confidence, so
--   both thresholds are dead controls — and a dead safety control is worse
--   than none (ruling D2). Zero readers verified before the drop
--   (agent_settings.py validation + the form were the only touches).
--   The CHECK trust_bands_sane references both columns and drops first.
--
-- Idempotent (IF EXISTS / IF NOT EXISTS). Safe to re-apply.

BEGIN;

ALTER TABLE tenant_agent_settings
    ADD COLUMN IF NOT EXISTS repair_gate_apply_enabled BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE tenant_agent_settings
    DROP CONSTRAINT IF EXISTS trust_bands_sane;

ALTER TABLE tenant_agent_settings
    DROP COLUMN IF EXISTS trust_threshold_high,
    DROP COLUMN IF EXISTS trust_threshold_medium;

COMMENT ON COLUMN tenant_agent_settings.repair_gate_apply_enabled IS
    'Step A dormant-first switch: while false, no repair proposal can be applied by anyone (human route refuses; auto pass dormant). Superadmin-only, audited.';

COMMIT;
