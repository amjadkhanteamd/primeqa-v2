-- Migration 070: Step A — drop the two trust thresholds (DESTRUCTIVE).
-- (LLD_STEP_A_REPAIR_GATE ruling D2; dump-first per D-476 / D-285.)
--
-- Since Step A the apply paths read the proposal's gate_verdict, never
-- repair_proposals.confidence, so trust_threshold_high and
-- trust_threshold_medium are dead controls — and a dead safety control is
-- worse than none. Zero readers verified before the drop (the
-- agent_settings validation + the form were the only touches; both gone
-- in the same slice). The CHECK trust_bands_sane references both columns
-- and drops first.
--
-- ORM-window discipline: applied ONLY AFTER every service runs the Step A
-- code — the old ORM maps these columns and would fail every
-- tenant_agent_settings load (the LLM gateway reads that row per call)
-- if they vanished under it. The new ORM does not map them.
--
-- Idempotent (IF EXISTS). Safe to re-apply.

BEGIN;

ALTER TABLE tenant_agent_settings
    DROP CONSTRAINT IF EXISTS trust_bands_sane;

ALTER TABLE tenant_agent_settings
    DROP COLUMN IF EXISTS trust_threshold_high,
    DROP COLUMN IF EXISTS trust_threshold_medium;

COMMIT;
