-- Migration 073: Step 5 — every decision records the policy, its version and
-- the plan it graded (LLD_STEP_5_QUALITY_POLICY §a, §b; ruling 6).
--
--   release_decisions.policy_id      the tenant policy row (tenant schema; no FK across schemas)
--   release_decisions.policy_version the version number, denormalised for the ledger
--   release_decisions.plan_id        the latest EXECUTED run plan for the scope, or NULL
--                                    ("evidence predates plans")
--
-- CLASSIFICATION (D-476 / D-285): ADDITIVE, three nullable columns, no data
-- write, no constraint that can reject a row — dumpless. Old rows stay NULL
-- ("decided before policies"). `releases.decision_criteria` is UNTOUCHED
-- (non-destructive): the column stays the legacy per-release criteria object;
-- its thresholds are superseded by the policy's rule parameters.
-- Idempotent (016+ convention).

ALTER TABLE release_decisions
    ADD COLUMN IF NOT EXISTS policy_id      uuid,
    ADD COLUMN IF NOT EXISTS policy_version integer,
    ADD COLUMN IF NOT EXISTS plan_id        uuid;
