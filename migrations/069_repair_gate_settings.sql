-- Migration 069: Step A — the repair gate's dormant-first switch (ADDITIVE).
-- (LLD_STEP_A_REPAIR_GATE §c, §f.)
--
--   repair_gate_apply_enabled (default FALSE) — while FALSE, NO apply path
--   is reachable: the human route refuses and the autonomous pass returns
--   dormant, whatever the verdict. Switch-on is its own gated act
--   (superadmin, /settings/agent, audited).
--
-- ORM-window discipline (the merge runbook, 2026-09-07): this ADDITIVE
-- half is applied BEFORE the new code deploys (the old ORM does not map
-- the new column, so the running services are unaffected; the new ORM
-- finds it present). The DESTRUCTIVE half — dropping the two trust
-- thresholds the old ORM still maps — is migration 070, applied only
-- AFTER every service runs the new code. Neither direction of the deploy
-- ever selects a column that is not there.
--
-- Idempotent (IF NOT EXISTS). Safe to re-apply.

BEGIN;

ALTER TABLE tenant_agent_settings
    ADD COLUMN IF NOT EXISTS repair_gate_apply_enabled BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN tenant_agent_settings.repair_gate_apply_enabled IS
    'Step A dormant-first switch: while false, no repair proposal can be applied by anyone (human route refuses; auto pass dormant). Superadmin-only, audited.';

COMMIT;
